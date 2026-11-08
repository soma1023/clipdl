import re

from clipdl.core.chat.export import (AssStyle, ass_color, ass_escape, build_vertical_events, user_color,
                                     write_ass_vertical)
from clipdl.core.models import ChatMessage


def m(t, user="u", text="hi", **kw):
    return ChatMessage(t, user, text, **kw)


def test_ass_color_is_bgr():
    assert ass_color("#112233") == "&H00332211&"
    assert ass_color("#ff0000") == "&H000000FF&"


def test_ass_escape_neutralizes_override_and_newlines():
    assert ass_escape("{\\b1}x\ny\\N") == "｛＼b1｝x y＼N"


def test_user_color_prefers_given_and_is_stable():
    assert user_color(m(0, color="#abcdef")) == "#ABCDEF"
    a1, a2 = user_color(m(0, user="alice")), user_color(m(5, user="alice"))
    assert a1 == a2 and re.fullmatch(r"#[0-9A-F]{6}", a1)


def test_events_stack_newest_last_and_expire():
    style = AssStyle(lifetime=10, max_lines=10)
    ev = build_vertical_events([m(0, text="a"), m(2, text="b"), m(20, text="c")], duration=40, style=style)
    shown = [(a, b, [x.text for x in v]) for a, b, v in ev]
    assert shown == [(0, 2, ["a"]), (2, 10, ["a", "b"]), (10, 12, ["b"]), (20, 30, ["c"])]


def test_events_respect_max_lines():
    style = AssStyle(lifetime=100, max_lines=3)
    ev = build_vertical_events([m(i, text=str(i)) for i in range(5)], duration=10, style=style)
    last = [x.text for x in ev[-1][2]]
    assert last == ["2", "3", "4"]
    assert all(len(v) <= 3 for _, _, v in ev)


def test_events_clamped_to_duration_and_skip_negative():
    style = AssStyle(lifetime=10)
    ev = build_vertical_events([m(-1), m(5)], duration=8, style=style)
    assert ev == [(5, 8, ev[0][2])] and len(ev[0][2]) == 1


def test_events_same_timestamp():
    ev = build_vertical_events([m(1, text="a"), m(1, text="b")], duration=5, style=AssStyle(lifetime=10))
    assert [[x.text for x in v] for _, _, v in ev] == [["a", "b"]]


def test_events_empty():
    assert build_vertical_events([], duration=10, style=AssStyle()) == []


def test_write_ass_file(tmp_path):
    p = write_ass_vertical(tmp_path / "c.ass",
                           [m(1.0, user="Alice", text="hello {x}", color="#FF0000"), m(3.5, user="B", text="yo")],
                           duration=60, width=1280, height=720, title="T")
    text = p.read_text("utf-8-sig")
    assert "PlayResX: 1280" in text and "PlayResY: 720" in text
    assert "[V4+ Styles]" in text and "[Events]" in text
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert dialogues[0].startswith("Dialogue: 0,0:00:01.00,0:00:03.50,Chat,")
    assert "{\\c&H000000FF&\\b1}Alice" in dialogues[0]
    assert "hello ｛x｝" in dialogues[0]
    assert "\\N" in dialogues[1]  # 2 件目で 2 行表示
    # スタイル行の列数がフォーマット行と一致する
    fmt = next(l for l in text.splitlines() if l.startswith("Format: Name"))
    sty = next(l for l in text.splitlines() if l.startswith("Style: Chat"))
    assert len(fmt.split(",")) == len(sty.split(","))


def test_ass_events_do_not_overlap(tmp_path):
    msgs = [m(t * 0.37, text=str(t)) for t in range(200)]
    ev = build_vertical_events(msgs, duration=80, style=AssStyle())
    for (a1, b1, _), (a2, _, _) in zip(ev, ev[1:]):
        assert a1 < b1 <= a2 + 1e-9


def test_scroll_comments_in_same_lane_keep_a_gap():
    """ニコニコ風: 同じ行に入れた次のコメントは、前のコメントと全角 1 文字分以上離れている（くっつかない）。"""
    from clipdl.core.chat.export import WIDE_ADVANCE, ScrollStyle, scroll_events
    style = ScrollStyle(play_res_y=144)          # 2 行だけにして、同じ行に入る場面を作る
    gap = style.font_size * WIDE_ADVANCE
    w = style.play_res_x

    def x_at(e, t):   # コメントの左端の x
        start, end, _lane, width = e
        return w - (w + width) * (t - start) / (end - start)

    checked = 0
    for first in ("空の色きれいすぎる、背景美術ほんとすごい", "88888888", "GG"):
        for second in ("Thanks for watching! 次回もよろしく", "8888", "このシーン何回見ても好き"):
            for k in range(1, 100):
                a, b = scroll_events([ChatMessage(0.0, "u", first), ChatMessage(k * 0.05, "u", second)], style, None)
                if b[2] != a[2]:
                    continue                     # 別の行に入った
                checked += 1
                for j in range(0, 41):
                    tt = b[0] + (a[1] - b[0]) * j / 40
                    assert x_at(b, tt) - (x_at(a, tt) + a[3]) >= gap - 1e-6, (first, second, k)
    assert checked > 0
