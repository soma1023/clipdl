"""チャットの出力: JSON（元データつき）/ CSV（時刻・ユーザー名・本文）/ ASS（動画に重ねる字幕）。"""
from __future__ import annotations

import csv
import unicodedata
import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..models import ChatMessage, ClipRange
from ..timeutil import format_ass_time, format_time

# ------------------------------------------------------------------ JSON


def write_json(path: Path, messages: list[ChatMessage], *, source: str, video_id: str,
               clip: ClipRange | None) -> Path:
    data = {
        "format": "clipdl-chat/1",
        "source": source,
        "video_id": video_id,
        "clip": None if clip is None else {"index": clip.index, "start": clip.start, "end": clip.end},
        "time_base": "clip" if clip else "video",
        "count": len(messages),
        "messages": [
            {
                "time": round(m.time, 3),
                "time_in_video": round(m.time + (clip.start if clip else 0.0), 3),
                "user": m.user, "user_id": m.user_id, "text": m.text, "color": m.color,
                "kind": m.kind, "amount": m.amount,
                "raw": m.raw,
            } for m in messages
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return path


# ------------------------------------------------------------------ CSV


def write_csv(path: Path, messages: list[ChatMessage]) -> Path:
    # Excel で文字化けしないよう UTF-8（BOM 付き）
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "time_sec", "user", "message", "kind", "amount"])
        for m in messages:
            w.writerow([format_time(max(0.0, m.time), millis=True), f"{m.time:.3f}", m.user, m.text,
                        m.kind, m.amount or ""])
    return path


def write_comments_csv(path: Path, comments: Iterable[dict]) -> Path:
    from datetime import datetime, timezone
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["posted_at", "author", "text", "like_count", "is_pinned", "parent", "id"])
        for c in comments:
            ts = c.get("timestamp")
            posted = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""
            w.writerow([posted, c.get("author", ""), c.get("text", ""), c.get("like_count", ""),
                        c.get("is_pinned", ""), c.get("parent", ""), c.get("id", "")])
    return path


# ------------------------------------------------------------------ ASS（縦流れ・Twitch 風）


@dataclass
class AssStyle:
    play_res_x: int = 1920
    play_res_y: int = 1080
    font: str = "Yu Gothic UI"
    font_size: int = 40
    panel_width_ratio: float = 0.28   # 画面右側のチャット欄の幅
    margin: int = 24
    max_lines: int = 12               # 同時に表示する最大件数
    lifetime: float = 12.0            # 1 件を表示し続ける秒数
    outline: float = 2.5


_PALETTE = ("#FF4A4A", "#4A9DFF", "#3FD06F", "#FFB02E", "#C77DFF", "#FF7AC6", "#2ED3D3", "#FFE14D",
            "#9BE564", "#FF8C42", "#7AA7FF", "#E36BFF")


def user_color(m: ChatMessage) -> str:
    if m.color and len(m.color) == 7 and m.color.startswith("#"):
        return m.color.upper()
    key = (m.user_id or m.user).encode("utf-8")
    return _PALETTE[zlib.crc32(key) % len(_PALETTE)]


def ass_color(hex_rgb: str) -> str:
    """'#RRGGBB' → ASS の '&H00BBGGRR&'。"""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}&".upper()


def ass_escape(text: str) -> str:
    """ASS で特別な意味を持つ文字を無害化する（{ } \\ と改行）。"""
    text = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    return " ".join(text.splitlines()).strip()


def spread_same_time(messages: list[ChatMessage], window: float = 1.0) -> list[ChatMessage]:
    """同じ時刻のメッセージを、その時刻から window 秒の中で均等にずらす（表示用。元の並び順は保つ）。

    Twitch の時刻は 1 秒単位のため、同じ秒のコメントが一斉に出てしまうのを防ぐ。
    n 件なら t, t+1/n, t+2/n, … とし、次の別の時刻を追い越さないよう幅を縮める。
    JSON / CSV には使わない（元の時刻のまま出力する）。
    """
    msgs = sorted(messages, key=lambda m: m.time)  # 安定ソートなので同じ時刻の順番は変わらない
    out: list[ChatMessage] = []
    i = 0
    while i < len(msgs):
        t = msgs[i].time
        j = i
        while j < len(msgs) and abs(msgs[j].time - t) < 1e-6:
            j += 1
        n = j - i
        nxt = msgs[j].time if j < len(msgs) else t + window
        width = max(0.0, min(window, nxt - t))
        for k in range(n):
            out.append(msgs[i + k].shifted(-(width * k / n)) if k else msgs[i + k])
        i = j
    return out


