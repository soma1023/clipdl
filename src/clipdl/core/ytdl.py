"""yt-dlp の共通設定とメタ情報の取得（DRM 検出を含む）。"""
from __future__ import annotations

import logging
from typing import Any

import yt_dlp

from .errors import UserError, classify, drm_error
from .models import VideoInfo
from .paths import env_flag, find_binary

log = logging.getLogger(__name__)


class _YtdlLogger:
    """yt-dlp のメッセージを logging に流す（画面には出さない）。"""

    def __init__(self):
        self.last_error = ""

    def debug(self, msg):
        if msg.startswith("[debug] "):
            log.debug(msg)
        else:
            log.info(msg)

    def info(self, msg):
        log.info(msg)

    def warning(self, msg):
        log.warning(msg)

    def error(self, msg):
        # yt-dlp のエラーはアプリ側で日本語にして表示するので、ここではログにだけ残す
        self.last_error = msg
        log.info("yt-dlp: %s", msg)


def base_params(cookie_opts: dict | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": _YtdlLogger(),
        "color": {"stdout": "never", "stderr": "never"},
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "noplaylist": True,
        "windowsfilenames": True,
        "allow_unplayable_formats": False,  # DRM 付きは扱わない
    }
    if env_flag("CLIPDL_YTDLP_VERBOSE"):  # サポート用: yt-dlp の詳しいログ（exe のバージョン検出など）をログに出す
        params["verbose"] = True
    ff = find_binary("ffmpeg")
    if ff:
        params["ffmpeg_location"] = ff
    deno = find_binary("deno")
    if deno:
        params["js_runtimes"] = {"deno": {"path": deno}}
    if cookie_opts:
        params.update(cookie_opts)
    return params


def _has_drm(info: dict) -> bool:
    if info.get("_has_drm"):
        return True
    formats = info.get("formats") or []
    return bool(formats) and all(f.get("has_drm") and f.get("has_drm") != "maybe" for f in formats)


def _best_video_fmt(info: dict) -> dict:
    fmts = [f for f in (info.get("formats") or []) if f.get("vcodec") not in (None, "none")]
    if not fmts:
        return {}
    return max(fmts, key=lambda f: ((f.get("height") or 0), (f.get("fps") or 0)))


def extract_info(url: str, cookie_opts: dict | None = None) -> VideoInfo:
    params = base_params(cookie_opts)
    try:
        with yt_dlp.YoutubeDL(params) as ydl:
            info = ydl.extract_info(url, download=False)
            info = ydl.sanitize_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise classify(str(e)) from e
    except Exception as e:  # noqa: BLE001 — 想定外も日本語化して返す
        raise classify(e) from e
    if not info:
        raise UserError("unknown", "動画情報を取得できませんでした。", "Could not get video information.", url)
    if info.get("_type") == "playlist":
        raise UserError("playlist", "再生リストには対応していません。動画 1 本の URL を入力してください。",
                        "Playlists are not supported. Enter a single video URL.", url)
    if _has_drm(info):
        raise drm_error()
    if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise classify({"is_live": "is currently live", "is_upcoming": "is_upcoming",
                        "post_live": "This live stream recording is not available"}[info["live_status"]])
    bv = _best_video_fmt(info)
    subs = info.get("subtitles") or {}
    return VideoInfo(
        url=info.get("webpage_url") or url,
        id=str(info.get("id")),
        extractor=str(info.get("extractor_key") or info.get("extractor") or "").lower(),
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or info.get("uploader_id") or "",
        duration=float(info["duration"]) if info.get("duration") else None,
        thumbnail=info.get("thumbnail"),
        upload_date=info.get("upload_date"),
        live_status=info.get("live_status"),
        has_live_chat="live_chat" in subs,
        fps=bv.get("fps") or info.get("fps"),
        width=bv.get("width") or info.get("width"),
        height=bv.get("height") or info.get("height"),
        raw=info,
    )
