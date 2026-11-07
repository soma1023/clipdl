"""時刻の変換と範囲の検証。

- 入力: "hh:mm:ss" / "mm:ss" / "ss"、小数（"01:02:03.5"）も可
- 範囲: "開始-終了"（例 "00:01:00-00:02:30"）
"""
from __future__ import annotations

import math
import re

from .models import ClipRange

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)\s*$")


class TimeFormatError(ValueError):
    """時刻や範囲の書式が正しくない。"""


def parse_time(text: str) -> float:
    """'hh:mm:ss(.fff)' / 'mm:ss' / 'ss' を秒に変換する。"""
    if text is None:
        raise TimeFormatError("時刻が空です")
    m = _TIME_RE.match(str(text))
    if not m:
        raise TimeFormatError(f"時刻の形式が正しくありません: {text!r}（例: 01:23:45）")
    a, b, c = m.groups()
    # 区切りの数でどれが時・分かを決める
    if a is not None and b is not None:
        h, mnt = int(a), int(b)
    elif a is not None:
        h, mnt = 0, int(a)
    else:
        h, mnt = 0, 0
    sec = float(c)
    if a is not None and sec >= 60:
        raise TimeFormatError(f"秒は 0〜59 で指定してください: {text!r}")
    if b is not None and mnt >= 60:
        raise TimeFormatError(f"分は 0〜59 で指定してください: {text!r}")
    return h * 3600 + mnt * 60 + sec


def format_time(seconds: float, *, millis: bool = False, sep: str = ":") -> str:
    """秒を 'hh:mm:ss'（millis=True なら 'hh:mm:ss.mmm'）にする。"""
    if seconds < 0 or math.isnan(seconds):
        raise ValueError("負の時刻は扱えません")
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    out = f"{h:02d}{sep}{m:02d}{sep}{s:02d}"
    return f"{out}.{ms:03d}" if millis else out


def format_short(seconds: float) -> str:
    """画面表示用の短い長さ: 1 時間未満は "m:ss"、以上は "h:mm:ss"。"""
    total = int(round(max(0.0, seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_ass_time(seconds: float) -> str:
    """ASS 字幕の時刻 'H:MM:SS.cc'（1/100 秒単位）。"""
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    h, rem = divmod(total_cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def parse_range(text: str, *, index: int = 1) -> ClipRange:
    """'開始-終了' を ClipRange にする。"""
    parts = re.split(r"\s*[-~〜]\s*", str(text).strip())
    if len(parts) != 2 or not all(parts):
        raise TimeFormatError(f"範囲の形式が正しくありません: {text!r}（例: 00:01:00-00:02:30）")
    start, end = parse_time(parts[0]), parse_time(parts[1])
    return ClipRange(start=start, end=end, index=index).validated()


def validate_ranges(ranges: list[ClipRange], duration: float | None) -> list[ClipRange]:
    """範囲を検証し、開始順に並べ、番号を振り直す。動画の長さを超える終了は丸める。"""
    out = []
    for r in ranges:
        r = r.validated()
        if duration:
            if r.start >= duration:
                raise TimeFormatError(
                    f"開始 {format_time(r.start)} が動画の長さ {format_time(duration)} を超えています")
            if r.end > duration:
                r = ClipRange(start=r.start, end=duration, index=r.index, label=r.label)
        out.append(r)
    out.sort(key=lambda r: (r.start, r.end))
    return [ClipRange(start=r.start, end=r.end, index=i, label=r.label) for i, r in enumerate(out, 1)]