# ASS の Fontsize は行の高さ基準なので、実際の文字幅はそれより狭い。
# Yu Gothic UI で実測（Fontsize 40）: 全角 約 23px（0.58 倍）、半角 約 14.5px（0.36 倍）
WIDE_ADVANCE = 0.6     # 全角 1 文字の幅 / Fontsize（少し大きめにして、はみ出しを防ぐ）
NARROW_RATIO = 0.62    # 半角の幅 / 全角の幅


def char_em(ch: str) -> float:
    """1 文字の幅（全角 1 文字 = 1.0）。"""
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else NARROW_RATIO


def text_em(text: str) -> float:
    return sum(char_em(c) for c in text)


def wrap_em(text: str, first_width: float, width: float) -> list[str]:
    """幅 width（em）で折り返す。libass は空白でしか改行しないため、日本語は自分で区切る。

    1 行目は名前の分だけ狭い（first_width）。英語は可能なら空白で区切る。
    """
    lines: list[str] = []
    cur, cur_w, limit = "", 0.0, max(1.0, first_width)
    for ch in text:
        w = char_em(ch)
        if cur and cur_w + w > limit:
            cut = cur.rfind(" ")
            if cut > 0 and cut >= len(cur) - 12 and all(char_em(c) < 1.0 for c in cur[cut:]):
                lines.append(cur[:cut])
                cur = cur[cut + 1:]
            else:
                lines.append(cur)
                cur = ""
            cur_w = text_em(cur)
            limit = width
        cur += ch
        cur_w += w
    lines.append(cur)
    return lines


def _line(m: ChatMessage, width_em: float | None = None) -> str:
    name = ass_escape(m.user) or "?"
    body = ass_escape(m.text)
    if m.amount:
        body = f"[{ass_escape(m.amount)}] {body}"
    if width_em:
        name_w = text_em(name) * 1.05 + text_em(": ")  # 名前は太字なので少し広め
        body = "\\N".join(wrap_em(body, width_em - name_w, width_em))
    return f"{{\\c{ass_color(user_color(m))}\\b1}}{name}{{\\b0\\c&H00FFFFFF&}}: {body}"


def build_vertical_events(messages: list[ChatMessage], duration: float | None,
                          style: AssStyle) -> list[tuple[float, float, list[ChatMessage]]]:
    """時刻を「表示内容が変わる瞬間」で区切り、各区間に表示するメッセージ（古い順）を返す。

    新しいメッセージは一番下に追加され、古いものは上へ押し上げられる。
    max_lines を超えるか lifetime を過ぎたものは消える。
    """
    msgs = sorted((m for m in messages if m.time >= 0), key=lambda m: m.time)
    if not msgs:
        return []
    end_limit = duration if duration else msgs[-1].time + style.lifetime
    points = set()
    for m in msgs:
        if m.time < end_limit:
            points.add(m.time)
            points.add(min(m.time + style.lifetime, end_limit))
    times = sorted(points)
    events = []
    idx = 0  # まだ表示を始めていない最初のメッセージ
    for a, b in zip(times, times[1:]):
        if b - a < 0.01:
            continue
        while idx < len(msgs) and msgs[idx].time <= a:
            idx += 1
        visible = [m for m in msgs[max(0, idx - style.max_lines * 4):idx]
                   if m.time + style.lifetime > a][-style.max_lines:]
        if visible:
            if events and events[-1][2] == visible and abs(events[-1][1] - a) < 1e-6:
                events[-1] = (events[-1][0], b, visible)
            else:
                events.append((a, b, visible))
    return events


