"""メイン画面の部品: カード、範囲の 1 行、キューの 1 行、下部のメッセージ表示、利用規約。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QProgressBar, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..core.queue import Job, JobState
from ..core.timeutil import TimeFormatError, format_short, format_time, parse_time
from . import theme
from .i18n import tr


def card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    return f, lay


def step_header(n: int | None, text: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    if n is not None:
        s = QLabel(str(n))
        s.setObjectName("step")
        h.addWidget(s)
    lab = QLabel(text)
    lab.setObjectName("sub")
    h.addWidget(lab, 1)
    return w


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
        self.setFixedWidth(96)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.font()
        f.setFamily("Consolas")
        self.setFont(f)
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
        self.setAutoFillBackground(True)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 2, 6, 2)
        self.num = QLabel("1")
        self.num.setObjectName("sub")
        self.num.setFixedWidth(18)
        self.num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.start_edit, self.end_edit = TimeEdit(start), TimeEdit(end)
        self.dur = sub_label()
        rm = QToolButton()
        rm.setText("✕")
        rm.setToolTip(tr("range.remove"))
        rm.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(self.num)
        h.addWidget(self.start_edit)
        h.addWidget(QLabel("〜"))
        h.addWidget(self.end_edit)
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
        self.setBackgroundRole(self.backgroundRole())
        self.setStyleSheet("RangeRow { background: palette(highlight); border-radius: 4px; }"
                           "RangeRow QLabel { color: palette(highlighted-text); }" if on else "")

    def _update_duration(self):
        s, e = self.values()
        self.dur.setText(format_short(e - s))


# ---------------------------------------------------------------------------- キュー


STAGE_KEYS = {"info": "q.info", "video": "q.video", "encode": "q.encode", "chat": "q.chat",
              "overlay": "q.overlay", "comments": "q.comments"}


class QueueRow(QFrame):
    pauseClicked = Signal(object)
    resumeClicked = Signal(object)
    cancelClicked = Signal(object)
    retryClicked = Signal(object)

    def __init__(self, job: Job, title: str, parent=None):
        super().__init__(parent)
        self.job = job
        self.title_text = title
        g = QHBoxLayout(self)
        g.setContentsMargins(0, 6, 0, 6)
        left = QVBoxLayout()
        left.setSpacing(1)
        self.title = ElidedLabel(title)
        self.status = sub_label(tr("q.waiting"))
        left.addWidget(self.title)
        left.addWidget(self.status)
        g.addLayout(left, 1)
        mid = QVBoxLayout()
        mid.setSpacing(1)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.bar.setFixedWidth(150)
        self.pct = sub_label("")
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        mid.addWidget(self.bar)
        mid.addWidget(self.pct)
        g.addLayout(mid)
        self.btns = QHBoxLayout()
        self.btns.setSpacing(4)
        g.addLayout(self.btns)
        self.b_pause = self._btn("❚❚", tr("q.pause"), self.pauseClicked)
        self.b_resume = self._btn("▶", tr("q.resume"), self.resumeClicked)
        self.b_cancel = self._btn("✕", tr("q.cancel"), self.cancelClicked)
        self.b_open = self._btn("📁", tr("q.open"), None)
        self.b_open.clicked.connect(self._open_folder)
        self.b_retry = self._btn("↻", tr("q.retry"), self.retryClicked)
        self.refresh()

    def _btn(self, text, tip, sig):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setFixedSize(28, 28)
        if sig is not None:
            b.clicked.connect(lambda: sig.emit(self.job))
        self.btns.addWidget(b)
        return b

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
        self.title.setText(self.title_text + clip)
        show = {"pause": False, "resume": False, "cancel": False, "open": False, "retry": False}
        self.bar.setVisible(True)
        if st == JobState.RUNNING:
            text = tr(STAGE_KEYS.get(p.stage, "q.video")) if p else tr("q.waiting")
            if p and p.stage == "encode" and "エンコーダ" in (p.message or ""):
                text += f"（{p.message.split(':', 1)[-1].strip()}）"
            elif p and p.stage in ("chat", "encode") and p.message:
                text = p.message
            self.status.setText(text)
            self._set_progress(p.ratio if p else None)
            show.update(pause=True, cancel=True)
        elif st == JobState.WAITING:
            self.status.setText(tr("q.waiting"))
            self._set_progress(0)
            show.update(pause=True, cancel=True)
        elif st == JobState.PAUSED:
            self.status.setText(tr("q.paused"))
            show.update(resume=True, cancel=True)
            self.pct.setText("")
        elif st == JobState.DONE:
            n = len(j.result.files) if j.result else 0
            self.status.setText(tr("q.done", n=n))
            self.status.setStyleSheet("color: #3a9d3a;")
            self.bar.setVisible(False)
            self.pct.setText("")
            show.update(open=True)
        elif st == JobState.FAILED:
            self.status.setText(tr("q.failed") + (f" ・ {j.error.message()}" if j.error else ""))
            self.status.setStyleSheet("color: #d13438;")
            self.bar.setVisible(False)
            self.pct.setText("")
            show.update(retry=True)
        elif st == JobState.CANCELLED:
            self.status.setText(tr("q.cancelled"))
            self.bar.setVisible(False)
            self.pct.setText("")
            show.update(retry=True)
        if st not in (JobState.DONE, JobState.FAILED):
            self.status.setStyleSheet("")
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
        self.action_btn = QPushButton()   # 「更新する」など、メッセージに対する操作（必要なときだけ表示）
        self.action_btn.hide()
        self.action_btn.clicked.connect(self._run_action)
        self._action = None
        row.addWidget(self.action_btn)
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
        self._set_action(None, None)
        self.show()

    def show_info(self, message: str, action_text: str | None = None, action=None) -> None:
        self._set_kind(False)
        self.icon.setText("ℹ")
        self.text.setText(message)
        self.text.setToolTip(message)
        self.detail.hide()
        self.detail_btn.hide()
        self._set_action(action_text, action)
        self.show()

    def _set_action(self, text, fn):
        self._action = fn
        self.action_btn.setText(text or "")
        self.action_btn.setVisible(bool(text and fn))

    def _run_action(self):
        fn, self._action = self._action, None
        self.action_btn.hide()
        if fn:
            fn()

    def _set_kind(self, error: bool):
        light = not theme.is_dark()
        for w in (self, self.text):
            w.setProperty("light", light)
            w.setProperty("info", not error)
            w.style().unpolish(w)
            w.style().polish(w)
        if not error:
            self.setStyleSheet("QFrame#errorbar { background: palette(alternate-base); }"
                               "QLabel#errtext { color: palette(text); }")
        else:
            self.setStyleSheet("")

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
