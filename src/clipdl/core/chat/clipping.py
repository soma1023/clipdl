"""範囲内のチャットを抜き出し、クリップの開始を 0 とした時刻に直す。"""
from __future__ import annotations

from bisect import bisect_left

from ..models import ChatMessage, ClipRange


def clip_messages(messages: list[ChatMessage], clip: ClipRange) -> list[ChatMessage]:
    """start <= time < end のメッセージを、time - start に直して返す（入力は時刻順を想定）。"""
    times = [m.time for m in messages]
    if times != sorted(times):
        messages = sorted(messages, key=lambda m: m.time)
        times = [m.time for m in messages]
    lo = bisect_left(times, clip.start)
    hi = bisect_left(times, clip.end)
    return [m.shifted(clip.start) for m in messages[lo:hi]]
