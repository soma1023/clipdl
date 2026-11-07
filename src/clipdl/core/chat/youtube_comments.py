"""YouTube の通常コメント（yt-dlp の getcomments）。動画内の時刻を持たないため、範囲とは連動させない。"""
from __future__ import annotations

import copy
import logging
from typing import Any

import yt_dlp

from ..errors import Cancelled, classify
from ..models import Progress, VideoInfo
from ..ytdl import base_params

log = logging.getLogger(__name__)


def fetch_comments(info: VideoInfo, cookie_opts: dict | None = None, max_comments: int | None = None,
                   on_progress=None, cancel=None) -> list[dict[str, Any]]:
    params = base_params(cookie_opts)
    params.update({"skip_download": True, "getcomments": True})
    if max_comments:
        params["extractor_args"] = {"youtube": {"max_comments": [str(max_comments)]}}
    if on_progress:
        on_progress(Progress("comments", None, "コメント取得中"))
    try:
        with yt_dlp.YoutubeDL(params) as ydl:
            # コメントは extract 時に取得されるので URL から取り直す
            data = ydl.extract_info(info.url, download=False)
    except yt_dlp.utils.DownloadError as e:
        if cancel is not None and cancel.cancelled:
            raise Cancelled() from e
        raise classify(str(e)) from e
    comments = copy.deepcopy((data or {}).get("comments") or [])
    if on_progress:
        on_progress(Progress("comments", 1.0, f"コメント {len(comments)} 件"))
    log.info("YouTube コメント %d 件", len(comments))
    return comments


COMMENT_CSV_FIELDS = ("timestamp", "author", "text", "like_count", "is_pinned", "parent", "id")
