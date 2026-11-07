"""設定・ログ・キャッシュの場所と、同梱バイナリ（vendor）の検出。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from platformdirs import PlatformDirs

from .. import APP_NAME

_dirs = PlatformDirs(APP_NAME, appauthor=False, roaming=False)


def env(name: str) -> str | None:
    """環境変数を読む。前後の空白を取り除き、空なら None（cmd の `set X=1 && ...` は値が "1 " になるため）。"""
    v = os.environ.get(name)
    if v is None:
        return None
    v = v.strip().strip('"').strip()
    return v or None


def env_flag(name: str) -> bool:
    """1 / true / yes / on なら True。"""
    return (env(name) or "").lower() in ("1", "true", "yes", "on")


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    return _ensure(Path(env("CLIPDL_CONFIG_DIR") or _dirs.user_config_path))


def log_dir() -> Path:
    return _ensure(Path(env("CLIPDL_LOG_DIR") or _dirs.user_log_path))


def cache_dir() -> Path:
    return _ensure(Path(env("CLIPDL_CACHE_DIR") or _dirs.user_cache_path))


def data_dir() -> Path:
    """更新した yt-dlp などを置く場所（フェーズ2の updater で使う）。"""
    return _ensure(Path(env("CLIPDL_DATA_DIR") or _dirs.user_data_path))


def app_root() -> Path:
    """アプリのルート。exe 化時は exe のあるフォルダ、開発時はリポジトリ直下。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


def vendor_dir() -> Path:
    """同梱バイナリの場所。exe 化したときは exe と同じフォルダの vendor（利用者が差し替えやすいように）。"""
    override = env("CLIPDL_VENDOR_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "vendor"
    return app_root() / "vendor"


# 同梱バイナリの相対位置
_VENDOR_BINS = {
    "ffmpeg": "ffmpeg/bin/ffmpeg",
    "ffprobe": "ffmpeg/bin/ffprobe",
    "deno": "deno/deno",
    "TwitchDownloaderCLI": "TwitchDownloaderCLI/TwitchDownloaderCLI",
}


def find_binary(name: str) -> str | None:
    """同梱版を優先し、無ければ PATH から探す。"""
    exe = ".exe" if os.name == "nt" else ""
    rel = _VENDOR_BINS.get(name)
    if rel:
        p = vendor_dir() / (rel + exe)
        if p.is_file():
            return str(p)
    return shutil.which(name)
