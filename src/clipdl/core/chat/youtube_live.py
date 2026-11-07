"""YouTube ライブチャットのリプレイ（yt-dlp の live_chat 字幕）。

yt-dlp は live_chat を 1 行 1 JSON（replayChatItemAction）で書き出す。
長い配信だと時間がかかるため、書き出し中のファイルを監視して「動画内のどこまで取れたか」で進捗を出す。
"""
from __future__ import annotations

import copy
import json
import shutil
import logging
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

import yt_dlp

from ..errors import Cancelled, classify
from ..models import ChatMessage, Progress, VideoInfo
from ..ytdl import base_params
from .base import ChatProgress, ChatProvider, ChatUnavailable

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ 解析

def _runs_to_text(message: dict | None) -> str:
    if not message:
        return ""
    if "simpleText" in message:
        return message["simpleText"]
    out = []
    for run in message.get("runs", []):
        if "text" in run:
            out.append(run["text"])
        elif "emoji" in run:
            emoji = run["emoji"]
            shortcuts = emoji.get("shortcuts") or []
            # 絵文字は Unicode ならそのまま、カスタム絵文字は :name: で表す
            if emoji.get("isCustomEmoji") and shortcuts:
                out.append(shortcuts[0])
            else:
                out.append(emoji.get("emojiId") or (shortcuts[0] if shortcuts else ""))
    return "".join(out)


def _argb_to_hex(v: Any) -> str | None:
    try:
        n = int(v) & 0xFFFFFF
    except (TypeError, ValueError):
        return None
    return f"#{n:06X}"


def parse_action(item: dict, offset_ms: int) -> ChatMessage | None:
    """addChatItemAction の item 1 件を ChatMessage にする。対象外なら None。"""
    t = offset_ms / 1000.0
    if r := item.get("liveChatTextMessageRenderer"):
        return ChatMessage(t, _runs_to_text(r.get("authorName")), _runs_to_text(r.get("message")),
                           user_id=r.get("authorExternalChannelId"), raw=item)
    if r := item.get("liveChatPaidMessageRenderer"):
        return ChatMessage(t, _runs_to_text(r.get("authorName")), _runs_to_text(r.get("message")),
                           color=_argb_to_hex(r.get("bodyBackgroundColor")), kind="superchat",
                           amount=_runs_to_text(r.get("purchaseAmountText")),
                           user_id=r.get("authorExternalChannelId"), raw=item)
    if r := item.get("liveChatPaidStickerRenderer"):
        return ChatMessage(t, _runs_to_text(r.get("authorName")), "[ステッカー]",
                           color=_argb_to_hex(r.get("backgroundColor")), kind="sticker",
                           amount=_runs_to_text(r.get("purchaseAmountText")),
                           user_id=r.get("authorExternalChannelId"), raw=item)
    if r := item.get("liveChatMembershipItemRenderer"):
        text = _runs_to_text(r.get("headerSubtext")) or _runs_to_text(r.get("message"))
        return ChatMessage(t, _runs_to_text(r.get("authorName")), text, kind="membership",
                           user_id=r.get("authorExternalChannelId"), raw=item)
    return None


def parse_line(obj: dict) -> list[ChatMessage]:
    rep = obj.get("replayChatItemAction")
    if not rep:
        return []
    try:
        offset = int(rep.get("videoOffsetTimeMsec", 0))
    except (TypeError, ValueError):
        return []
    out = []
    for action in rep.get("actions", []):
        add = action.get("addChatItemAction")
        if not add:
            continue
        msg = parse_action(add.get("item", {}), offset)
        if msg is not None:
            out.append(msg)
    return out


def parse_lines(lines: Iterable[str]) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        out.extend(parse_line(obj))
    out.sort(key=lambda m: m.time)
    return out


def last_offset_ms(path: Path, tail_bytes: int = 65536) -> int | None:
    """書き出し中のファイルの末尾から、最後の videoOffsetTimeMsec を読む。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    idx = data.rfind('"videoOffsetTimeMsec"')
    if idx < 0:
        return None
    seg = data[idx:idx + 60]
    digits = "".join(ch for ch in seg.split(":", 1)[-1] if ch.isdigit() or ch == ",").split(",")[0]
    return int(digits) if digits else None


# ------------------------------------------------------------------ 取得

class YouTubeLiveChatProvider(ChatProvider):
    name = "youtube_live_chat"

    def __init__(self, cookie_opts: dict | None = None):
        self.cookie_opts = cookie_opts or {}

    def supports(self, info: VideoInfo) -> bool:
        return info.site == "youtube"

    raw_suffix = ".live_chat.jsonl"

    def parse(self, path: Path) -> list[ChatMessage]:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_lines(f)

    def fetch(self, info: VideoInfo, span, dest: Path, on_progress: ChatProgress | None, cancel):
        # YouTube のリプレイは途中から取れないため常に全体を取る（キャッシュで使い回す）
        if not info.has_live_chat:
            raise ChatUnavailable("この動画にはライブチャットのリプレイがありません")
        tmp = Path(tempfile.mkdtemp(prefix="clipdl-chat-"))
        params = base_params(self.cookie_opts)
        params.update({
            "skip_download": True,
            "writesubtitles": True,
            "subtitleslangs": ["live_chat"],
            "outtmpl": {"default": str(tmp / "chat.%(ext)s")},
        })
        stop = threading.Event()
        duration = info.duration or 0

        def find_file() -> Path | None:
            c = sorted(tmp.glob("*live_chat*"))
            return c[0] if c else None

        def watch():
            while not stop.wait(1.0):
                if cancel is not None and cancel.cancelled:
                    return
                f = find_file()
                if f is None or on_progress is None:
                    continue
                off = last_offset_ms(f)
                if off is not None:
                    ratio = min(1.0, off / 1000 / duration) if duration else None
                    on_progress(Progress("chat", ratio, f"ライブチャット取得中（{off // 60000} 分まで）"))

        def hook(d):
            if cancel is not None and cancel.cancelled:
                raise Cancelled()

        params["progress_hooks"] = [hook]
        th = threading.Thread(target=watch, daemon=True)
        th.start()
        try:
            with yt_dlp.YoutubeDL(params) as ydl:
                ydl.process_ie_result(copy.deepcopy(info.raw), download=True)
        except Cancelled:
            raise
        except yt_dlp.utils.DownloadError as e:
            if cancel is not None and cancel.cancelled:
                raise Cancelled() from e
            raise classify(str(e)) from e
        finally:
            stop.set()
            th.join(timeout=2)
        f = find_file()
        if f is None or f.suffix == ".part":
            raise ChatUnavailable("ライブチャットを取得できませんでした")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), dest)
        shutil.rmtree(tmp, ignore_errors=True)  # アプリが作った一時フォルダ
        if on_progress:
            on_progress(Progress("chat", 1.0, "ライブチャット取得完了"))
        return None
