"""アプリのアイコン（src/clipdl/gui/assets/clipdl.ico と clipdl.png）を作る。

描き方は clipdl.gui.art.app_icon と同じ（画面の左上・ウィンドウ・exe で同じ絵になる）。
.ico には 16〜256px を PNG のまま入れる（Windows Vista 以降で読める形）。
使い方: .venv\\Scripts\\python.exe packaging\\make_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from clipdl.gui.art import ASSET_DIR, ICON_SIZES, app_icon  # noqa: E402


def png_bytes(size: int) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    app_icon(size).save(buf, "PNG")
    return bytes(data)


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return head + entries + blobs


def main() -> int:
    QGuiApplication([])
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    images = [(s, png_bytes(s)) for s in ICON_SIZES]
    (ASSET_DIR / "clipdl.ico").write_bytes(build_ico(images))
    (ASSET_DIR / "clipdl.png").write_bytes(dict(images)[256])
    print(f"作りました: {ASSET_DIR / 'clipdl.ico'}（{len(images)} サイズ）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
