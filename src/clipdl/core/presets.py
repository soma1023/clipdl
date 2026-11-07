"""画質・形式のプリセット。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    name_ja: str
    name_en: str
    format: str            # yt-dlp の format 指定
    merge_ext: str | None  # 映像＋音声をまとめる形式
    audio_only: bool = False
    audio_ext: str | None = None
    format_sort: tuple[str, ...] = ()


PRESETS: dict[str, Preset] = {
    # 編集ソフトで扱いやすい H.264 + AAC の MP4 を優先。無ければ 1080p 以下の最良を MP4 にまとめる
    "edit1080": Preset(
        "edit1080", "編集用 1080p MP4", "Editing 1080p MP4",
        "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080][vcodec^=avc1]+ba/"
        "bv*[height<=1080]+ba/b[height<=1080]/b",
        "mp4",
        format_sort=("res:1080", "vcodec:h264", "acodec:aac"),
    ),
    "best": Preset("best", "最高画質", "Best quality", "bv*+ba/b", "mkv"),
    "audio": Preset("audio", "音声のみ", "Audio only", "ba[acodec^=mp4a]/ba/b", None,
                    audio_only=True, audio_ext="m4a"),
}


def get_preset(key: str) -> Preset:
    try:
        return PRESETS[key]
    except KeyError:
        raise ValueError(f"不明なプリセットです: {key}（{', '.join(PRESETS)}）") from None
