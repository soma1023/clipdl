"""範囲バー: 動画全体の中で、どこを切り抜くかを表示・操作する。

- 範囲ごとに両端のつまみだけを持つ（プレビュー画像などは付けない）
- 短い範囲でもつまみが重ならないよう、表示上の最小間隔を確保する
- 重なったときは最後に触ったつまみを上に描き、先にクリックを受け付ける
- 時刻の入力欄とは MainWindow 側で双方向に連動させる
- つまみを選んで ← → で 1 秒、Shift+← → で 10 秒動かせる（細かい調整用）
- 太い線の上に、選んだ範囲を色の帯で示す。つまみの形と色はデザイン（theme）ごとに変わる
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.timeutil import format_time
from . import theme

KNOB_W, KNOB_H = 16.0, 30.0  # つまみの当たり判定（見た目はデザインごとに _draw_knob で描く）
MIN_GAP_PX = KNOB_W + 2      # 同じ範囲の 2 つのつまみの、表示上の最小間隔
MIN_DURATION = 0.5           # 範囲の最短（秒）
PAD = 14.0
TRACK_Y = 34.0               # 線の中心の高さ（上に範囲の番号、下に目盛りの時刻）


@dataclass
class _Knob:
    index: int
    side: str  # "start" / "end"


class RangeBar(QWidget):
    rangeChanged = Signal(int, float, float)   # 範囲番号（0 始まり）, 開始, 終了
    rangeSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        theme.notifier.changed.connect(self.update)
        self.duration: float = 0.0
        self.empty_text = ""         # 動画の長さが分からないときに線の下に出す文
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
        h = float(theme.current()[0].track_h)
        return QRectF(PAD, TRACK_Y - h / 2, max(1.0, self.width() - 2 * PAD), h)

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
        d, c = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tr = self._track()
        th = tr.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.track))
        p.drawRoundedRect(tr, th / 2, th / 2)
        # 目盛り（5 か所）と時刻
        f = QFont(theme.NUM_FONT)
        f.setPixelSize(11)
        p.setFont(f)
        label_y = tr.bottom() + 12
        if self.duration > 0:
            for k in range(5):
                x = tr.left() + tr.width() * k / 4
                p.setPen(QColor(c.border))
                p.drawLine(QPointF(x, tr.bottom() + 6), QPointF(x, tr.bottom() + 10))
                p.setPen(QColor(c.muted))
                align = (Qt.AlignmentFlag.AlignLeft if k == 0 else Qt.AlignmentFlag.AlignRight if k == 4
                         else Qt.AlignmentFlag.AlignHCenter)
                rx = x if k == 0 else x - 120 if k == 4 else x - 60
                p.drawText(QRectF(rx, label_y, 120, 16), align, format_time(self.duration * k / 4))
        else:
            p.setPen(QColor(c.muted))
            p.drawText(QRectF(tr.left(), label_y, tr.width(), 16), Qt.AlignmentFlag.AlignHCenter,
                       self.empty_text)
        # 範囲の帯（選択中は濃く、ほかは薄く）と、帯の上の番号
        fn = QFont(theme.NUM_FONT)
        fn.setPixelSize(11)
        fn.setBold(True)
        for i in range(len(self.ranges)):
            xs, xe = self.knob_positions(i)
            sel = i == self.selected
            band = QRectF(xs, tr.top(), max(4.0, xe - xs), th)
            if sel and d.gradient:
                g = QLinearGradient(band.topLeft(), band.topRight())
                g.setColorAt(0, QColor(c.accent))
                g.setColorAt(1, QColor(c.accent_hi))
                p.setBrush(QBrush(g))
            else:
                col = QColor(c.accent)
                col.setAlphaF(1.0 if sel else 0.42)
                p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            rad = th / 2 if d.knob != "bar" else 1.0
            p.drawRoundedRect(band, rad, rad)
            if len(self.ranges) > 1:
                p.setFont(fn)
                p.setPen(QColor(c.accent) if sel else QColor(c.muted))
                p.drawText(QRectF(xs - 20, tr.top() - 24, (xe - xs) + 40, 14), Qt.AlignmentFlag.AlignHCenter,
                           str(i + 1))
        # つまみ
        for k in self._knobs_in_z_order():
            r = self._knob_rect(k)
            sel = k.index == self.selected
            self._draw_knob(p, d, c, r.center().x(), r.center().y(), sel)
            if self.hasFocus() and self._last and (k.index, k.side) == (self._last.index, self._last.side):
                p.setPen(QPen(QColor(c.accent), 1, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 4, 4)
        p.end()

    @staticmethod
    def _draw_knob(p: QPainter, d, c, x: float, cy: float, sel: bool) -> None:
        acc, white, muted = QColor(c.accent), QColor("#FFFFFF"), QColor(c.muted)
        if d.knob == "pill":       # 縦長の持ち手（線入り）
            w, h = (14.0, 28.0) if sel else (11.0, 22.0)
            p.setPen(QPen(acc if sel else muted, 2))
            p.setBrush(white)
            p.drawRoundedRect(QRectF(x - w / 2, cy - h / 2, w, h), 4, 4)
            p.setPen(QPen(QColor("#9AA3AF"), 1))
            for dy in (-4, 0, 4):
                p.drawLine(QPointF(x - 2.5, cy + dy), QPointF(x + 2.5, cy + dy))
        elif d.knob == "circle":   # 白い丸
            rad = 12.0 if sel else 9.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawEllipse(QPointF(x, cy + 1.5), rad + 1, rad + 1)
            p.setBrush(white)
            p.setPen(QPen(acc if sel else muted, 3 if sel else 2))
            p.drawEllipse(QPointF(x, cy), rad, rad)
        else:                      # 細い縦棒
            w, h = (4.0, 30.0) if sel else (3.0, 22.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(acc if sel else muted)
            p.drawRoundedRect(QRectF(x - w / 2, cy - h / 2, w, h), 1.5, 1.5)

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
