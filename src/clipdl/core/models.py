"""core 全体で使うデータ型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CutMode(str, Enum):
    FAST = "fast"        # キーフレーム単位（再エンコードなし）
    PRECISE = "precise"  # 正確（再エンコードあり）


class CfrMode(str, Enum):
    AUTO = "auto"
    ALWAYS = "always"
    OFF = "off"


class ChatFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    ASS = "ass"
    MOV = "mov"   # オーバーレイ動画（ProRes 4444・背景透明）。初期値は OFF


@dataclass(frozen=True)
class ClipRange:
    start: float
    end: float
    index: int = 1
    label: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def validated(self) -> "ClipRange":
        from .timeutil import TimeFormatError, format_time
        if self.start < 0:
            raise TimeFormatError("開始時刻は 0 以上にしてください")
        if self.end <= self.start:
            raise TimeFormatError(
                f"終了 {format_time(self.end)} は開始 {format_time(self.start)} より後にしてください")
        return self

    def tag(self) -> str:
        """ファイル名用の短い識別子（例 c01_000100-000230）。"""
        from .timeutil import format_time
        return f"c{self.index:02d}_{format_time(self.start, sep='')}-{format_time(self.end, sep='')}"


@dataclass
class VideoInfo:
    url: str
    id: str
    extractor: str          # "youtube" / "twitch:vod" など（yt-dlp の extractor_key を小文字化）
    title: str
    channel: str
    duration: float | None
    thumbnail: str | None
    upload_date: str | None  # YYYYMMDD
    live_status: str | None
    has_live_chat: bool
    fps: float | None
    width: int | None
    height: int | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def site(self) -> str:
        e = self.extractor.lower()
        if e.startswith("youtube"):
            return "youtube"
        if e.startswith("twitch"):
            return "twitch"
        return e


@dataclass
class ChatMessage:
    time: float              # 動画（またはクリップ）先頭からの秒
    user: str
    text: str
    color: str | None = None  # "#RRGGBB"
    kind: str = "text"        # text / superchat / membership / sticker
    amount: str | None = None  # スーパーチャット金額など
    user_id: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)

    def shifted(self, offset: float) -> "ChatMessage":
        return ChatMessage(self.time - offset, self.user, self.text, self.color, self.kind,
                           self.amount, self.user_id, self.raw)


@dataclass
class Progress:
    """進捗の通知。ratio は 0〜1（不明なら None）。"""
    stage: str               # "info" / "video" / "encode" / "chat" / "comments" / "done"
    ratio: float | None = None
    message: str = ""
    clip_index: int | None = None