def write_ass_vertical(path: Path, messages: list[ChatMessage], *, duration: float | None,
                       width: int | None = None, height: int | None = None,
                       style: AssStyle | None = None, title: str = "") -> Path:
    style = style or AssStyle()
    if width and height:
        scale = height / style.play_res_y
        style = AssStyle(**{**style.__dict__, "play_res_x": width, "play_res_y": height,
                            "font_size": max(12, round(style.font_size * scale)),
                            "margin": max(4, round(style.margin * scale))})
    panel_w = int(style.play_res_x * style.panel_width_ratio)
    margin_l = style.play_res_x - panel_w - style.margin
    width_em = panel_w / (style.font_size * WIDE_ADVANCE) * 0.95  # 折り返し幅（全角何文字分か）
    header = f"""[Script Info]
; clipdl が生成したチャット字幕（縦流れ）
Title: {ass_escape(title) or 'chat'}
ScriptType: v4.00+
PlayResX: {style.play_res_x}
PlayResY: {style.play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Chat,{style.font},{style.font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{style.outline},0,1,{margin_l},{style.margin},{style.margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for a, b, visible in build_vertical_events(messages, duration, style):
        text = "\\N".join(_line(m, width_em) for m in visible)
        lines.append(f"Dialogue: 0,{format_ass_time(a)},{format_ass_time(b)},Chat,,0,0,0,,{text}\n")
    path.write_text("".join(lines), "utf-8-sig")
    return path


# ------------------------------------------------------------------ ASS（横流れ・ニコニコ風）


@dataclass
class ScrollStyle:
    play_res_x: int = 1920
    play_res_y: int = 1080
    font: str = "Yu Gothic UI"
    font_size: int = 52
    margin: int = 12
    duration: float = 5.0          # 右端から左端まで流れる秒数
    outline: float = 3.0
    line_spacing: float = 1.15


def scroll_events(messages: list[ChatMessage], style: ScrollStyle,
                  duration: float | None) -> list[tuple[float, float, int, float]]:
    """各メッセージの (開始, 終了, 行番号, 文字幅px) を決める。

    前のコメントの末尾が画面に入りきり、かつ追いつかない行に入れる。空きが無ければ一番早く空く行に重ねる。
    """
    w_screen = style.play_res_x
    line_h = style.font_size * style.line_spacing
    lanes = max(1, int((style.play_res_y - 2 * style.margin) // line_h))
    gap = style.font_size * WIDE_ADVANCE   # 同じ行で前後のコメントのあいだに空ける幅（全角 1 文字分）
    lane_last: list[tuple[float, float] | None] = [None] * lanes  # (開始, 幅px)
    out = []
    for m in sorted((m for m in messages if m.time >= 0), key=lambda m: m.time):
        if duration is not None and m.time >= duration:
            break
        text = _scroll_text(m)
        width = text_em(text) * style.font_size * WIDE_ADVANCE
        speed = (w_screen + width) / style.duration

        def free_at(lane: int) -> float:
            prev = lane_last[lane]
            if prev is None:
                return 0.0
            p_start, p_w = prev
            p_speed = (w_screen + p_w) / style.duration
            # 前のコメントの末尾が画面に入り、さらに約 1 文字分の間が空く時刻（幅の見積もりが少し小さくても重ならない）
            tail_in = p_start + (p_w + gap) / p_speed
            no_catch = p_start + style.duration - (w_screen - gap) / speed  # これ以降なら左端で追いつかない
            return max(tail_in, no_catch)

        free = [free_at(i) for i in range(lanes)]
        lane = next((i for i in range(lanes) if free[i] <= m.time), min(range(lanes), key=lambda i: free[i]))
        lane_last[lane] = (m.time, width)
        out.append((m.time, m.time + style.duration, lane, width))
    return out


def _scroll_text(m: ChatMessage) -> str:
    text = " ".join(m.text.split())
    return f"[{m.amount}] {text}" if m.amount else text


def write_ass_scroll(path: Path, messages: list[ChatMessage], *, duration: float | None,
                     width: int | None = None, height: int | None = None,
                     style: ScrollStyle | None = None, title: str = "") -> Path:
    """ニコニコ風: コメントが右から左へ流れる。ユーザー名は出さない（スーパーチャット等は色付き）。"""
    style = style or ScrollStyle()
    if width and height:
        scale = height / style.play_res_y
        style = ScrollStyle(**{**style.__dict__, "play_res_x": width, "play_res_y": height,
                               "font_size": max(12, round(style.font_size * scale)),
                               "margin": max(2, round(style.margin * scale))})
    line_h = style.font_size * style.line_spacing
    header = f"""[Script Info]
; clipdl が生成したチャット字幕（横流れ）
Title: {ass_escape(title) or 'chat'}
ScriptType: v4.00+
PlayResX: {style.play_res_x}
PlayResY: {style.play_res_y}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Scroll,{style.font},{style.font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{style.outline},0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    msgs = sorted(messages, key=lambda m: m.time)
    lines = [header]
    for (start, end, lane, w), m in zip(scroll_events(msgs, style, duration), [x for x in msgs if x.time >= 0]):
        y = round(style.margin + lane * line_h)
        color = f"\\c{ass_color(user_color(m))}" if m.kind != "text" else ""
        text = ass_escape(_scroll_text(m))
        lines.append(f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},Scroll,,0,0,0,,"
                     f"{{\\move({style.play_res_x},{y},{-round(w) - 10},{y}){color}}}{text}\n")
    path.write_text("".join(lines), "utf-8-sig")
    return path


def write_ass(path: Path, messages: list[ChatMessage], *, kind: str = "vertical", **kw) -> Path:
    """kind: "vertical"（縦流れ・Twitch 風）/ "scroll"（横流れ・ニコニコ風）"""
    if kind == "scroll":
        return write_ass_scroll(path, messages, **kw)
    return write_ass_vertical(path, messages, **kw)
