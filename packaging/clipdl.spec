# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller の設定（onedir）。packaging/build.py から呼ぶ。

ポイント:
- yt_dlp と yt_dlp_ejs は PYZ（exe の内部アーカイブ）に入れず、_internal/pylib/ に普通の .py として置く。
  PYZ に入れると FrozenImporter が先に見つけてしまい、sys.path の先頭に入れた更新版の zip が無視されるため。
  pylib は実行時フック（rthook_pylib.py）で sys.path の「末尾」に足すので、更新版（先頭）が優先される。
- yt_dlp の依存（標準ライブラリや requests など）は、解析には含めて同梱する（PYZ から外すのは yt_dlp 本体だけ）。
- ffmpeg / Deno / TwitchDownloaderCLI は build.py が exe の隣の vendor/ に置く（差し替えやすいように）。
"""
import os
from pathlib import Path

import yt_dlp
import yt_dlp_ejs

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
PYLIB_PACKAGES = {"yt_dlp": Path(yt_dlp.__file__).parent, "yt_dlp_ejs": Path(yt_dlp_ejs.__file__).parent}

datas = [(str(SRC / "clipdl" / "core" / "keys"), "clipdl/core/keys"),
         (str(SRC / "clipdl" / "core" / "chat" / "badges"), "clipdl/core/chat/badges")]  # Twitch配信風の自作バッジ
for name, src in PYLIB_PACKAGES.items():
    for f in src.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
            datas.append((str(f), str(Path("pylib") / name / f.relative_to(src).parent)))

common = dict(
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=["clipdl.gui.app", "clipdl.cli.main"],
    hookspath=[],
    runtime_hooks=[str(Path(SPECPATH) / "rthook_pylib.py")],
    # mutagen は GPL-2.0-or-later のため同梱しない（yt-dlp のメタデータ埋め込み用で、clipdl は使わない）
    excludes=["tkinter", "pytest", "pysequoia", "PyInstaller", "setuptools", "pip", "mutagen"],
)


def analysis(script):
    a = Analysis([str(script)], **common)
    # yt_dlp / yt_dlp_ejs を PYZ から外す（依存は残す）
    a.pure = [e for e in a.pure if not (e[0] == "yt_dlp" or e[0].startswith(("yt_dlp.", "yt_dlp_ejs")))]
    return a


gui_a = analysis(SRC / "clipdl" / "gui" / "__main__.py")
cli_a = analysis(SRC / "clipdl" / "__main__.py")

gui_exe = EXE(PYZ(gui_a.pure), gui_a.scripts, [], exclude_binaries=True, name="clipdl",
              console=False, upx=False, version=None)
cli_exe = EXE(PYZ(cli_a.pure), cli_a.scripts, [], exclude_binaries=True, name="clipdl-cli",
              console=True, upx=False, version=None)

coll = COLLECT(gui_exe, gui_a.binaries, gui_a.datas,
               cli_exe, cli_a.binaries, cli_a.datas,
               upx=False, name="clipdl")

# 入れ替え用プログラム（clipdl-updater.exe）: 標準ライブラリだけの 1 ファイル exe。
# 実行中は自分のファイルが使用中になるので、アプリが一時フォルダにコピーしてから起動する。
# build.py が build/dist/clipdl-updater.exe を配布フォルダにコピーする。
upd_a = Analysis([str(Path(SPECPATH) / "updater_entry.py")], pathex=[str(SRC)], datas=[], hiddenimports=[],
                 excludes=["tkinter", "PySide6", "yt_dlp", "yt_dlp_ejs", "Cryptodome", "platformdirs", "pytest",
                           "PyInstaller", "setuptools", "pip"])
upd_exe = EXE(PYZ(upd_a.pure), upd_a.scripts, upd_a.binaries, upd_a.datas, name="clipdl-updater",
              console=False, upx=False, version=None)
