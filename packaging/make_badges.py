"""YouTube のモデレーター・オーナー・認証済みバッジの代わりのアイコンを作る（自作の図形。開発時に 1 回だけ実行）。

使い方: .venv\\Scripts\\python.exe packaging\\make_badges.py
出力: src/clipdl/core/chat/badges/{moderator,owner,verified}.png（72x72・背景透明）
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPolygonF  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "src" / "clipdl" / "core" / "chat" / "badges"
S = 72


def canvas():
    img = QImage(S, S, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return img, p


def rounded(p, color):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(2, 2, S - 4, S - 4), 12, 12)


def moderator():
    img, p = canvas()
    rounded(p, "#2E7D32")   # 緑の四角に白いスパナ
    p.translate(S / 2, S / 2)
    p.rotate(-45)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(-5, -6, 10, 30), 4, 4)            # 柄
    head = QPainterPath()
    head.addEllipse(QPointF(0, -14), 13, 13)
    cut = QPainterPath()
    cut.addRect(QRectF(-5, -30, 10, 16))                       # 口
    p.drawPath(head.subtracted(cut))
    p.end()
    return img


def owner():
    img, p = canvas()
    rounded(p, "#F9A825")   # 黄色の四角に白い王冠
    p.setBrush(QColor("white"))
    crown = QPolygonF([QPointF(14, 50), QPointF(14, 24), QPointF(26, 36), QPointF(36, 18), QPointF(46, 36),
                       QPointF(58, 24), QPointF(58, 50)])
    p.drawPolygon(crown)
    p.drawRect(QRectF(14, 52, 44, 6))
    p.end()
    return img


def verified():
    img, p = canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#9E9E9E"))   # 灰色の丸に白いチェック
    p.drawEllipse(QRectF(4, 4, S - 8, S - 8))
    pen = QPen(QColor("white"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(21, 37), QPointF(32, 48), QPointF(52, 26)]))
    p.end()
    return img


def main() -> int:
    QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("moderator", moderator), ("owner", owner), ("verified", verified)):
        fn().save(str(OUT / f"{name}.png"))
        print(OUT / f"{name}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
