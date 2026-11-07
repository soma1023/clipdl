# PyInstaller の実行時フック: 同梱の yt_dlp / yt_dlp_ejs（_internal/pylib）を sys.path の末尾に足す。
# 末尾なので、core/bootstrap.py が先頭に入れる検証済みの更新版のほうが優先される。
import os
import sys

_pylib = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "pylib")
if os.path.isdir(_pylib) and _pylib not in sys.path:
    sys.path.append(_pylib)
