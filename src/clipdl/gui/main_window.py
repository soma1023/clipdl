"""メイン画面（1 画面で完結）: 1. URL を貼る → 2. 範囲 → 3. ダウンロード。PLAN.md §5 を参照。"""
from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPushButton, QScrollArea, QToolButton, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..core.chat.overlay import estimate_size_mb, format_size
from ..core.cookies import SUPPORTED_BROWSERS, CookieOptionError, cookie_params
from ..core.downloader import DownloadOptions
from ..core.errors import UserError
from ..core.logging_setup import export_logs
from ..core.models import CfrMode, ChatFormat, ClipRange, CutMode, VideoInfo
from ..core.naming import TemplateError, validate_template
from ..core.chat.twitch_style import estimate_size_mb as twitch_estimate_mb
from ..core.pipeline import JobSpec, style_options
from ..core.presets import PRESETS
from ..core.queue import Job, JobState
from ..core.settings import Settings
from ..core.timeutil import format_short, format_time
from ..core.updater import check_updates
from ..core.updater import update as update_components
from ..core.ytdl import extract_info
from . import art, theme
from .i18n import language, tr
from .range_bar import MIN_DURATION, RangeBar
from .widgets import (DropArea, ElidedLabel, MessageBar, QueueRow, RangeRow, card, step_header, sub_label)
from .workers import QueueBridge, run_in_thread

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+?(?=https?://|\s|$)")
NEW_RANGE_LEN = 60.0


def split_urls(text: str) -> list[str]:
    """貼り付けた文字列から URL を取り出す（改行・空白で区切られていても、つながっていても分ける）。"""
    return [u.rstrip(",;") for u in URL_RE.findall(text or "")]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.info: VideoInfo | None = None
        self._info_url = ""
        self._fetch_seq = 0
        self._multi_urls: list[str] = []
        self.rows: list[RangeRow] = []
        self.queue_rows: dict[str, QueueRow] = {}
        self.bridge = QueueBridge(self)
        self.bridge.jobChanged.connect(self._on_job_changed)

        self.setWindowTitle(tr("app.title", version=__version__))
        self.setWindowIcon(art.window_icon())
        self.setMinimumWidth(760)
        self._thumb_src: QPixmap | None = None
        self._typing_url = False
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("body")
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(22, 4, 22, 18)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.msg = MessageBar()
        outer.addWidget(self.msg)
        self.setCentralWidget(central)

        self._build_header()
        self._build_url_card()
        self._build_range_card()
        self._build_settings_card()
        self._build_queue_card()
        self.body_lay.addStretch(1)
        theme.notifier.changed.connect(self._restyle)
        self._restyle()

        self._fetch_timer = QTimer(self, singleShot=True, interval=500)
        self._fetch_timer.timeout.connect(self._fetch_info)
        self._refresh_ranges()
        self._update_estimate()

    # ================================================================== 見出し
    def _build_header(self):
        hd = QFrame()
        hd.setObjectName("header")
        h = QHBoxLayout(hd)
        h.setContentsMargins(0, 10, 0, 6)
        h.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(art.app_icon(30))
        name = QLabel("clipdl")
        name.setObjectName("appname")
        beta = QLabel(tr("beta", version=__version__))
        beta.setObjectName("beta")
        h.addWidget(icon)
        h.addWidget(name)
        h.addWidget(beta)
        h.addStretch(1)
        self.body_lay.addWidget(hd)

    def _restyle(self):
        """デザインが変わったときに、余白や並び方を合わせる（色や形はスタイルシートで変わる）。"""
        d, _ = theme.current()
        self.body_lay.setSpacing(14 if d.cards else 0)
        self.queue_lay.setSpacing(8 if d.cards else 0)
        self._place_download_button(d.button_full)
        if self._thumb_src is not None:
            self._show_thumb()

    # ================================================================== 1. URL
    def _build_url_card(self):
        f, lay = card()
        lay.addWidget(step_header(1, tr("step.url"), tr("step.url.hint")))
        self.drop = DropArea()
        self.drop.pasted.connect(self._paste_url)
        self.drop.typeRequested.connect(self._start_typing_url)
        lay.addWidget(self.drop)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(tr("url.placeholder"))
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.textChanged.connect(self._on_url_changed)
        self.url_edit.installEventFilter(self)
        lay.addWidget(self.url_edit)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(176, 99)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta = QVBoxLayout()
        meta.setSpacing(4)
        self.title_label = ElidedLabel("")
        self.title_label.setObjectName("title")
        self.meta_label = ElidedLabel("")
        self.meta_label.setObjectName("meta")
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        self.chip_labels = []
        for _ in range(3):
            c = QLabel()
            c.setObjectName("chip")
            c.hide()
            self.chips.addWidget(c)
            self.chip_labels.append(c)
        self.chips.addStretch(1)
        meta.addStretch(1)
        meta.addWidget(self.title_label)
        meta.addWidget(self.meta_label)
        meta.addSpacing(2)
        meta.addLayout(self.chips)
        meta.addStretch(1)
        row.addWidget(self.thumb)
        row.addLayout(meta, 1)
        self.info_row = QWidget()
        self.info_row.setLayout(row)
        self.info_row.hide()
        lay.addWidget(self.info_row)
        self.body_lay.addWidget(f)
        self._update_url_mode()

    # URL が空のときは大きな貼り付け欄を出し、打ち込むときや貼ったあとは入力欄に切り替える
    def _update_url_mode(self):
        empty = not self.url_edit.text().strip()
        show_drop = empty and not self._typing_url
        self.drop.setVisible(show_drop)
        self.url_edit.setVisible(not show_drop)

    def _paste_url(self, text: str):
        self._typing_url = False
        self.url_edit.setText(text)
        self._update_url_mode()

    def _start_typing_url(self):
        self._typing_url = True
        self._update_url_mode()
        self.url_edit.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.url_edit and ev.type() == QEvent.Type.FocusOut and not self.url_edit.text().strip():
            self._typing_url = False   # 何も打たずに離れたら、貼り付け欄に戻す
            QTimer.singleShot(0, self._update_url_mode)
        return super().eventFilter(obj, ev)

    def _on_url_changed(self, text: str):
        urls = split_urls(text)
        self._multi_urls = urls if len(urls) > 1 else []
        self._show_confirm(len(urls) > 1)
        self.range_card.setEnabled(len(urls) <= 1)
        self.range_note.setVisible(len(urls) > 1)
        if len(urls) == 1 and urls[0] != self._info_url:
            self._fetch_timer.start()
        elif not urls:
            self._set_info(None)
        if not text.strip() and not self.url_edit.hasFocus():
            self._typing_url = False
        self._update_url_mode()
        self._update_download_button()

    def _fetch_info(self):
        urls = split_urls(self.url_edit.text())
        if len(urls) != 1:
            return
        url = urls[0]
        self._info_url = url
        self._fetch_seq += 1
        seq = self._fetch_seq
        self.info_row.show()
        self.title_label.setText(tr("info.fetching"))
        self.meta_label.setText(url)
        for c in self.chip_labels:
            c.hide()
        self._thumb_src = None
        self.thumb.clear()
        try:
            cookies = self._cookie_opts()
        except CookieOptionError as e:
            self.msg.show_error(str(e))
            return

        def done(info):
            if seq == self._fetch_seq:
                self._set_info(info)

        def failed(err: UserError):
            if seq == self._fetch_seq:
                self._set_info(None)
                self.msg.show_error(err.message(language()), err.raw)

        run_in_thread(lambda: extract_info(url, cookies), done, failed)

    def _set_info(self, info: VideoInfo | None):
        self.info = info
        if info is None:
            self.info_row.hide()
            self._info_url = ""
            self.range_bar.set_duration(0)
            self._update_estimate()
            return
        self.msg.hide()
        self.info_row.show()
        self.title_label.setText(info.title)
        dur = format_time(info.duration) if info.duration else "?"
        chat = tr("info.chat_yes") if (info.has_live_chat or info.site == "twitch") else tr("info.chat_no")
        date = f"{info.upload_date[:4]}/{info.upload_date[4:6]}/{info.upload_date[6:]}" if info.upload_date else ""
        site = {"youtube": "YouTube", "twitch": "Twitch"}.get(info.site, info.site.capitalize())
        self.meta_label.setText(" ・ ".join(x for x in (info.channel, site, date) if x))
        res = f"{info.height}p{int(info.fps) if info.fps and info.fps > 30 else ''}" if info.height else ""
        for c, text in zip(self.chip_labels, (dur, res, chat)):
            c.setText(text)
            c.setVisible(bool(text))
        self.range_bar.set_duration(info.duration)
        # 範囲を動画の長さに収める
        for r in self.rows:
            s, e = r.values()
            if info.duration and e > info.duration:
                r.set_values(min(s, max(0, info.duration - MIN_DURATION)), info.duration)
        self._refresh_ranges()
        self._update_estimate()
        url = _thumbnail_url(info)
        if url:
            run_in_thread(lambda: _download(url), self._set_thumb)

    def _set_thumb(self, data: bytes):
        pm = QPixmap()
        if data and pm.loadFromData(QByteArray(data)):
            self._thumb_src = pm
            self._show_thumb()

    def _show_thumb(self):
        d, _ = theme.current()
        self.thumb.setPixmap(art.rounded(self._thumb_src, self.thumb.width(), self.thumb.height(), d.radius))

    # ================================================================== 2. 範囲
    def _build_range_card(self):
        f, lay = card()
        self.range_card = f
        lay.addWidget(step_header(2, tr("step.range"), tr("step.range.hint")))
        self.range_bar = RangeBar()
        self.range_bar.empty_text = tr("range.empty")
        self.range_bar.rangeChanged.connect(self._on_bar_changed)
        self.range_bar.rangeSelected.connect(self._select_range)
        lay.addWidget(self.range_bar)
        self.rows_lay = QVBoxLayout()
        self.rows_lay.setSpacing(4)
        lay.addLayout(self.rows_lay)
        add = QPushButton(tr("range.add"))
        add.setObjectName("ghost")
        add.clicked.connect(self._add_range)
        h = QHBoxLayout()
        h.addWidget(add)
        h.addStretch(1)
        lay.addLayout(h)
        self.range_note = sub_label(tr("range.single_only"))
        self.range_note.hide()
        self.body_lay.addWidget(f)
        self.body_lay.addWidget(self.range_note)

    def _add_range(self):
        dur = self.info.duration if self.info and self.info.duration else None
        start = self.rows[-1].values()[1] if self.rows else 0.0
        if dur:
            start = min(start, max(0.0, dur - MIN_DURATION))
        end = start + NEW_RANGE_LEN
        if dur:
            end = min(end, dur)
        row = RangeRow(start, end)
        row.changed.connect(self._on_row_changed)
        row.removed.connect(self._remove_range)
        row.selected.connect(lambda r: self._select_range(self.rows.index(r)))
        self.rows.append(row)
        self.rows_lay.addWidget(row)
        self._refresh_ranges(selected=len(self.rows) - 1)

    def _remove_range(self, row: RangeRow):
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._refresh_ranges()

    def _on_row_changed(self, row: RangeRow):
        s, e = row.values()
        dur = self.info.duration if self.info and self.info.duration else None
        if dur:
            e = min(e, dur)
        if e - s < MIN_DURATION:  # 入力が逆転したら、触った側に合わせて直す
            e = s + MIN_DURATION
        row.set_values(s, e)
        self._refresh_ranges(selected=self.rows.index(row))

    def _on_bar_changed(self, i: int, s: float, e: float):
        if 0 <= i < len(self.rows):
            self.rows[i].set_values(s, e)
            self._select_range(i)
            self._update_estimate()

    def _select_range(self, i: int):
        for k, r in enumerate(self.rows):
            r.set_highlight(k == i)
        if self.range_bar.selected != i:
            self.range_bar.selected = i
            self.range_bar.update()

    def _refresh_ranges(self, selected: int | None = None):
        for i, r in enumerate(self.rows):
            r.set_index(i)
        sel = selected if selected is not None else min(self.range_bar.selected, len(self.rows) - 1)
        self.range_bar.set_ranges([r.values() for r in self.rows], sel)
        self._select_range(sel)
        self._update_estimate()
        self._update_download_button()

    # ================================================================== 3. 設定とダウンロード
    def _build_settings_card(self):
        s = self.settings
        f, lay = card()
        lay.addWidget(step_header(3, tr("step.save"), tr("step.save.hint")))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.preset = QComboBox()
        for key, p in PRESETS.items():
            self.preset.addItem(p.name_en if language() == "en" else p.name_ja, key)
        self.preset.setCurrentIndex(max(0, self.preset.findData(s.preset)))
        self.preset.currentIndexChanged.connect(self._settings_changed)
        row.addWidget(self.preset)
        self.save_chat = QCheckBox(tr("save_chat"))
        self.save_chat.setChecked(s.save_chat)
        self.save_chat.toggled.connect(self._settings_changed)
        row.addWidget(self.save_chat)
        row.addStretch(1)
        grid.addWidget(sub_label(tr("quality")), 0, 0)
        grid.addLayout(row, 0, 1)
        row = QHBoxLayout()
        self.out_edit = QLineEdit(s.output_dir)
        self.out_edit.editingFinished.connect(self._settings_changed)
        browse = QPushButton(tr("browse"))
        browse.clicked.connect(self._browse_dir)
        row.addWidget(self.out_edit, 1)
        row.addWidget(browse)
        grid.addWidget(sub_label(tr("output_dir")), 1, 0)
        grid.addLayout(row, 1, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        row = QHBoxLayout()
        self.adv_toggle = QToolButton()
        self.adv_toggle.setObjectName("adv")
        self.adv_toggle.setText(tr("advanced"))
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.adv_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.adv_toggle.setAutoRaise(True)
        self.adv_toggle.toggled.connect(self._toggle_advanced)
        row.addWidget(self.adv_toggle)
        row.addStretch(1)
        self.dl_btn = QPushButton(tr("step.download"))
        self.dl_btn.setObjectName("primary")
        self.dl_btn.setDefault(True)
        self.dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dl_btn.clicked.connect(self._download)
        self._dl_row = row
        lay.addLayout(row)
        self._dl_full_slot = QVBoxLayout()   # デザインによっては、ボタンを横幅いっぱいにしてここへ置く
        lay.addLayout(self._dl_full_slot)
        self.adv = self._build_advanced()
        self.adv.hide()
        lay.addWidget(self.adv)
        self.body_lay.addWidget(f)

    def _build_advanced(self) -> QWidget:
        s = self.settings
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 8, 0, 0)
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(6)

        def combo(items, current):
            c = QComboBox()
            for label, data in items:
                c.addItem(label, data)
            c.setCurrentIndex(max(0, c.findData(current)))
            c.currentIndexChanged.connect(self._settings_changed)
            return c

        self.cut = combo([(tr("adv.cut.fast"), "fast"), (tr("adv.cut.precise"), "precise")], s.cut_mode)
        self.cfr = combo([(tr("adv.cfr.auto"), "auto"), (tr("adv.cfr.always"), "always"),
                          (tr("adv.cfr.off"), "off")], s.cfr_mode)
        g.addWidget(sub_label(tr("adv.cut")), 0, 0)
        g.addWidget(sub_label(tr("adv.cfr")), 0, 1)
        g.addWidget(self.cut, 1, 0)
        g.addWidget(self.cfr, 1, 1)
        g.addWidget(sub_label(tr("adv.chat_formats")), 2, 0, 1, 2)
        fr = QHBoxLayout()
        fr.setSpacing(18)
        self.fmt = {}
        for key, label in (("json", "JSON"), ("csv", "CSV"), ("ass", "ASS"), ("mov", tr("adv.overlay"))):
            cb = QCheckBox(label)
            cb.setChecked(key in s.chat_formats)
            cb.toggled.connect(self._settings_changed)
            self.fmt[key] = cb
            fr.addWidget(cb)
        self.estimate = sub_label("")
        fr.addWidget(self.estimate)
        fr.addStretch(1)
        g.addLayout(fr, 3, 0, 1, 2)
        self.chat_style = combo([(tr("style.twitch"), "twitch"), (tr("style.vertical"), "vertical"),
                                 (tr("style.scroll"), "scroll")], s.chat_style)
        self.template = QLineEdit(s.filename_template)
        self.template.editingFinished.connect(self._settings_changed)
        g.addWidget(sub_label(tr("adv.chat_style")), 4, 0)
        g.addWidget(sub_label(tr("adv.template")), 4, 1)
        g.addWidget(self.chat_style, 5, 0)
        g.addWidget(self.template, 5, 1)
        items = [(tr("adv.cookies.none"), "")] + [(b.capitalize(), b) for b in SUPPORTED_BROWSERS
                                                   if b != "safari"]
        items.append((tr("adv.cookies.file"), "__file__"))
        self.cookies = combo(items, "__file__" if s.cookies_file else s.cookies_from_browser)
        if s.cookies_file:
            self.cookies.setItemText(self.cookies.count() - 1, f"cookies.txt: {Path(s.cookies_file).name}")
        self.cookies.activated.connect(self._cookie_activated)
        g.addWidget(sub_label(tr("adv.cookies")), 6, 0)
        g.addWidget(sub_label(tr("adv.language")), 6, 1)
        g.addWidget(self.cookies, 7, 0)
        lr = QHBoxLayout()
        self.lang = combo([("日本語", "ja"), ("English", "en")], s.language)
        self.lang.currentIndexChanged.connect(lambda _: self.msg.show_info(tr("adv.language.restart")))
        logs = QPushButton(tr("adv.export_logs"))
        logs.clicked.connect(self._export_logs)
        upd = QPushButton(tr("adv.check_update"))
        upd.clicked.connect(self._check_update)
        lr.addWidget(self.lang)
        lr.addWidget(logs)
        lr.addWidget(upd)
        lr.addStretch(1)
        g.addLayout(lr, 7, 1)
        self.app_update_cb = QCheckBox(tr("adv.app_update"))
        self.app_update_cb.setChecked(s.check_app_update)
        self.app_update_cb.toggled.connect(self._settings_changed)
        g.addWidget(self.app_update_cb, 8, 1)
        self.design = QComboBox()
        for key in theme.DESIGNS:
            self.design.addItem(tr(f"design.{key}"), key)
        self.design.setCurrentIndex(max(0, self.design.findData(s.ui_design)))
        self.design.currentIndexChanged.connect(self._apply_look)
        self.mode = QComboBox()
        for key in theme.MODES:
            self.mode.addItem(tr(f"mode.{key}"), key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.ui_mode)))
        self.mode.currentIndexChanged.connect(self._apply_look)
        g.addWidget(sub_label(tr("adv.design")), 9, 0)
        g.addWidget(sub_label(tr("adv.mode")), 9, 1)
        g.addWidget(self.design, 10, 0)
        g.addWidget(self.mode, 10, 1)
        return w

    def _place_download_button(self, full: bool):
        self._dl_row.removeWidget(self.dl_btn)
        self._dl_full_slot.removeWidget(self.dl_btn)
        if full:
            self._dl_full_slot.addWidget(self.dl_btn)
        else:
            self._dl_row.addWidget(self.dl_btn)

    def _update_download_button(self):
        """ボタンの文言を、範囲の数に合わせる。URL が無いときは押せない。"""
        if not hasattr(self, "dl_btn"):
            return
        urls = split_urls(self.url_edit.text())
        self.dl_btn.setEnabled(bool(urls))
        n = len(self.rows)
        if len(urls) > 1 or not urls:
            text = tr("step.download")
        elif n == 0:
            text = tr("dl.whole")
        elif n == 1:
            text = tr("step.download")
        else:
            text = tr("dl.n", n=n)
        self.dl_btn.setText(text)

    def _apply_look(self, *_):
        """デザインと明るさをその場で切り替える。"""
        self._settings_changed()
        theme.apply(QApplication.instance(), self.settings.ui_design, self.settings.ui_mode)

    def _toggle_advanced(self, on: bool):
        self.adv.setVisible(on)
        self.adv_toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, tr("output_dir"), self.out_edit.text())
        if d:
            self.out_edit.setText(str(Path(d)))
            self._settings_changed()

    def _cookie_activated(self, idx: int):
        if self.cookies.itemData(idx) == "__file__":
            path, _ = QFileDialog.getOpenFileName(self, "cookies.txt", "", "cookies.txt (*.txt);;* (*)")
            if path:
                self.settings.cookies_file = path
                self.cookies.setItemText(idx, f"cookies.txt: {Path(path).name}")
            else:
                self.cookies.setCurrentIndex(0)
        self._settings_changed()

    def _settings_changed(self, *_):
        s = self.settings
        s.preset = self.preset.currentData()
        s.save_chat = self.save_chat.isChecked()
        s.output_dir = self.out_edit.text().strip() or s.output_dir
        s.cut_mode = self.cut.currentData()
        s.cfr_mode = self.cfr.currentData()
        s.chat_formats = [k for k, cb in self.fmt.items() if cb.isChecked()]
        s.chat_style = self.chat_style.currentData()
        s.ass_style = "scroll" if s.chat_style == "scroll" else "vertical"
        s.filename_template = self.template.text().strip() or s.filename_template
        ck = self.cookies.currentData()
        s.cookies_from_browser = "" if ck in ("", "__file__") else ck
        if ck != "__file__":
            s.cookies_file = ""
        s.language = self.lang.currentData()
        s.check_app_update = self.app_update_cb.isChecked()
        if hasattr(self, "design"):
            s.ui_design = self.design.currentData()
            s.ui_mode = self.mode.currentData()
        s.save()
        self._update_estimate()

    def _update_estimate(self):
        """オーバーレイ動画のチェックの横に、予想サイズを 1 行で出す（ポップアップは使わない）。"""
        if not hasattr(self, "estimate"):
            return
        on = self.fmt["mov"].isChecked() and self.save_chat.isChecked()
        self.estimate.setVisible(on)
        if not on:
            return
        ranges = [r.values() for r in self.rows] if not self._multi_urls else []
        if ranges:
            total = sum(e - s for s, e in ranges)
            detail = f"{len(ranges)} 範囲・{format_short(total)}" if language() == "ja" \
                else f"{len(ranges)} ranges, {format_short(total)}"
        elif self.info and self.info.duration:
            total = self.info.duration
            detail = ("動画全体・" if language() == "ja" else "whole video, ") + format_time(total)
        else:
            self.estimate.setText("")
            return
        height = min(self.info.height or 1080, 1080) if self.info and self.preset.currentData() == "edit1080" \
            else (self.info.height if self.info and self.info.height else 1080)
        st = style_options(self.settings)
        if st["chat_style"] == "twitch":
            mb = twitch_estimate_mb(total, height, st["twitch_style"], st["overlay"])
        else:
            mb = estimate_size_mb(total, height, options=st["overlay"])
        self.estimate.setText(tr("adv.overlay.estimate", size=format_size(mb), detail=detail))


    def _cookie_opts(self) -> dict:
        return cookie_params(self.settings.cookies_from_browser, self.settings.cookies_file)

    def _make_spec(self, url: str, ranges: list[ClipRange], info: VideoInfo | None) -> JobSpec:
        s = self.settings
        validate_template(s.filename_template)
        opts = DownloadOptions(output_dir=Path(s.output_dir).expanduser(), preset=s.preset,
                               cut_mode=CutMode(s.cut_mode), cfr_mode=CfrMode(s.cfr_mode),
                               template=s.filename_template, cookie_opts=self._cookie_opts())
        return JobSpec(url=url, ranges=ranges, download=opts, save_chat=s.save_chat,
                       chat_formats=tuple(ChatFormat(f) for f in s.chat_formats), info=info,
                       **style_options(s))

    def _download(self):
        self._settings_changed()
        urls = split_urls(self.url_edit.text())
        if not urls:
            self.msg.show_error(tr("err.no_url"))
            return
        if len(urls) > 1:
            self._show_confirm(True)  # 複数のときは確認行で「追加」を押してもらう
            return
        ranges = [ClipRange(s, e, i + 1) for i, (s, e) in enumerate(r.values() for r in self.rows)]
        info = self.info if self.info and self._info_url == urls[0] else None
        try:
            spec = self._make_spec(urls[0], ranges, info)
        except (TemplateError, CookieOptionError) as e:
            self.msg.show_error(str(e))
            return
        self._enqueue(spec, info.title if info else urls[0])

    # ================================================================== キュー
    def _build_queue_card(self):
        f, lay = card()
        qh = QHBoxLayout()
        qt = QLabel(tr("queue"))
        qt.setObjectName("steptitle")
        self.queue_count = QLabel()
        self.queue_count.setObjectName("chip")
        self.queue_count.hide()
        qh.addWidget(qt)
        qh.addWidget(self.queue_count)
        qh.addStretch(1)
        lay.addLayout(qh)
        self.queue_lay = QVBoxLayout()
        lay.addLayout(self.queue_lay)
        self.queue_empty = sub_label(tr("q.empty"))
        self.queue_lay.addWidget(self.queue_empty)
        # 複数 URL の確認行（モックどおりキューの下）
        self.confirm = QFrame()
        self.confirm.setObjectName("confirm")
        h = QHBoxLayout(self.confirm)
        h.setContentsMargins(8, 4, 8, 4)
        self.confirm_text = QLabel()
        add = QPushButton(tr("multi.add"))
        add.clicked.connect(self._add_multi)
        no = QPushButton(tr("multi.cancel"))
        no.clicked.connect(lambda: self._show_confirm(False))
        h.addWidget(self.confirm_text, 1)
        h.addWidget(add)
        h.addWidget(no)
        self.confirm.hide()
        lay.addWidget(self.confirm)
        self.body_lay.addWidget(f)

    def _show_confirm(self, on: bool):
        n = len(self._multi_urls)
        self.confirm_text.setText(tr("multi.confirm", n=n))
        self.confirm.setVisible(on and n > 1)
        self._update_estimate()

    def _add_multi(self):
        self._settings_changed()
        try:
            specs = [self._make_spec(u, [], None) for u in self._multi_urls]
        except (TemplateError, CookieOptionError) as e:
            self.msg.show_error(str(e))
            return
        for sp in specs:
            self._enqueue(sp, sp.url)
        self._show_confirm(False)
        self.url_edit.clear()

    def _enqueue(self, spec: JobSpec, title: str):
        job = self.bridge.add(spec)
        thumb = self._thumb_src if (self.info and spec.url == self._info_url) else None
        row = QueueRow(job, title, thumb=thumb)
        row.pauseClicked.connect(self.bridge.pause)
        row.resumeClicked.connect(self.bridge.resume)
        row.cancelClicked.connect(self.bridge.cancel)
        row.retryClicked.connect(self.bridge.resume)
        self.queue_rows[job.id] = row
        self.queue_empty.hide()
        self.queue_lay.insertWidget(0, row)  # 新しいものを上に
        self.queue_count.setText(tr("q.count", n=len(self.queue_rows)))
        self.queue_count.show()
        row.refresh()

    def _on_job_changed(self, job: Job):
        row = self.queue_rows.get(job.id)
        if row is None:
            return
        row.refresh()
        if job.state == JobState.FAILED and job.error:
            self.msg.show_error(job.error.message(language()), job.error.raw)
        elif job.state == JobState.DONE and job.result and job.result.warnings:
            self.msg.show_info(" / ".join(job.result.warnings))

    # ================================================================== サポート
    def _export_logs(self):
        d = QFileDialog.getExistingDirectory(self, tr("adv.export_logs"), str(Path.home() / "Desktop"))
        if d:
            out = export_logs(Path(d))
            self.msg.show_info(tr("logs.saved", path=out))

    def _check_update(self):
        self.msg.show_info(tr("adv.update.checking"))

        def done(res):
            text = " / ".join(s.summary() for s in res)
            if any(s.outdated and s.updatable for s in res):
                self.msg.show_info(text, tr("adv.update.apply"), self._apply_update)
            else:
                self.msg.show_info(text)

        run_in_thread(check_updates, done, lambda err: self.msg.show_error(err.message(language()), err.raw))

    def _apply_update(self):
        self.msg.show_info(tr("adv.update.applying"))
        run_in_thread(lambda: update_components(), lambda res: self.msg.show_info(
            tr("adv.update.done", version=res.ytdlp_version)),
            lambda err: self.msg.show_error(err.message(language()), err.raw))

    def show_startup_notices(self, after_update=None):
        """起動時のお知らせ。優先順: 更新の結果 → 更新版を使えなかった警告 → 準備済みの新しい版 → 新しい版の確認。"""
        from ..core import selfupdate
        from ..core.bootstrap import REJECTED
        if after_update is not None:
            if after_update.status == "ok":
                self.msg.show_info(after_update.message)
            else:
                self.msg.show_error(tr("selfupdate.rolled_back"), after_update.message)
        elif REJECTED:
            name, reason = next(iter(REJECTED.items()))
            self.msg.show_error(tr("adv.update.rejected", name=name), reason)
        else:
            try:
                prepared = selfupdate.find_prepared()
            except Exception:  # noqa: BLE001
                prepared = None
            if prepared is not None:
                self._show_update_ready(prepared)
                return
        self._check_app_update()

    def _check_app_update(self):
        """clipdl の新しい版があれば下部に知らせる（1 日 1 回まで。失敗しても何も出さない）。"""
        import copy

        from ..core import app_update
        if not self.settings.check_app_update:
            return
        snap = copy.copy(self.settings)   # 別スレッドでは写しだけを書き換える

        def done(release):
            s = self.settings
            s.app_update_checked_at = snap.app_update_checked_at
            s.app_update_latest = snap.app_update_latest
            s.app_update_url = snap.app_update_url
            s.save()
            # 他のメッセージ（更新版を使えなかった警告など）が出ているときは、そちらを優先する
            if release and not self.msg.isVisible():
                self._show_update_available(release)

        run_in_thread(lambda: app_update.check_on_startup(snap), done, lambda *_: None)

    # ------------------------------------------------------------------ ワンクリック更新
    def _open_release(self, release):
        QDesktopServices.openUrl(QUrl(release.url))

    def _show_update_available(self, release):
        from ..core import selfupdate
        actions = []
        if selfupdate.unsupported_reason() is None:
            actions.append((tr("selfupdate.update"), lambda: self._start_self_update(release)))
        actions.append((tr("app_update.open"), lambda: self._open_release(release)))
        self.msg.show_info(tr("app_update.available", version=release.tag), actions=actions)

    def _start_self_update(self, release):
        """新しい版をダウンロード・検証して、アプリの隣に展開する（別スレッド）。"""
        from ..core import selfupdate
        from .workers import ProgressRelay
        relay = ProgressRelay(self)
        labels = {"verify": tr("selfupdate.verifying"), "download": tr("selfupdate.downloading"),
                  "extract": tr("selfupdate.extracting")}

        def on_progress(stage, ratio):
            text = labels.get(stage, stage)
            if ratio is not None:
                text += f"  {ratio * 100:.0f}%"
            self.msg.show_info(text)

        relay.progress.connect(on_progress)
        self.msg.show_info(labels["verify"])

        def work():
            try:
                return selfupdate.prepare(release.tag, progress=relay.emit_throttled)
            except selfupdate.SelfUpdateError as e:
                return e
            except Exception as e:  # noqa: BLE001 — 通信エラーなども「ページを開く」案内に戻す
                logging.getLogger(__name__).exception("自動更新の準備に失敗")
                return selfupdate.SelfUpdateError(f"{type(e).__name__}: {e}")

        def done(res):
            relay.deleteLater()
            if isinstance(res, selfupdate.SelfUpdateError):
                logging.getLogger(__name__).warning("自動更新できませんでした: %s", res)
                self.msg.show_info(tr("selfupdate.failed", reason=str(res)),
                                   actions=[(tr("app_update.open"), lambda: self._open_release(release))])
            else:
                self._show_update_ready(res)

        run_in_thread(work, done, lambda err: done(selfupdate.SelfUpdateError(err.message(language()))))

    def _show_update_ready(self, prepared):
        self.msg.show_info(tr("selfupdate.ready", version=prepared.version),
                           actions=[(tr("selfupdate.restart"), lambda: self._restart_to_update(prepared)),
                                    (tr("selfupdate.later"), self.msg.hide)])

    def _restart_to_update(self, prepared):
        from ..core import selfupdate
        busy = any(r.job.state in (JobState.WAITING, JobState.RUNNING) for r in self.queue_rows.values())
        if busy:
            self.msg.show_info(tr("selfupdate.busy"),
                               actions=[(tr("selfupdate.restart"), lambda: self._restart_to_update(prepared)),
                                        (tr("selfupdate.later"), self.msg.hide)])
            return
        try:
            selfupdate.start_swap(prepared)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).exception("入れ替え用プログラムを起動できませんでした")
            self.msg.show_error(tr("selfupdate.failed", reason=str(e)), str(e))
            return
        self.close()
        QApplication.instance().quit()

    # ================================================================== 終了
    def closeEvent(self, ev):
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        self.bridge.shutdown()
        super().closeEvent(ev)

    def restore_geometry(self):
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
        else:
            self.resize(880, 940)


def _thumbnail_url(info: VideoInfo) -> str | None:
    """Qt が読める JPEG / PNG のサムネイルを選ぶ（webp は環境によって読めないため後回し）。"""
    thumbs = (info.raw or {}).get("thumbnails") or []
    cands = [t for t in thumbs if t.get("url") and not str(t["url"]).split("?")[0].endswith(".webp")]
    cands.sort(key=lambda t: abs((t.get("width") or 480) - 480))
    if cands:
        return cands[0]["url"]
    if info.site == "youtube":
        return f"https://i.ytimg.com/vi/{info.id}/hqdefault.jpg"
    return info.thumbnail


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()
