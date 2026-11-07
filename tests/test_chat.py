import csv
import json

import pytest

from clipdl.core.chat.cache import ChatCache, covers, union_span
from clipdl.core.chat.clipping import clip_messages
from clipdl.core.chat.export import write_csv, write_json
from clipdl.core.chat.twitch import parse_twitch_json
from clipdl.core.chat.youtube_live import last_offset_ms, parse_lines
from clipdl.core.models import ChatMessage, ClipRange, VideoInfo


def m(t, user="u", text="hi"):
    return ChatMessage(t, user, text)


# ---------------------------------------------------------------- 範囲抽出

def test_clip_messages_range_and_rebase():
    msgs = [m(t) for t in (5, 9.999, 10, 15.5, 19.999, 20, 30)]
    out = clip_messages(msgs, ClipRange(10, 20))
    assert [x.time for x in out] == pytest.approx([0, 5.5, 9.999])


def test_clip_messages_boundaries_start_inclusive_end_exclusive():
    msgs = [m(10), m(20)]
    assert [x.time for x in clip_messages(msgs, ClipRange(10, 20))] == [0]


def test_clip_messages_unsorted_input():
    msgs = [m(15), m(11), m(13)]
    assert [x.time for x in clip_messages(msgs, ClipRange(10, 20))] == [1, 3, 5]


def test_clip_messages_does_not_mutate():
    src = [m(12)]
    clip_messages(src, ClipRange(10, 20))
    assert src[0].time == 12


def test_clip_messages_empty():
    assert clip_messages([], ClipRange(0, 10)) == []
    assert clip_messages([m(50)], ClipRange(0, 10)) == []


def test_multiple_clips_from_one_source():
    msgs = [m(t) for t in range(0, 100, 5)]
    a = clip_messages(msgs, ClipRange(0, 10, 1))
    b = clip_messages(msgs, ClipRange(50, 62, 2))
    assert [x.time for x in a] == [0, 5]
    assert [x.time for x in b] == [0, 5, 10]


# ---------------------------------------------------------------- YouTube ライブチャット

def _yt_line(offset_ms, renderer, body):
    return json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {renderer: body}}}],
                                                "videoOffsetTimeMsec": str(offset_ms)}})


def test_parse_youtube_live_chat():
    lines = [
        _yt_line(65000, "liveChatTextMessageRenderer", {
            "authorName": {"simpleText": "Bob"}, "authorExternalChannelId": "UC2",
            "message": {"runs": [{"text": "late "}, {"emoji": {"emojiId": "😀", "shortcuts": [":grin:"]}}]}}),
        _yt_line(1500, "liveChatTextMessageRenderer", {
            "authorName": {"simpleText": "Alice"}, "message": {"runs": [{"text": "hello"}]}}),
        _yt_line(2000, "liveChatPaidMessageRenderer", {
            "authorName": {"simpleText": "Rich"}, "purchaseAmountText": {"simpleText": "¥1,000"},
            "bodyBackgroundColor": 4280150454, "message": {"runs": [{"text": "スパチャ"}]}}),
        _yt_line(3000, "liveChatTextMessageRenderer", {
            "authorName": {"simpleText": "C"},
            "message": {"runs": [{"emoji": {"emojiId": "UCxx/abc", "isCustomEmoji": True,
                                            "shortcuts": [":_custom:"]}}]}}),
        _yt_line(4000, "liveChatViewerEngagementMessageRenderer", {"message": {"runs": [{"text": "ignored"}]}}),
        "not json",
        "",
    ]
    msgs = parse_lines(lines)
    assert [(x.time, x.user, x.text, x.kind) for x in msgs] == [
        (1.5, "Alice", "hello", "text"),
        (2.0, "Rich", "スパチャ", "superchat"),
        (3.0, "C", ":_custom:", "text"),
        (65.0, "Bob", "late 😀", "text"),
    ]
    assert msgs[1].amount == "¥1,000"
    assert msgs[1].color == "#1DE9B6"
    assert msgs[3].user_id == "UC2"


