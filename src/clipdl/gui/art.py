"""自前で描く絵: アプリのアイコン、サムネイルが無いときの代わり、角を丸めたサムネイル。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from . import theme

ASSET_DIR = Path(__file__).resolve().parent / "assets"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def app_icon(size: int) -> QPixmap:
    """アイコン: 黒の角丸の四角に、オレンジの「切り取りの角かっこ」と白い再生の三角。

    16〜20px では角かっこがつぶれるので、三角だけにする。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    r = QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ICON_BG))
    p.drawRoundedRect(r, s * 0.16, s * 0.16)
    p.setBrush(QColor("#FFFFFF"))
    if size <= 20:
        tri = QPainterPath()
        tri.moveTo(s * 0.36, s * 0.26)
        tri.lineTo(s * 0.76, s * 0.5)
        tri.lineTo(s * 0.36, s * 0.74)
        tri.closeSubpath()
        p.drawPath(tri)
        p.end()
        return pm
    pen = QPen(QColor(theme.ICON_ACCENT), max(1.6, s * 0.075), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    a, b, L = s * 0.24, s * 0.76, s * 0.16
    p.drawPolyline(QPolygonF([QPointF(a, a + L), QPointF(a, a), QPointF(a + L, a)]))
    p.drawPolyline(QPolygonF([QPointF(b, b - L), QPointF(b, b), QPointF(b - L, b)]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    tri = QPainterPath()
    cx, cy, h = s * 0.52, s * 0.5, s * 0.30
    tri.moveTo(cx - h * 0.42, cy - h / 2)
    tri.lineTo(cx + h * 0.55, cy)
    tri.lineTo(cx - h * 0.42, cy + h / 2)
    tri.closeSubpath()
    p.drawPath(tri)
    p.end()
    return pm


def window_icon() -> QIcon:
    """ウィンドウとタスクバーのアイコン（同梱の .ico があればそれを、無ければその場で描く）。"""
    ico = ASSET_DIR / "clipdl.ico"
    if ico.is_file():
        return QIcon(str(ico))
    icon = QIcon()
    for s in ICON_SIZES:
        icon.addPixmap(app_icon(s))
    return icon


_PAIRS = [("#F59E0B", "#DB2777"), ("#06B6D4", "#6366F1"), ("#10B981", "#0EA5E9"), ("#8B5CF6", "#EC4899"),
          ("#F97316", "#EAB308"), ("#14B8A6", "#3B82F6")]


def placeholder_thumb(w: int, h: int, key: str, radius: float) -> QPixmap:
    """サムネイルが無いときの代わり（URL ごとに決まった色のグラデーション）。"""
    c1, c2 = _PAIRS[int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % len(_PAIRS)]
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(c1))
    g.setColorAt(1, QColor(c2))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.fillPath(path, QBrush(g))
    p.setClipPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 38))
    p.drawEllipse(QPointF(w * 0.78, h * 0.25), h * 0.55, h * 0.55)
    p.drawEllipse(QPointF(w * 0.15, h * 0.95), h * 0.45, h * 0.45)
    p.end()
    return pm


def rounded(src: QPixmap, w: int, h: int, radius: float) -> QPixmap:
    """画像を w×h に切り抜いて（はみ出す分は中央で切る）、角を丸める。"""
    scaled = src.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(int((w - scaled.width()) / 2), int((h - scaled.height()) / 2), scaled)
    p.end()
    return pm
