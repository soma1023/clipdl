"""スタイル「Twitch配信風」: 名前の色・日本語の折り返し・chatrender 用 JSON の変換。描画（外部 exe）は使わない。"""
import json

import pytest

from clipdl.core.chat import twitch_style as ts
from clipdl.core.chat.overlay import OverlayOptions, VideoGeometry
from clipdl.core.chat.twitch import merge_embedded
from clipdl.core.models import ChatMessage

# ------------------------------------------------------------------------ 名前の色


@pytest.mark.parametrize("color", ["#FF0000", "#FF7F50", "#1E90FF", "#9ACD32", "#FFFFFF", "#00FF7F"])
def test_readable_colors_are_kept_as_is(color):
    assert ts.readable_color(color) == color


@pytest.mark.parametrize("color", ["#0000FF", "#008000", "#B22222", "#1E1E1E", "#8A2BE2"])
def test_too_dark_colors_are_lifted_keeping_hue(color):
    out = ts.readable_color(color)
    r, g, b = (int(out[i:i + 2], 16) / 255 for i in (1, 3, 5))
    assert out != color and ts.luminance(r, g, b) >= ts.MIN_LUMINANCE - 0.01


def test_stable_color_is_deterministic():
    assert ts.stable_color("user-1") == ts.stable_color("user-1")
    assert {ts.stable_color(f"u{i}") for i in range(200)} <= set(ts.PALETTE)


def _tw_comment(name, color=None, uid="1", text="hi"):
    return {"_id": "c" + uid, "content_offset_seconds": 10, "commenter": {"display_name": name, "_id": uid},
            "message": {"body": text, "fragments": [{"text": text, "emoticon": None}], "user_badges": [],
                        "user_color": color}}


def test_twitch_keeps_user_color_and_fixes_missing_ones():
    msgs = [ChatMessage(10, "a", "hi", raw=_tw_comment("a", "#FF0000", "1")),
            ChatMessage(11, "b", "hi", raw=_tw_comment("b", "#0000FF", "2")),
            ChatMessage(12, "c", "hi", raw=_tw_comment("c", None, "3"))]
    data, _ = ts.build_render_json(msgs, "twitch")
    colors = [c["message"]["user_color"] for c in data["comments"]]
    assert colors[0] == "#FF0000"                                  # 本人の色（読める）はそのまま
    assert colors[1] == ts.readable_color("#0000FF") != "#0000FF"   # 暗すぎる色だけ明るく
    assert colors[2] == ts.readable_color(ts.stable_color("3"))     # 未設定は ID から固定で
    data2, _ = ts.build_render_json(msgs, "twitch")
    assert [c["message"]["user_color"] for c in data2["comments"]] == colors


# ------------------------------------------------------------------------ 折り返し


def _visible(s: str) -> str:
    return s.replace(ts.BREAK, "").replace(ts.ZW, "")


def test_breaks_between_japanese_but_not_inside_words():
    s = ts.make_breakable("魚釣りあげてから hello-world https://ex.com/a?b=1 KEKW 草")
    parts = [p for p in s.split(ts.BREAK) if p]
    assert "hello-world" in parts and "https://ex.com/a?b=1" in parts and "KEKW" in parts
    assert "魚" in parts and "釣" in parts
    assert _visible(s) == "魚釣りあげてからhello-worldhttps://ex.com/a?b=1KEKW草"   # 文字は欠けない


def test_no_break_before_closing_punctuation_or_small_kana():
    s = ts.make_breakable("「えっ」、ちょっと！？ｗ")
    parts = [p for p in s.split(ts.BREAK) if p]
    # 「 の後、っ 」 、 ！ ？ の前では切らない
    assert parts == ["「えっ」、", "ちょっ", "と！？", "ｗ"]


def _gap_units(gap: str) -> float:
    """単語と単語の間の文字列が、chatrender で空白何個分の幅になるか。

    chatrender は空白（区切り）で分けた空でないかたまりごとに単語の間隔（1/4）を足す。
    直前の単語の分の 1/4 ＋ 間にある幅 0 のかたまりの数 × 1/4。
    """
    parts = [p for p in gap.split(ts.BREAK) if p]
    assert all(set(p) == {ts.ZW} for p in parts)   # 間にあるのは幅 0 の文字だけ
    return ts.WORD_SPACE_SCALE * (len(parts) + 1)