def test_last_offset_ms(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(_yt_line(1000, "liveChatTextMessageRenderer", {}) + "\n" +
                 _yt_line(123456, "liveChatTextMessageRenderer", {}) + "\n", "utf-8")
    assert last_offset_ms(p) == 123456
    assert last_offset_ms(tmp_path / "missing") is None


# ---------------------------------------------------------------- Twitch

def test_parse_twitch_json():
    data = {"comments": [
        {"content_offset_seconds": 12.5, "commenter": {"display_name": "Zed", "_id": "9"},
         "message": {"body": "pog", "user_color": "#FF0000", "bits_spent": 0}},
        {"content_offset_seconds": 3, "commenter": {"name": "anon"},
         "message": {"fragments": [{"text": "a"}, {"text": "b"}], "bits_spent": 100}},
        {"content_offset_seconds": None, "commenter": {}, "message": {}},
    ]}
    msgs = parse_twitch_json(data)
    assert [(x.time, x.user, x.text, x.kind) for x in msgs] == [(3.0, "anon", "ab", "bits"),
                                                                 (12.5, "Zed", "pog", "text")]
    assert msgs[0].amount == "100 bits"
    assert msgs[1].color == "#FF0000"


# ---------------------------------------------------------------- 出力

def test_write_csv(tmp_path):
    p = write_csv(tmp_path / "c.csv", [ChatMessage(61.25, "名前", "こんにちは, 世界", amount="¥500")])
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel 用 BOM
    rows = list(csv.reader(p.open(encoding="utf-8-sig")))
    assert rows[0] == ["time", "time_sec", "user", "message", "kind", "amount"]
    assert rows[1] == ["00:01:01.250", "61.250", "名前", "こんにちは, 世界", "text", "¥500"]


def test_write_json_keeps_video_time(tmp_path):
    clip = ClipRange(100, 200, 1)
    msgs = clip_messages([ChatMessage(150.5, "a", "x", raw={"k": 1})], clip)
    data = json.loads(write_json(tmp_path / "c.json", msgs, source="s", video_id="v", clip=clip).read_text("utf-8"))
    assert data["time_base"] == "clip"
    assert data["messages"][0]["time"] == 50.5
    assert data["messages"][0]["time_in_video"] == 150.5
    assert data["messages"][0]["raw"] == {"k": 1}


# ---------------------------------------------------------------- キャッシュ

def test_covers_and_union():
    assert covers(None, (1, 2)) and covers(None, None)
    assert covers((0, 100), (10, 20))
    assert not covers((0, 100), (10, 120))
    assert not covers((0, 100), None)
    assert union_span([(50, 60), (10, 20)]) == (10, 60)
    assert union_span([]) is None


class _FakeProvider:
    name = "fake"
    raw_suffix = ".json"

    def __init__(self):
        self.calls = 0

    def fetch(self, info, span, dest, on_progress, cancel):
        self.calls += 1
        dest.write_text(json.dumps([[10, "a"], [50, "b"]]), "utf-8")
        return span

    def parse(self, path):
        return [ChatMessage(t, u, "x") for t, u in json.loads(path.read_text("utf-8"))]


def _info():
    return VideoInfo("u", "vid/1", "twitch:vod", "t", "c", 100, None, None, None, False, 30, 1920, 1080)


def test_cache_reuses_when_covered(tmp_path):
    cache, prov = ChatCache(tmp_path), _FakeProvider()
    _, first = cache.get(prov, _info(), (0, 60))
    msgs, stats = cache.get(prov, _info(), (5, 55))
    assert prov.calls == 1 and len(msgs) == 2
    assert stats.cache_hit and stats.fetch_seconds == 0 and stats.raw_bytes > 0 and stats.messages == 2
    assert "キャッシュを使用" in stats.summary()
    assert not first.cache_hit
    _, again = cache.get(prov, _info(), (0, 90))  # 範囲外なので取り直す
    assert prov.calls == 2 and not again.cache_hit


# ---------------------------------------------------------------- Twitch の時刻基準（-b/-e 使用時）

def _tw(start, offsets):
    return {"video": {"start": start, "end": start + 600},
            "comments": [{"content_offset_seconds": o, "commenter": {"display_name": f"u{i}"},
                          "message": {"body": f"m{i}"}} for i, o in enumerate(offsets)]}


def test_twitch_vod_based_offsets_kept():
    from clipdl.core.chat.twitch import detect_time_base
    data = _tw(3600, [3600.4, 3605, 3700])  # TwitchDownloaderCLI 1.56.5 の実際の形（VOD 先頭基準）
    assert detect_time_base(data) == ("vod", 0.0)
    assert [m.time for m in parse_twitch_json(data)] == [3600.4, 3605, 3700]


def test_twitch_range_based_offsets_are_shifted():
    from clipdl.core.chat.twitch import detect_time_base
    data = _tw(3600, [0.4, 5, 100])  # 仮に範囲基準で返ってきた場合
    assert detect_time_base(data) == ("range", 3600.0)
    assert [m.time for m in parse_twitch_json(data)] == [3600.4, 3605, 3700]


def test_twitch_no_trim():
    from clipdl.core.chat.twitch import detect_time_base
    assert detect_time_base(_tw(0, [1, 2]))[0] == "vod"


@pytest.mark.parametrize("offsets", [[3600.4, 3605, 3700], [0.4, 5, 100]])
def test_twitch_ass_is_clip_zero_based_in_both_cases(tmp_path, offsets):
    """どちらの時刻基準でも、ASS はクリップ開始（3600 秒）を 0 とした時刻になる。"""
    from clipdl.core.chat.export import write_ass_vertical
    msgs = clip_messages(parse_twitch_json(_tw(3600, offsets)), ClipRange(3600, 3660))
    assert [round(x.time, 3) for x in msgs] == [0.4, 5.0]
    text = write_ass_vertical(tmp_path / "c.ass", msgs, duration=60).read_text("utf-8-sig")
    starts = [l.split(",")[1] for l in text.splitlines() if l.startswith("Dialogue:")]
    assert starts[0] == "0:00:00.40" and "0:00:05.00" in starts


def test_merge_spans_and_covers_list():
    from clipdl.core.chat.cache import merge_spans
    assert merge_spans([(50, 60), (0, 10), (10, 20), (55, 70)]) == [(0, 20), (50, 70)]
    assert covers([(0, 20), (50, 70)], (52, 60))
    assert not covers([(0, 20), (50, 70)], (15, 55))  # 間の穴にかかる
    assert covers([[0, 20]], (5, 6))  # meta から読んだリスト形式


class _MergingProvider(_FakeProvider):
    def fetch(self, info, span, dest, on_progress, cancel):
        self.calls += 1
        dest.write_text(json.dumps([[span[0] + 1, f"s{span[0]}"]]), "utf-8")
        return span

    def merge(self, old, new, dest):
        a, b = json.loads(old.read_text("utf-8")), json.loads(new.read_text("utf-8"))
        dest.write_text(json.dumps(sorted(a + b)), "utf-8")


def test_cache_keeps_old_span_when_fetching_outside(tmp_path):
    """区間の外を取り直しても、前に取った区間は消えずに再利用できる（実データで見つかった問題）。"""
    cache, prov = ChatCache(tmp_path), _MergingProvider()
    cache.get(prov, _info(), (3600, 5460))
    _, st = cache.get(prov, _info(), (7200, 7260))
    assert prov.calls == 2 and st.span == [(3600, 5460), (7200, 7260)]
    msgs, st = cache.get(prov, _info(), (4200, 4320))  # 1 回目の区間の内側
    assert prov.calls == 2 and st.cache_hit and len(msgs) == 2


def test_merge_twitch_json_dedupes_and_normalizes():
    from clipdl.core.chat.twitch import merge_twitch_json
    a = {"video": {"start": 3600, "end": 5460},
         "comments": [{"_id": "x", "content_offset_seconds": 3700, "commenter": {}, "message": {"body": "a"}}]}
    b = {"video": {"start": 3650, "end": 7260},  # 範囲基準で返ってきたと仮定（3650 + 50 = 3700 で重複）
         "comments": [{"_id": "x", "content_offset_seconds": 50, "commenter": {}, "message": {"body": "a"}},
                      {"_id": "y", "content_offset_seconds": 3600, "commenter": {}, "message": {"body": "b"}}]}
    m = merge_twitch_json(a, b)
    assert [c["_id"] for c in m["comments"]] == ["x", "y"]
    assert m["video"]["start"] == 3600 and m["video"]["end"] == 7260
    assert [x.time for x in parse_twitch_json(m)] == [3700, 7250]
