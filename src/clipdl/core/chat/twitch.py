"""Twitch VOD のチャットリプレイ（TwitchDownloaderCLI を外部 exe として呼ぶ）。

yt-dlp の rechat は Twitch 旧 API の廃止で使えない（2026-06 に削除）。PLAN.md §6 参照。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from ..errors import Cancelled, UserError, classify
from ..models import ChatMessage, Progress, VideoInfo
from ..paths import find_binary
from ..timeutil import format_time
from .base import ChatProgress, ChatProvider

log = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_PERCENT_RE = re.compile(r"(\d{1,3})\s*%")


def detect_time_base(data: dict) -> tuple[str, float]:
    """content_offset_seconds が「VOD の先頭から」か「範囲（-b）の先頭から」かを判定する。

    TwitchDownloaderCLI 1.56.5 のソース（ChatDownloader.cs）では、GQL の contentOffsetSeconds（VOD 先頭基準）を
    範囲で絞り込むだけで値は変えず、範囲の開始を video.start に記録している。つまり通常は「VOD 先頭基準」。
    将来の仕様変更に備え、最初のコメントが video.start より明らかに前なら「範囲基準」とみなして補正する。
    戻り値: ("vod" または "range", VOD 先頭基準にするため足す秒数)
    """
    start = float(((data.get("video") or {}).get("start")) or 0.0)
    offsets = []
    for c in data.get("comments") or []:
        try:
            offsets.append(float(c.get("content_offset_seconds")))
        except (TypeError, ValueError):
            continue
    if start >= 1.0 and offsets and min(offsets) < start - 1.0:
        return "range", start
    return "vod", 0.0


def parse_twitch_json(data: dict) -> list[ChatMessage]:
    """time は常に「VOD の先頭から」の秒にそろえて返す。"""
    base, add = detect_time_base(data)
    if base == "range":
        log.info("Twitch チャットの時刻が範囲基準のため %.1f 秒を足して VOD 基準に直します", add)
    out = []
    for c in data.get("comments") or []:
        try:
            t = float(c.get("content_offset_seconds")) + add
        except (TypeError, ValueError):
            continue
        commenter = c.get("commenter") or {}
        message = c.get("message") or {}
        text = message.get("body")
        if text is None:
            text = "".join(f.get("text", "") for f in message.get("fragments") or [])
        bits = message.get("bits_spent") or 0
        out.append(ChatMessage(
            time=t,
            user=commenter.get("display_name") or commenter.get("name") or "",
            text=text,
            color=message.get("user_color") or None,
            kind="bits" if bits else "text",
            amount=f"{bits} bits" if bits else None,
            user_id=str(commenter.get("_id") or "") or None,
            raw=c,
        ))
    out.sort(key=lambda m: m.time)
    return out


def merge_twitch_json(old: dict, new: dict) -> dict:
    """2 つの取得結果を 1 つにまとめる（時刻は VOD 先頭基準にそろえ、同じコメントは 1 件にする）。"""
    seen: set = set()
    comments = []
    for data in (old, new):
        _, add = detect_time_base(data)
        for c in data.get("comments") or []:
            c = dict(c)
            try:
                c["content_offset_seconds"] = float(c.get("content_offset_seconds")) + add
            except (TypeError, ValueError):
                continue
            key = c.get("_id") or (c["content_offset_seconds"], (c.get("commenter") or {}).get("_id"),
                                   (c.get("message") or {}).get("body"))
            if key in seen:
                continue
            seen.add(key)
            comments.append(c)
    comments.sort(key=lambda c: c["content_offset_seconds"])
    out = dict(new)
    video = dict(new.get("video") or {})
    starts = [float((d.get("video") or {}).get("start") or 0) for d in (old, new)]
    ends = [float((d.get("video") or {}).get("end") or 0) for d in (old, new)]
    video["start"], video["end"] = min(starts), max(ends)
    out["video"], out["comments"] = video, comments
    out["embeddedData"] = merge_embedded(old.get("embeddedData"), new.get("embeddedData"))
    return out


def merge_embedded(a: dict | None, b: dict | None) -> dict | None:
    """埋め込み画像（エモート・バッジなど）を、重複を除いてまとめる。"""
    if not a and not b:
        return None
    out: dict = {}
    for key in ("thirdParty", "firstParty", "twitchBadges", "twitchBits"):
        seen, items = set(), []
        for src in (b or {}, a or {}):
            for item in src.get(key) or []:
                k = item.get("id") or item.get("name") or json.dumps(item, sort_keys=True)[:200]
                if k in seen:
                    continue
                seen.add(k)
                items.append(item)
        out[key] = items
    return out


class TwitchChatProvider(ChatProvider):
    name = "twitch_tdcli"
    raw_suffix = ".twitch_chat.json"

    def supports(self, info: VideoInfo) -> bool:
        return info.site == "twitch"

    last_time_base: str | None = None

    def parse(self, path: Path) -> list[ChatMessage]:
        data = json.loads(path.read_text("utf-8"))
        self.last_time_base = detect_time_base(data)[0]
        return parse_twitch_json(data)

    def merge(self, old: Path, new: Path, dest: Path) -> None:
        merged = merge_twitch_json(json.loads(old.read_text("utf-8")), json.loads(new.read_text("utf-8")))
        dest.write_text(json.dumps(merged, ensure_ascii=False), "utf-8")

    def fetch(self, info: VideoInfo, span, dest: Path, on_progress: ChatProgress | None, cancel):
        exe = find_binary("TwitchDownloaderCLI")
        if not exe:
            raise UserError("tdcli_missing", "Twitch チャット取得用の部品（TwitchDownloaderCLI）が見つかりません。",
                            "TwitchDownloaderCLI (needed for Twitch chat) was not found.", "TwitchDownloaderCLI")
        vod_id = re.sub(r"^v", "", info.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="clipdl-tdcli-"))
        tmp_out = tmp_dir / "chat.json"
        # -E: エモート・バッジの画像を埋め込む（スタイル「Twitch配信風」を通信なしで描画するため）
        args = [exe, "chatdownload", "--id", vod_id, "-o", str(tmp_out), "--collision", "Overwrite",
                "--banner", "false", "--temp-path", str(tmp_dir), "-E"]
        if span is not None:
            start, end = span
            args += ["-b", format_time(max(0.0, start)), "-e", format_time(end)]
        log.debug("TwitchDownloaderCLI: %s", subprocess.list2cmdline(args))
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        tail: list[str] = []
        stop = threading.Event()

        def watch_cancel():
            while not stop.wait(0.3):
                if cancel is not None and cancel.cancelled and proc.poll() is None:
                    proc.kill()
                    return

        th = threading.Thread(target=watch_cancel, daemon=True)
        th.start()
        try:
            assert proc.stdout
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-30]
                m = _PERCENT_RE.search(line)
                if m and on_progress:
                    on_progress(Progress("chat", min(100, int(m.group(1))) / 100, "Twitch チャット取得中"))
            proc.wait()
        finally:
            stop.set()
            th.join(timeout=1)
        if cancel is not None and cancel.cancelled:
            raise Cancelled()
        if proc.returncode != 0 or not tmp_out.exists():
            raise classify("TwitchDownloaderCLI failed:\n" + "\n".join(tail))
        os.replace(tmp_out, dest)
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)  # アプリが作った一時フォルダ
        if on_progress:
            on_progress(Progress("chat", 1.0, "Twitch チャット取得完了"))
        return span
