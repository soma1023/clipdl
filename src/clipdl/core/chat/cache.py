"""チャットの元データを動画 ID ごとにキャッシュし、複数の範囲・再実行で使い回す。

<cache>/chat/<provider>_<video_id><suffix>     元データ
<cache>/chat/<provider>_<video_id>.meta.json   取得した区間など
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..models import ChatMessage, VideoInfo
from ..paths import cache_dir
from .base import ChatProgress, ChatProvider

log = logging.getLogger(__name__)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


Span = tuple[float, float]


def merge_spans(spans: list[Span]) -> list[Span]:
    """重なる・接する区間をまとめ、開始順に並べる。"""
    out: list[list[float]] = []
    for a, b in sorted((float(x), float(y)) for x, y in spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _as_spans(cached) -> list[Span] | None:
    """meta の値を「区間のリスト」にそろえる（None は全体）。旧形式（区間 1 つ）も読む。"""
    if cached is None:
        return None
    if len(cached) == 2 and all(isinstance(x, (int, float)) for x in cached):
        return [tuple(cached)]  # type: ignore[list-item]
    return [tuple(x) for x in cached]  # type: ignore[misc]


def covers(cached, needed: Span | None) -> bool:
    """キャッシュ済みの区間 cached（None は全体。区間 1 つ、または区間のリスト）が、
    必要な区間 needed（None は全体）を含むか。"""
    spans = _as_spans(cached)
    if spans is None:
        return True
    if needed is None:
        return False
    return any(a <= needed[0] and needed[1] <= b for a, b in merge_spans(spans))


def union_span(ranges: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not ranges:
        return None
    return (min(r[0] for r in ranges), max(r[1] for r in ranges))


@dataclass
class ChatStats:
    """チャット取得の計測値（動作確認・サポート用）。"""
    provider: str
    cache_hit: bool
    fetch_seconds: float          # 取得にかかった秒数（キャッシュ使用時は 0）
    parse_seconds: float
    raw_bytes: int
    messages: int
    span: list[tuple[float, float]] | None   # キャッシュ済みの区間（None は全体）
    raw_path: Path
    time_base: str | None = None  # Twitch: "vod" / "range"

    def summary(self) -> str:
        size = f"{self.raw_bytes / 1_048_576:.1f} MB" if self.raw_bytes >= 1_048_576 else f"{self.raw_bytes / 1024:.0f} KB"
        src = "キャッシュを使用" if self.cache_hit else f"取得 {self.fetch_seconds:.1f} 秒"
        span = "全体" if self.span is None else ", ".join(f"{a:.0f}〜{b:.0f} 秒" for a, b in self.span)
        tb = f" / 時刻基準: {'VOD 先頭' if self.time_base == 'vod' else '範囲の先頭（補正済み）'}" if self.time_base else ""
        return (f"チャット（{self.provider}）: {src} / 解析 {self.parse_seconds:.1f} 秒 / 元データ {size} / "
                f"{self.messages:,} 件 / 区間 {span}{tb}")


class ChatCache:
    def __init__(self, root: Path | None = None):
        self.root = root or (cache_dir() / "chat")
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, provider: ChatProvider, info: VideoInfo) -> tuple[Path, Path]:
        base = f"{_safe(provider.name)}_{_safe(info.id)}"
        return self.root / (base + provider.raw_suffix), self.root / (base + ".meta.json")

    def lookup(self, provider: ChatProvider, info: VideoInfo, span) -> Path | None:
        raw, meta = self._paths(provider, info)
        if not (raw.exists() and meta.exists()):
            return None
        try:
            m = json.loads(meta.read_text("utf-8"))
        except ValueError:
            return None
        return raw if covers(self._meta_spans(m), span) else None

    @staticmethod
    def _meta_spans(m: dict) -> list[Span] | None:
        if "spans" in m:
            return _as_spans(m["spans"])
        return _as_spans(m.get("span"))  # 旧形式

    def get(self, provider: ChatProvider, info: VideoInfo, span: tuple[float, float] | None,
            on_progress: ChatProgress | None = None, cancel=None) -> tuple[list[ChatMessage], ChatStats]:
        hit = self.lookup(provider, info, span)
        raw, meta = self._paths(provider, info)
        fetch_s = 0.0
        if hit is not None:
            log.info("チャットのキャッシュを使用: %s", hit.name)
            spans = self._meta_spans(json.loads(meta.read_text("utf-8")))
        else:
            old_spans: list[Span] | None = []
            if raw.exists() and meta.exists():
                try:
                    old_spans = self._meta_spans(json.loads(meta.read_text("utf-8")))
                except ValueError:
                    old_spans = []
            tmp = raw.with_name(raw.name + ".part")
            t0 = time.monotonic()
            got = provider.fetch(info, span, tmp, on_progress, cancel)
            fetch_s = time.monotonic() - t0
            can_merge = got is not None and old_spans and raw.exists() and hasattr(provider, "merge")
            if can_merge:
                # 前に取った区間と合わせて保存し、どちらの区間も再利用できるようにする
                merged = raw.with_name(raw.name + ".merged")
                provider.merge(raw, tmp, merged)  # type: ignore[attr-defined]
                merged.replace(raw)
                tmp.unlink(missing_ok=True)
                spans = merge_spans([*(old_spans or []), tuple(got)])
                log.info("チャットのキャッシュに区間を追加: %s", spans)
            else:
                tmp.replace(raw)
                spans = None if got is None else [tuple(got)]
            meta.write_text(json.dumps({"provider": provider.name, "video_id": info.id, "spans": spans,
                                        "title": info.title, "fetched_at": time.time(),
                                        "fetch_seconds": round(fetch_s, 2)}, ensure_ascii=False), "utf-8")
        t1 = time.monotonic()
        messages = provider.parse(raw)
        stats = ChatStats(provider.name, hit is not None, fetch_s, time.monotonic() - t1, raw.stat().st_size,
                          len(messages), spans, raw,
                          getattr(provider, "last_time_base", None))
        log.info(stats.summary())
        return messages, stats