@pytest.mark.parametrize("text,word_a,word_b,units", [
    ("好きじゃないのか w", "か", "w", 1),        # 半角の空白 1 つ
    ("あああ　w", "あ", "w", 4),                 # 全角の空白は日本語 1 文字分（半角 4 つ分）
    ("hello  wxyz", "o", "w", 2),              # 連続する空白もまとめない
    ("abc 　def", "c", "d", 5),                # 半角＋全角
])
def test_original_spaces_keep_count_and_width(text, word_a, word_b, units):
    s = ts.make_breakable(text)
    gap = s[s.rindex(word_a) + 1: s.index(word_b, s.rindex(word_a))]
    assert _gap_units(gap) == units
    assert ts.BREAK in gap                     # 空白の位置では折り返せる


def test_no_space_is_added_where_the_original_has_none():
    # YouTube は「好きじゃないのか」「w」のように空白なしで分けて送ることがある（元の文には空白が無い）
    assert _visible(ts.make_breakable("のか")) == "のか"
    assert ts.make_breakable("w") == "w"


def test_space_only_fragments_untouched_and_lead_pad():
    assert ts.make_breakable(" ") == " "         # エモートの間の空白はそのまま
    assert ts.make_breakable("あ", lead=True).startswith(ts.PAD)
    assert _gap_units(ts.PAD) == 1.0             # 名前の後ろの間隔 1/4 ＋ PAD ＝ 空白 1 つ分


def test_build_adds_breaks_and_lead_pad():
    msgs = [ChatMessage(10, "a", "長い日本語", raw=_tw_comment("a", "#FF0000", text="長い日本語"))]
    data, _ = ts.build_render_json(msgs, "twitch")
    text = data["comments"][0]["message"]["fragments"][0]["text"]
    assert text.startswith(ts.PAD) and ts.BREAK in text and _visible(text) == "長い日本語"


# ------------------------------------------------------------------------ YouTube の変換


def _yt(renderer_name, renderer, **kw):
    return ChatMessage(kw.pop("time", 100.0), kw.pop("user", "@名前"), kw.pop("text", ""),
                       raw={renderer_name: renderer}, **kw)


class FakeImages(ts.ImageStore):
    def __init__(self, tmp_path, fail=()):
        self.fail = set(fail)
        super().__init__(tmp_path, fetch=self._f)

    def _f(self, url):
        if url in self.fail:
            raise OSError("404")
        return b"\x89PNG" + url.encode()


