"""メイン画面の部品: カード、範囲の 1 行、キューの 1 行、下部のメッセージ表示、利用規約。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import time

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QKeySequence, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from ..core.queue import Job, JobState
from ..core.timeutil import TimeFormatError, format_short, format_time, parse_time
from . import art, theme
from .i18n import tr


class Card(QFrame):
    """手順ごとの区切り。デザインによって枠付きのカード、または横線だけになる（余白もそれに合わせる）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setSpacing(12)
        theme.notifier.changed.connect(self._restyle)
        self._restyle()

    def _restyle(self):
        d, _ = theme.current()
        if d.cards:
            self.lay.setContentsMargins(18, 16, 18, 16)
        else:
            self.lay.setContentsMargins(4, 20, 4, 18)


def card() -> tuple[QFrame, QVBoxLayout]:
    f = Card()
    return f, f.lay


class StepNumber(QWidget):
    """①②③ の番号。デザインごとに「大きな数字」「塗りつぶした丸」「01 02 03」で描く。"""

    def __init__(self, n: int, parent=None):
        super().__init__(parent)
        self.n = n
        theme.notifier.changed.connect(self._restyle)
        self._restyle()

    def _restyle(self):
        d, _ = theme.current()
        self.setFixedSize({"number": QSize(26, 40), "circle": QSize(40, 40), "padded": QSize(56, 44)}[d.step])
        self.update()

    def paintEvent(self, _):
        d, c = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(theme.NUM_FONT)
        r = QRectF(self.rect())
        if d.step == "circle":
            g = QLinearGradient(0, 0, r.width(), r.height())
            g.setColorAt(0, QColor(c.accent_hi))
            g.setColorAt(1, QColor(c.accent))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
            f.setPixelSize(20)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.n))
        else:
            padded = d.step == "padded"
            f.setPixelSize(40 if padded else 30)
            f.setWeight(QFont.Weight.Light if padded else QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(QColor(c.accent))
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{self.n:02d}" if padded else str(self.n))
        p.end()


def step_header(n: int | None, text: str, hint: str = "") -> QWidget:
    """手順の見出し: 大きな番号 ＋ 太字の見出し ＋ 小さな説明。"""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    if n is not None:
        h.addWidget(StepNumber(n))
    v = QVBoxLayout()
    v.setSpacing(0)
    title = QLabel(text)
    title.setObjectName("steptitle")
    v.addStretch(1)
    v.addWidget(title)
    if hint:
        sub = QLabel(hint)
        sub.setObjectName("hint")
        sub.setWordWrap(True)
        v.addWidget(sub)
    v.addStretch(1)
    h.addLayout(v, 1)
    return w


class DropArea(QFrame):
    """URL が空のときの大きな「ここに URL を貼り付け」欄。Ctrl+V・ドラッグ＆ドロップ・ボタンで貼り付けられる。"""

    pasted = Signal(str)
    typeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("drop")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 24, 20, 24)
        v.setSpacing(6)
        title = QLabel(tr("drop.title"))
        title.setObjectName("droptitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(tr("drop.hint"))
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        self.paste_btn = QPushButton(tr("drop.paste"))
        self.paste_btn.setObjectName("paste")
        self.paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paste_btn.clicked.connect(self._paste_clipboard)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.paste_btn)
        row.addStretch(1)
        v.addWidget(title)
        v.addWidget(hint)
        v.addSpacing(8)
        v.addLayout(row)

    def _paste_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if text:
            self.pasted.emit(text)
        else:
            self.typeRequested.emit()   # クリップボードが空なら、入力欄を出して打ち込んでもらう

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.StandardKey.Paste):
            self._paste_clipboard()
        elif ev.text().strip():   # 文字を打ち始めたら入力欄に切り替える
            self.typeRequested.emit()
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        self.typeRequested.emit()
        super().mousePressEvent(ev)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasText() or ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        md = ev.mimeData()
        text = md.text() if md.hasText() else "\n".join(u.toString() for u in md.urls())
        if text.strip():
            self.pasted.emit(text.strip())
            ev.acceptProposedAction()


