"""ファイル名テンプレート {date}_{channel}_{title} の展開と、使えない文字の処理。"""
from __future__ import annotations

import re
import unicodedata
from string import Formatter

from .models import ClipRange, VideoInfo

FIELDS = {"date", "channel", "title", "id", "site", "clip", "start", "end"}

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_STEM = 150


class TemplateError(ValueError):
    pass


def sanitize(name: str, max_len: int = MAX_STEM) -> str:
    """Windows / macOS で使えない文字を全角などに置き換える。"""
    name = unicodedata.normalize("NFC", name)
    table = {'<': '＜', '>': '＞', ':': '：', '"': '”', '/': '／', '\\': '＼', '|': '｜', '?': '？', '*': '＊'}
    name = "".join(table.get(c, c) for c in name)
    name = _FORBIDDEN.sub("", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if name.upper().split(".")[0] in _RESERVED:
        name = "_" + name
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return name or "untitled"


def validate_template(template: str) -> None:
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name is not None and field_name not in FIELDS:
            raise TemplateError(f"ファイル名テンプレートに使えない項目です: {{{field_name}}}"
                                f"（使える項目: {', '.join(sorted(FIELDS))}）")


def render(template: str, info: VideoInfo, clip: ClipRange | None = None) -> str:
    """テンプレートを展開してファイル名の stem（拡張子なし）を返す。範囲ありなら末尾にクリップ識別子を付ける。"""
    validate_template(template)
    from .timeutil import format_time
    values = {
        "date": info.upload_date or "nodate",
        "channel": info.channel or "unknown",
        "title": info.title or info.id,
        "id": info.id,
        "site": info.site,
        "clip": clip.tag() if clip else "",
        "start": format_time(clip.start, sep="") if clip else "",
        "end": format_time(clip.end, sep="") if clip else "",
    }
    # 各値の中の禁止文字を先に処理する（テンプレートの区切りは残す）
    values = {k: sanitize(v, 100) if v else "" for k, v in values.items()}
    stem = template.format(**values)
    if clip and "{clip}" not in template and "{start}" not in template:
        stem = f"{stem}_{clip.tag()}"
    return sanitize(stem)