def test_youtube_member_emoji_badges_and_at_sign(tmp_path):
    r = {"id": "m1", "authorExternalChannelId": "UC1",
         "message": {"runs": [{"text": "こんにちは "},
                              {"emoji": {"emojiId": "UC1/abc", "isCustomEmoji": True, "shortcuts": [":yay:"],
                                         "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/e1", "width": 48}]}}},
                              {"emoji": {"emojiId": "UC1/zzz", "isCustomEmoji": True, "shortcuts": [":ng:"],
                                         "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/bad"}]}}},
                              {"emoji": {"emojiId": "😀", "shortcuts": [":grin:"]}}]},
         "authorBadges": [
             {"liveChatAuthorBadgeRenderer": {"customThumbnail": {"thumbnails": [
                 {"url": "https://yt3.ggpht.com/b1", "width": 32}]}, "tooltip": "メンバー（1 か月）"}},
             {"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "MODERATOR"}, "tooltip": "モデレーター"}}]}
    imgs = FakeImages(tmp_path, fail={"https://yt3.ggpht.com/bad"})
    data, st = ts.build_render_json([_yt("liveChatTextMessageRenderer", r, user_id="UC1")], "youtube",
                                    images=imgs, start=90, end=120)
    c = data["comments"][0]
    assert c["commenter"]["display_name"] == "名前"                     # @ を外す
    frags = c["message"]["fragments"]
    emo = [f for f in frags if f.get("emoticon")]
    assert len(emo) == 1 and data["embeddedData"]["firstParty"][0]["name"] == ":yay:"
    assert any(":ng:" in _visible(f["text"]) for f in frags if not f.get("emoticon"))   # 取れない絵文字は文字で
    assert any("😀" in f["text"] for f in frags)                         # 標準の絵文字は Unicode の文字で
    assert len(c["message"]["user_badges"]) == 2 and st["badges"] == 2 and st["image_failed"] == 1
    assert c["content_offset_seconds"] == 100.0
    json.dumps(data)   # そのまま JSON にできる


def test_youtube_superchat_and_membership_prefix(tmp_path):
    paid = _yt("liveChatPaidMessageRenderer", {"id": "p", "message": {"runs": [{"text": "ありがとう"}]}},
               kind="superchat", amount="¥1,000", user_id="U2")
    mem = _yt("liveChatMembershipItemRenderer", {"id": "q", "headerSubtext": {"runs": [{"text": "新規メンバー"}]}},
              kind="membership", user_id="U3")
    data, _ = ts.build_render_json([paid, mem], "youtube", images=FakeImages(tmp_path))
    texts = [_visible("".join(f["text"] for f in c["message"]["fragments"])) for c in data["comments"]]
    assert texts[0].startswith("[¥1,000]") and "ありがとう" in texts[0]
    assert texts[1].startswith("[新規メンバー]")


def test_image_store_only_fetches_allowed_hosts_and_caches(tmp_path):
    calls = []
    store = ts.ImageStore(tmp_path, fetch=lambda u: calls.append(u) or b"img")
    assert store.get("https://evil.example.com/x.png") is None
    assert store.get("http://yt3.ggpht.com/x") is None          # https だけ
    assert store.get("https://yt3.ggpht.com/x") == b"img"
    assert store.get("https://yt3.ggpht.com/x") == b"img" and calls == ["https://yt3.ggpht.com/x"]


def test_bundled_badge_icons_exist():
    for f in ts._ICON_BADGES.values():
        assert (ts.BADGE_DIR / f).read_bytes()[:4] == b"\x89PNG"


# ------------------------------------------------------------------------ そのほか


def test_merge_embedded_unions_without_duplicates():
    a = {"firstParty": [{"id": "1"}], "thirdParty": [{"id": "b1"}], "twitchBadges": [{"name": "sub"}]}
    b = {"firstParty": [{"id": "1"}, {"id": "2"}], "twitchBadges": [{"name": "vip"}]}
    m = merge_embedded(a, b)
    assert [e["id"] for e in m["firstParty"]] == ["1", "2"]
    assert {x["name"] for x in m["twitchBadges"]} == {"sub", "vip"} and m["thirdParty"] == [{"id": "b1"}]
    assert merge_embedded(None, None) is None


def test_geometry_scales_with_clip_height():
    o = ts.TwitchStyleOptions()
    assert ts.geometry_for(VideoGeometry(1920, 1080, "60", 10), o) == (420, 1080, 22)
    assert ts.geometry_for(VideoGeometry(1280, 720, "30", 10), o) == (280, 720, 15)


def test_output_args_have_alpha():
    assert ts.output_args("qtrle").startswith("-c:v qtrle -pix_fmt argb")
    assert "prores_ks" in ts.output_args("prores") and ts.output_args("png").endswith('"{save_path}"')


def test_style_options_from_settings():
    from clipdl.core.pipeline import style_options
    from clipdl.core.settings import Settings
    s = Settings()
    o = style_options(s)
    assert o["chat_style"] == "twitch" and o["ass_style"] == "vertical" and o["overlay"].crop is False
    s.twitch_style_bg_opacity = 3
    assert style_options(s)["twitch_style"].bg_opacity == 1.0
    assert style_options(s, "scroll")["ass_style"] == "scroll"
    assert style_options(s, "vertical")["overlay"].crop is True


def test_estimate_is_positive():
    assert ts.estimate_size_mb(60, 1080, overlay=OverlayOptions()) > 30
