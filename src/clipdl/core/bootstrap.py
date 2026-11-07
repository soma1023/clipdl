"""起動の最初に呼ぶ: 検証済みの更新版 yt-dlp / yt-dlp-ejs があれば、ハッシュを照合してから読み込む。

- 更新版は data_dir()/updates/ に置き、manifest.json に「検証が通ったときの SHA-256」を記録している
- 起動のたびにファイルの SHA-256 を計算し、記録と一致したものだけを sys.path の先頭に入れる
- 一致しない・ファイルが無い・記録が壊れているときは何もしない（同梱版で動く）
- yt-dlp を import する前に呼ぶ必要がある（このモジュール自体は yt-dlp を import しない）
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import data_dir

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"
# manifest のキー → import するときのモジュール名
COMPONENTS = {"yt-dlp": "yt_dlp", "yt-dlp-ejs": "yt_dlp_ejs"}


@dataclass
class ActiveUpdate:
    name: str
    version: str
    path: Path


ACTIVE: dict[str, ActiveUpdate] = {}   # 実際に読み込んだ更新版
REJECTED: dict[str, str] = {}          # 読み込まなかった理由


def updates_dir() -> Path:
    d = data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_manifest(root: Path | None = None) -> dict:
    p = (root or updates_dir()) / MANIFEST
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def activate_updates(root: Path | None = None, components: dict[str, str] | None = None) -> dict[str, ActiveUpdate]:
    """記録と一致する更新版だけを読み込む。読み込んだものを返す。"""
    root = root or updates_dir()
    components = components or COMPONENTS
    manifest = read_manifest(root)
    activated: dict[str, ActiveUpdate] = {}
    for name, module in components.items():
        entry = manifest.get(name)
        if not entry:
            continue
        bundled = bundled_version(module)
        if bundled and not _version_newer(str(entry.get("version", "")), bundled):
            # アプリの更新で同梱版のほうが新しく（または同じに）なった。警告は出さずに同梱版を使う
            log.info("同梱版の %s %s が更新版 %s より新しい（または同じ）ため、同梱版を使います",
                     name, bundled, entry.get("version"))
            if name == "yt-dlp":   # ejs は yt-dlp と組。yt-dlp を同梱版にするなら ejs も同梱版にする
                manifest = {k: v for k, v in manifest.items() if k != "yt-dlp-ejs"}
            continue
        reason = _check(root, entry, module)
        if name == "yt-dlp-ejs" and "yt-dlp" in manifest and "yt-dlp" not in activated and not reason:
            # yt-dlp-ejs は yt-dlp の版と組で使う。yt-dlp の更新版を使えないなら ejs も使わない
            reason = "対になる yt-dlp の更新版を使えないため"
        if reason:
            REJECTED[name] = reason
            log.warning("更新版の %s を使わず同梱版で動きます: %s", name, reason)
            continue
        path = root / entry["file"]
        sys.path.insert(0, str(path))
        activated[name] = ActiveUpdate(name, str(entry.get("version")), path)
        log.info("更新版の %s %s を使います（SHA-256 一致）", name, entry.get("version"))
    ACTIVE.update(activated)
    return activated


_VERSION_LINE = re.compile(r"""^__version__\s*=\s*(?:version\s*=\s*)?['"]([^'"]+)['"]""", re.MULTILINE)


def bundled_version(module: str) -> str | None:
    """sys.path のフォルダにある同梱版（zip の更新版は除く）の版を、import せずに読む。"""
    for entry in sys.path:
        d = Path(entry) / module
        if not entry or not d.is_dir():
            continue
        for name in ("version.py", "_version.py"):
            f = d / name
            if f.is_file():
                m = _VERSION_LINE.search(f.read_text("utf-8", errors="replace"))
                if m:
                    return m.group(1)
    return None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _version_newer(a: str, b: str) -> bool:
    """a が b より新しいか（yt-dlp の 2026.10.26 / 2026.11.02.1、ejs の 0.8.0 のような数字の並びを比べる）。"""
    return _version_tuple(a) > _version_tuple(b)


def _check(root: Path, entry: dict, module: str) -> str | None:
    """問題があれば理由を返す。"""
    if module in sys.modules:
        return "すでに同梱版を読み込んでいるため（起動の順番の問題）"
    file, expected = entry.get("file"), entry.get("sha256")
    if not file or not expected or "/" in file or "\\" in file or ".." in file:
        return "記録（manifest.json）が正しくありません"
    path = root / file
    if not path.is_file():
        return "ファイルが見つかりません"
    actual = sha256_file(path)
    if actual != str(expected).lower():
        return f"SHA-256 が記録と一致しません（記録 {str(expected)[:12]}… / 実際 {actual[:12]}…）"
    return None


def startup(verbose: bool = False) -> None:
    """CLI / GUI の入口で、yt-dlp を import する前に呼ぶ。"""
    from .logging_setup import setup_logging  # yt-dlp を import しない
    for stream in (sys.stdout, sys.stderr):  # ログを出す前に UTF-8 にそろえる（パイプ先での文字化け防止）
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    setup_logging(verbose)
    add_vendor_to_path()
    try:
        activate_updates()
    except Exception:  # noqa: BLE001 — 更新版の確認で起動できなくなることは避ける
        log.exception("更新版の確認に失敗しました（同梱版で動きます）")
    if ACTIVE:
        _try_import_or_fallback()


def add_vendor_to_path() -> None:
    """同梱の ffmpeg / Deno を PATH の先頭に足す。

    yt-dlp は範囲ダウンロードの直前に、ffmpeg_location の指定を見ずに PATH から ffmpeg を探す箇所がある
    （FFmpegFD.available）。ffmpeg を入れていない PC では範囲 DL が「ffmpeg is not installed」で失敗するため。
    """
    import os
    from .paths import vendor_dir
    v = vendor_dir()
    dirs = [str(d) for d in (v / "ffmpeg" / "bin", v / "deno") if d.is_dir()]
    if dirs:
        current = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(dirs + [current] if current else dirs)
        log.info("PATH の先頭に同梱の部品を追加: %s", "; ".join(dirs))


def _try_import_or_fallback() -> None:
    """更新版を実際に読み込んでみる。失敗したら（exe に無い標準ライブラリを使う新版など）同梱版に戻す。"""
    try:
        import yt_dlp  # noqa: F401
        import yt_dlp.version  # noqa: F401
        if "yt-dlp-ejs" in ACTIVE:
            import yt_dlp_ejs  # noqa: F401
        log.info("yt-dlp の読み込み元: %s", getattr(yt_dlp, "__file__", "?"))
    except Exception as e:  # noqa: BLE001
        reason = f"更新版を読み込めませんでした（{type(e).__name__}: {e}）"
        log.warning("同梱版に戻します: %s", reason)
        for name, act in list(ACTIVE.items()):
            if str(act.path) in sys.path:
                sys.path.remove(str(act.path))
            REJECTED[name] = reason
            del ACTIVE[name]
        for m in [m for m in sys.modules if m == "yt_dlp" or m.startswith(("yt_dlp.", "yt_dlp_ejs"))]:
            del sys.modules[m]
        import importlib
        importlib.invalidate_caches()
