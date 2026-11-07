"""範囲バー: 動画全体の中で、どこを切り抜くかを表示・操作する。

- 範囲ごとに両端のつまみだけを持つ（プレビュー画像などは付けない）
- 短い範囲でもつまみが重ならないよう、表示上の最小間隔を確保する
- 重なったときは最後に触ったつまみを上に描き、先にクリックを受け付ける
- 時刻の入力欄とは MainWindow 側で双方向に連動させる
- つまみを選んで ← → で 1 秒、Shift+← → で 10 秒動かせる（細かい調整用）
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.timeutil import format_time

KNOB_W, KNOB_H = 12.0, 18.0
MIN_GAP_PX = KNOB_W + 2      # 同じ範囲の 2 つのつまみの、表示上の最小間隔
MIN_DURATION = 0.5           # 範囲の最短（秒）
PAD = 8.0


@dataclass
class _Knob:
    index: int
    side: str  # "start" / "end"


class RangeBar(QWidget):
    rangeChanged = Signal(int, float, float)   # 範囲番号（0 始まり）, 開始, 終了
    rangeSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.duration: float = 0.0
        self.ranges: list[tuple[float, float]] = []
        self.selected: int = -1
        self._last: _Knob | None = None   # 最後に触ったつまみ（上に描く）
        self._drag: _Knob | None = None
        self._drag_offset = 0.0

    # ------------------------------------------------------------------ データ
    def set_duration(self, seconds: float | None) -> None:
        self.duration = float(seconds or 0)
        self.update()

    def set_ranges(self, ranges: list[tuple[float, float]], selected: int | None = None) -> None:
        self.ranges = list(ranges)
        if selected is not None:
            self.selected = selected
        self.selected = min(self.selected, len(self.ranges) - 1)
        self.update()

    # ------------------------------------------------------------------ 座標
    def _track(self) -> QRectF:
        return QRectF(PAD, 22.0, max(1.0, self.width() - 2 * PAD), 4.0)

    def _x(self, t: float) -> float:
        tr = self._track()
        if self.duration <= 0:
            return tr.left()
        return tr.left() + max(0.0, min(1.0, t / self.duration)) * tr.width()

    def _t(self, x: float) -> float:
        tr = self._track()
        return max(0.0, min(1.0, (x - tr.left()) / tr.width())) * self.duration

    def knob_positions(self, i: int) -> tuple[float, float]:
        """範囲 i の 2 つのつまみの x（表示用。最小間隔を確保する）。"""
        s, e = self.ranges[i]
        xs, xe = self._x(s), self._x(e)
        if xe - xs < MIN_GAP_PX:
            mid = (xs + xe) / 2
            tr = self._track()
            mid = max(tr.left() + MIN_GAP_PX / 2, min(tr.right() - MIN_GAP_PX / 2, mid))
            xs, xe = mid - MIN_GAP_PX / 2, mid + MIN_GAP_PX / 2
        return xs, xe

    def _knob_rect(self, k: _Knob) -> QRectF:
        xs, xe = self.knob_positions(k.index)
        x = xs if k.side == "start" else xe
        cy = self._track().center().y()
        return QRectF(x - KNOB_W / 2, cy - KNOB_H / 2, KNOB_W, KNOB_H)

    def _knobs_in_z_order(self) -> list[_Knob]:
        """描く順（後ろほど上）。最後に触ったつまみ、選択中の範囲を上にする。"""
        ks = [_Knob(i, s) for i in range(len(self.ranges)) for s in ("start", "end")]

        def key(k: _Knob):
            last = self._last is not None and (k.index, k.side) == (self._last.index, self._last.side)
            return (k.index == self.selected, last)

        return sorted(ks, key=key)

    def _hit(self, pos: QPointF) -> _Knob | None:
        for k in reversed(self._knobs_in_z_order()):  # 上にあるものから
            if self._knob_rect(k).adjusted(-3, -4, 3, 4).contains(pos):
                return k
        return None

    # ------------------------------------------------------------------ 描画
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        acc = pal.color(QPalette.ColorRole.Accent)
        muted = pal.color(QPalette.ColorRole.PlaceholderText)
        tr = self._track()
        track_col = QColor(pal.color(QPalette.ColorRole.Mid))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track_col)
        p.drawRoundedRect(tr, 2, 2)
        # 目盛りの文字
        p.setPen(muted)
        f = p.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        p.drawText(QRectF(tr.left(), 0, 120, 16), Qt.AlignmentFlag.AlignLeft, "0:00:00")
        if self.duration > 0:
            p.drawText(QRectF(tr.right() - 120, 0, 120, 16), Qt.AlignmentFlag.AlignRight,
                       format_time(self.duration))
        # 範囲の帯
        for i in range(len(self.ranges)):
            xs, xe = self.knob_positions(i)
            c = QColor(acc)
            c.setAlphaF(1.0 if i == self.selected else 0.55)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(xs, tr.center().y() - 4, xe - xs, 8), 2, 2)
        # つまみ
        base = pal.color(QPalette.ColorRole.Base)
        for k in self._knobs_in_z_order():
            r = self._knob_rect(k)
            sel = k.index == self.selected
            p.setBrush(base)
            p.setPen(QPen(acc if sel else muted, 2))
            p.drawRoundedRect(r, 3, 3)
            if self.hasFocus() and self._last and (k.index, k.side) == (self._last.index, self._last.side):
                p.setPen(QPen(acc, 1, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 4, 4)
        p.end()

    # ------------------------------------------------------------------ 操作
    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton or self.duration <= 0:
            return
        pos = ev.position()
        k = self._hit(pos)
        if k is not None:
            self._drag = self._last = k
            r = self._knob_rect(k)
            self._drag_offset = pos.x() - r.center().x()
            self._select(k.index)
            self.update()
            return
        for i in range(len(self.ranges)):  # 帯をクリックしたら選択
            xs, xe = self.knob_positions(i)
            if xs <= pos.x() <= xe:
                self._select(i)
                return

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._drag is None:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._hit(pos) else Qt.CursorShape.ArrowCursor)
            return
        self._move_knob(self._drag, self._t(pos.x() - self._drag_offset))

    def mouseReleaseEvent(self, _):
        self._drag = None

    def keyPressEvent(self, ev):
        k = self._last
        if k is None or k.index >= len(self.ranges) or ev.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return super().keyPressEvent(ev)
        step = 10.0 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        s, e = self.ranges[k.index]
        cur = s if k.side == "start" else e
        self._move_knob(k, cur + (step if ev.key() == Qt.Key.Key_Right else -step))

    def _move_knob(self, k: _Knob, t: float) -> None:
        s, e = self.ranges[k.index]
        t = round(t)  # ドラッグは 1 秒単位（細かい値は入力欄で）
        if k.side == "start":
            s = max(0.0, min(t, e - MIN_DURATION))
        else:
            e = min(self.duration, max(t, s + MIN_DURATION))
        self.ranges[k.index] = (s, e)
        self.update()
        self.rangeChanged.emit(k.index, s, e)

    def _select(self, i: int) -> None:
        if i != self.selected:
            self.selected = i
            self.update()
        self.rangeSelected.emit(i)
