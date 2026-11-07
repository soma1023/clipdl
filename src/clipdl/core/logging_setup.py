"""ログの設定と「ログを書き出す」（サポート用 zip）。"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from .. import __version__
from .paths import log_dir

LOG_NAME = "clipdl.log"


def setup_logging(verbose: bool = False) -> Path:
    path = log_dir() / LOG_NAME
    root = logging.getLogger()
    if any(getattr(h, "_clipdl", False) for h in root.handlers):
        if verbose:  # 起動時に先に設定済みでも、詳しい表示の指定は反映する
            for h in root.handlers:
                if getattr(h, "_clipdl", False) and isinstance(h, logging.StreamHandler) \
                        and not isinstance(h, logging.FileHandler):
                    h.setLevel(logging.DEBUG)
        return path
    root.setLevel(logging.DEBUG)
    fh = logging.handlers.TimedRotatingFileHandler(path, when="midnight", backupCount=14, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    fh._clipdl = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    if sys.stderr is not None:  # GUI の exe（コンソールなし）では stderr が無い
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
        sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        sh._clipdl = True  # type: ignore[attr-defined]
        root.addHandler(sh)
    logging.getLogger(__name__).info("clipdl %s / Python %s / %s", __version__, platform.python_version(),
                                     platform.platform())
    return path


_SECRET_PATTERNS = [
    # Cookie・トークンらしき値
    (re.compile(r"(?i)(cookie|authorization|oauth|token|sapisid|__secure-[\w-]+)([\"']?\s*[:=]\s*)([^\s;,\"']+)"),
     r"\1\2***"),
]


def mask(text: str) -> str:
    """ユーザー名を含むパスや秘密情報を伏せる。"""
    home = str(Path.home())
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    for variant in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        text = text.replace(variant, "~")
    if user and len(user) >= 3:
        text = re.sub(re.escape(user), "<user>", text, flags=re.IGNORECASE)
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def export_logs(dest_dir: Path) -> Path:
    """ログを伏せ字にして zip にまとめる。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"clipdl-logs-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("system.txt", mask(
            f"clipdl {__version__}\nPython {platform.python_version()}\n{platform.platform()}\n"))
        files = sorted(log_dir().glob(LOG_NAME + "*")) + sorted(log_dir().glob("clipdl-updater.log*"))
        for p in files:  # 本体のログと、入れ替え用プログラムのログ
            try:
                zf.writestr(p.name + ".txt", mask(p.read_text("utf-8", errors="replace")))
            except OSError:
                continue
    return out