def sub_label(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("sub")
    return lab


class ElidedLabel(QLabel):
    """長いタイトルを「…」で省略する。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt の名前に合わせる)
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._elide()

    def _elide(self):
        super().setText(QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight,
                                                             max(10, self.width())))


class TimeEdit(QLineEdit):
    """hh:mm:ss の入力欄。確定時に値を検証し、正しくなければ赤枠にして元に戻す。"""

    committed = Signal(float)

    def __init__(self, seconds: float = 0.0, parent=None):
        super().__init__(parent)
        self.setObjectName("time")
        self.setFixedWidth(100)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value = seconds
        self.set_seconds(seconds)
        self.editingFinished.connect(self._commit)
        self.textEdited.connect(lambda _: self._mark(False))

    def seconds(self) -> float:
        return self._value

    def set_seconds(self, s: float) -> None:
        self._value = s
        millis = abs(s - round(s)) > 1e-3
        self.setText(format_time(s, millis=millis))
        self._mark(False)

    def _mark(self, invalid: bool):
        self.setProperty("invalid", invalid)
        self.style().unpolish(self)
        self.style().polish(self)

    def _commit(self):
        try:
            v = parse_time(self.text())
        except TimeFormatError:
            self._mark(True)
            self.setToolTip(tr("err.time"))
            return
        self.setToolTip("")
        if abs(v - self._value) > 1e-6:
            self._value = v
            self.committed.emit(v)
        self.set_seconds(self._value)


class RangeRow(QFrame):
    changed = Signal(object)        # self
    removed = Signal(object)
    selected = Signal(object)

    def __init__(self, start: float, end: float, parent=None):
        super().__init__(parent)
        self.setObjectName("range")
        self.setProperty("selected", False)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 6, 4)
        h.setSpacing(8)
        self.num = QLabel("1")
        self.num.setObjectName("idx")
        self.num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.start_edit, self.end_edit = TimeEdit(start), TimeEdit(end)
        self.dur = QLabel()
        self.dur.setObjectName("dur")
        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip(tr("range.remove"))
        rm.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(self.num)
        h.addWidget(self.start_edit)
        h.addWidget(sub_label("〜"))
        h.addWidget(self.end_edit)
        h.addSpacing(6)
        h.addWidget(self.dur)
        h.addStretch(1)
        h.addWidget(rm)
        for e in (self.start_edit, self.end_edit):
            e.committed.connect(lambda _v: self.changed.emit(self))
        self._update_duration()

    def mousePressEvent(self, ev):
        self.selected.emit(self)
        super().mousePressEvent(ev)

    def values(self) -> tuple[float, float]:
        return self.start_edit.seconds(), self.end_edit.seconds()

    def set_values(self, s: float, e: float) -> None:
        self.start_edit.set_seconds(s)
        self.end_edit.set_seconds(e)
        self._update_duration()

    def set_index(self, i: int) -> None:
        self.num.setText(str(i + 1))

    def set_highlight(self, on: bool) -> None:
        """選択中の行は、枠線や左の線と薄い色で控えめに示す（見た目はデザインごとにスタイルシートで）。"""
        if self.property("selected") == on:
            return
        self.setProperty("selected", on)
        for w in (self, self.num):
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()

    def _update_duration(self):
        s, e = self.values()
        self.dur.setText(format_short(e - s))


# ---------------------------------------------------------------------------- キュー


STAGE_KEYS = {"info": "q.info", "video": "q.video", "encode": "q.encode", "chat": "q.chat",
              "overlay": "q.overlay", "comments": "q.comments"}


class _EtaClock:
    """残り時間の見積もり（いまの段階の進み方から）。段階が変わる・戻るとやり直す。"""

    MIN_ELAPSED = 3.0
    MIN_PROGRESS = 0.02

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.key = None
        self.t0 = 0.0
        self.r0 = 0.0

    def update(self, key, ratio: float | None) -> float | None:
        now = self.clock()
        if ratio is None or key is None:
            self.key = None
            return None
        if key != self.key or ratio < self.r0:
            self.key, self.t0, self.r0 = key, now, ratio
            return None
        dt, dr = now - self.t0, ratio - self.r0
        if dt < self.MIN_ELAPSED or dr < self.MIN_PROGRESS:
            return None
        return max(0.0, dt / dr * (1.0 - ratio))


class QueueRow(QFrame):
    """キューの 1 件（カード）: サムネイル・タイトル・状態・太い進捗バー・％・残り時間・操作ボタン。"""

    pauseClicked = Signal(object)
    resumeClicked = Signal(object)
    cancelClicked = Signal(object)
    retryClicked = Signal(object)

    THUMB = QSize(112, 63)

    def __init__(self, job: Job, title: str, parent=None, thumb: QPixmap | None = None):
        super().__init__(parent)
        self.setObjectName("job")
        self.job = job
        self.title_text = title
        self._thumb_src = thumb
        self._eta = _EtaClock()
        g = QHBoxLayout(self)
        g.setContentsMargins(10, 10, 10, 10)
        g.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setFixedSize(self.THUMB)
        g.addWidget(self.thumb)
        left = QVBoxLayout()
        left.setSpacing(5)
        self.title = ElidedLabel(title)
        self.title.setObjectName("jobtitle")
        self.status = QLabel(tr("q.waiting"))
        self.status.setObjectName("jobstatus")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        left.addWidget(self.title)
        left.addWidget(self.status)
        left.addWidget(self.bar)
        g.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(0)
        self.pct = QLabel("")
        self.pct.setObjectName("pct")
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.remain = sub_label("")
        self.remain.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addStretch(1)
        right.addWidget(self.pct)
        right.addWidget(self.remain)
        right.addStretch(1)
        g.addLayout(right)
        self.btns = QHBoxLayout()
        self.btns.setSpacing(2)
        g.addLayout(self.btns)
        self.b_pause = self._btn("❚❚", tr("q.pause"), self.pauseClicked)
        self.b_resume = self._btn("▶", tr("q.resume"), self.resumeClicked)
        self.b_cancel = self._btn("✕", tr("q.cancel"), self.cancelClicked)
        self.b_open = self._btn("📁", tr("q.open"), None)
        self.b_open.clicked.connect(self._open_folder)
        self.b_retry = self._btn("↻", tr("q.retry"), self.retryClicked)
        theme.notifier.changed.connect(self._restyle)
        self._restyle()
        self.refresh()

    def _btn(self, text, tip, sig):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setFixedSize(30, 30)
        if sig is not None:
            b.clicked.connect(lambda: sig.emit(self.job))
        self.btns.addWidget(b)
        return b

    def set_thumbnail(self, pm: QPixmap | None) -> None:
        self._thumb_src = pm
        self._restyle()

    def _restyle(self):
        d, _ = theme.current()
        self.bar.setFixedHeight(d.track_h)
        w, h, rad = self.THUMB.width(), self.THUMB.height(), max(4, d.radius - 2)
        if self._thumb_src is not None and not self._thumb_src.isNull():
            self.thumb.setPixmap(art.rounded(self._thumb_src, w, h, rad))
        else:
            self.thumb.setPixmap(art.placeholder_thumb(w, h, self.job.spec.url, rad))

    def _set_state(self, state: str):
        if self.status.property("state") != state:
            self.status.setProperty("state", state)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def refresh(self) -> None:
        j = self.job
        st = j.state
        p = j.progress
        n_clips = len(j.spec.ranges)
        if p and p.stage == "info" and p.ratio == 1.0 and p.message:
            self.title_text = p.message
        clip = ""
        if p and p.clip_index and n_clips > 1:
            clip = " ・ " + tr("q.clip", i=p.clip_index, n=n_clips)
        self.title.setText(self.title_text)
        show = {"pause": False, "resume": False, "cancel": False, "open": False, "retry": False}
        self.bar.setVisible(True)
        self.remain.setText("")
        state = ""
        if st == JobState.RUNNING:
            text = tr(STAGE_KEYS.get(p.stage, "q.video")) if p else tr("q.waiting")
            if p and p.stage == "encode" and "エンコーダ" in (p.message or ""):
                text += f"（{p.message.split(':', 1)[-1].strip()}）"
            elif p and p.stage in ("chat", "encode") and p.message:
                text = p.message
            self.status.setText(text + clip)
            ratio = p.ratio if p else None
            self._set_progress(ratio)
            eta = self._eta.update((p.stage, p.clip_index) if p else None, ratio)
            # 計算できないあいだ（始まったばかり・段階が変わった直後）も空欄にしない
            self.remain.setText(tr("q.remain", t=format_short(eta)) if eta is not None else tr("q.remain.calc"))
            show.update(pause=True, cancel=True)
        elif st == JobState.WAITING:
            self.status.setText(tr("q.waiting"))
            self._set_progress(0)
            self.pct.setText("")
            show.update(pause=True, cancel=True)
        elif st == JobState.PAUSED:
            self.status.setText(tr("q.paused"))
            show.update(resume=True, cancel=True)
            self.pct.setText("")
        elif st == JobState.DONE:
            n = len(j.result.files) if j.result else 0
            self.status.setText("✓ " + tr("q.done", n=n))
            state = "done"
            self.bar.setVisible(False)
            self.pct.setText("")
            show.update(open=True)
        elif st == JobState.FAILED:
            self.status.setText(tr("q.failed") + (f" ・ {j.error.message()}" if j.error else ""))
            state = "failed"
            self.bar.setVisible(False)
            self.pct.setText("")
            show.update(retry=True)
        elif st == JobState.CANCELLED:
            self.status.setText(tr("q.cancelled"))
            self.bar.setVisible(False)
            self.pct.setText("")
            show.update(retry=True)
        self._set_state(state)
        for k, b in (("pause", self.b_pause), ("resume", self.b_resume), ("cancel", self.b_cancel),
                     ("open", self.b_open), ("retry", self.b_retry)):
            b.setVisible(show[k])

    def _set_progress(self, ratio: float | None):
        if ratio is None:
            self.bar.setRange(0, 0)   # 進み具合が分からないときは動く表示
            self.pct.setText("")
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(ratio * 1000))
            self.pct.setText(f"{ratio * 100:.0f}%")

    def _open_folder(self):
        files = self.job.result.files if self.job.result else []
        target = files[0] if files else Path(self.job.spec.download.output_dir)
        if os.name == "nt" and Path(target).is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            os.startfile(str(Path(target).parent if Path(target).is_file() else target))  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- 下部のメッセージ


class MessageBar(QFrame):
    """画面下部に 1 行で表示する。エラーは「詳しく」で原文を開く（ポップアップは使わない）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("errorbar")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 6, 16, 6)
        v.setSpacing(4)
        row = QHBoxLayout()
        self.icon = QLabel("⚠")
        self.text = QLabel()
        self.text.setObjectName("errtext")
        self.text.setWordWrap(False)
        self.detail_btn = QPushButton(tr("err.details"))
        self.detail_btn.setFlat(True)
        self.detail_btn.clicked.connect(self._toggle)
        close = QToolButton()
        close.setText("✕")
        close.clicked.connect(self.hide)
        row.addWidget(self.icon)
        row.addWidget(self.text, 1)
        row.addWidget(self.detail_btn)
        # 「更新する」など、メッセージに対する操作のボタン（必要なときだけ。複数並べられる）
        self._actions_row = QHBoxLayout()
        self._actions_row.setSpacing(6)
        self.action_buttons: list[QPushButton] = []
        row.addLayout(self._actions_row)
        row.addWidget(close)
        v.addLayout(row)
        self.detail = QPlainTextEdit()
        self.detail.setObjectName("errdetail")
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(110)
        self.detail.hide()
        v.addWidget(self.detail)
        self.hide()

    def show_error(self, message: str, raw: str = "") -> None:
        self._set_kind(True)
        self.icon.setText("⚠")
        self.text.setText(message)
        self.detail.setPlainText(raw)
        self.detail.hide()
        self.detail_btn.setText(tr("err.details"))
        self.detail_btn.setVisible(bool(raw))
        self._set_actions([])
        self.show()

    def show_info(self, message: str, action_text: str | None = None, action=None,
                  actions: list[tuple[str, object]] | None = None) -> None:
        self._set_kind(False)
        self.icon.setText("ℹ")
        self.text.setText(message)
        self.text.setToolTip(message)
        self.detail.hide()
        self.detail_btn.hide()
        acts = list(actions or [])
        if action_text and action:
            acts.insert(0, (action_text, action))
        self._set_actions(acts)
        self.show()

    @property
    def action_btn(self) -> QPushButton | None:
        """先頭のボタン（無ければ None）。"""
        return self.action_buttons[0] if self.action_buttons else None

    def _set_actions(self, acts) -> None:
        for b in self.action_buttons:
            self._actions_row.removeWidget(b)
            b.deleteLater()
        self.action_buttons = []
        for text, fn in acts:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: self._run_action(f))
            self._actions_row.addWidget(b)
            self.action_buttons.append(b)

    def _run_action(self, fn):
        self._set_actions([])   # 二度押しを防ぐ（操作の結果で次のメッセージを出す）
        fn()

    def _set_kind(self, error: bool):
        """エラーは赤み、お知らせは落ち着いた色（色はデザインごとにスタイルシートで決める）。"""
        for w in (self, self.text):
            w.setProperty("info", not error)
            w.style().unpolish(w)
            w.style().polish(w)

    def _toggle(self):
        vis = not self.detail.isVisible()
        self.detail.setVisible(vis)
        self.detail_btn.setText(tr("err.hide") if vis else tr("err.details"))


class TermsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("terms.title"))
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        body = QLabel(tr("terms.body"))
        body.setWordWrap(True)
        v.addWidget(body)
        bb = QDialogButtonBox()
        ok = bb.addButton(tr("terms.accept"), QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(tr("terms.decline"), QDialogButtonBox.ButtonRole.RejectRole)
        ok.setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
