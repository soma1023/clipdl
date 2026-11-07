"""表示時刻の分散（Twitch）と、オーバーレイ動画（ProRes 4444・背景透明）の書き出し。"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from clipdl.core.chat.clipping import clip_messages
from clipdl.core.chat.export import spread_same_time, text_em, wrap_em, write_ass_vertical, write_csv, write_json
from clipdl.core.chat.overlay import (OverlayOptions, VideoGeometry, ass_margin_l, crop_box, estimate_size_mb,
                                      format_size, render_overlay)
from clipdl.core.models import ChatMessage, ClipRange
from clipdl.core.paths import find_binary


def m(t, text):
    return ChatMessage(t, "u", text)


# ---------------------------------------------------------------- 分散

def test_spread_same_second_evenly():
    out = spread_same_time([m(10, "a"), m(10, "b"), m(10, "c"), m(10, "d"), m(12, "e")])
    assert [(x.text, round(x.time, 3)) for x in out] == [("a", 10), ("b", 10.25), ("c", 10.5), ("d", 10.75),
                                                         ("e", 12)]


def test_spread_keeps_original_order_and_single_untouched():
    out = spread_same_time([m(5, "x"), m(3, "p"), m(3, "q")])
    assert [(x.text, round(x.time, 3)) for x in out] == [("p", 3), ("q", 3.5), ("x", 5)]


def test_spread_does_not_overtake_next_message():
    # 次の別の時刻が 0.5 秒後なら、その幅の中でずらす
    out = spread_same_time([m(1.0, "a"), m(1.0, "b"), m(1.5, "c")])
    assert [round(x.time, 3) for x in out] == [1.0, 1.25, 1.5]
    assert [x.time for x in out] == sorted(x.time for x in out)


def test_spread_does_not_mutate_input():
    src = [m(1, "a"), m(1, "b")]
    spread_same_time(src)
    assert [x.time for x in src] == [1, 1]


def test_json_csv_keep_original_time_while_ass_is_spread(tmp_path):
    """JSON / CSV は元の時刻、ASS だけずらした時刻になる（パイプラインと同じ使い方）。"""
    msgs = clip_messages([m(3601, "a"), m(3601, "b")], ClipRange(3600, 3660))
    j = json.loads(write_json(tmp_path / "c.json", msgs, source="t", video_id="v", clip=None).read_text("utf-8"))
    assert [x["time"] for x in j["messages"]] == [1, 1]
    rows = (write_csv(tmp_path / "c.csv", msgs)).read_text("utf-8-sig").splitlines()[1:]
    assert [r.split(",")[0] for r in rows] == ["00:00:01.000", "00:00:01.000"]
    ass = write_ass_vertical(tmp_path / "c.ass", spread_same_time(msgs), duration=60).read_text("utf-8-sig")
    starts = [l.split(",")[1] for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert starts[:2] == ["0:00:01.00", "0:00:01.50"]


# ---------------------------------------------------------------- オーバーレイ動画

needs_ffmpeg = pytest.mark.skipif(find_binary("ffmpeg") is None, reason="ffmpeg が無い")


def _alpha_range(path) -> tuple[int, int]:
    """全フレームのアルファの最小・最大（signalstats）。"""
    r = subprocess.run([find_binary("ffmpeg"), "-hide_banner", "-i", str(path), "-vf",
                        # 色の範囲を自動変換させない（変換されると透明の 0 が 256 として出る）
                        "alphaextract,scale=in_range=full:out_range=full,format=yuv444p12le,signalstats,"
                        "metadata=print:file=-", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    lo = [int(x) for x in re.findall(r"signalstats\.YMIN=(\d+)", r.stdout)]
    hi = [int(x) for x in re.findall(r"signalstats\.YMAX=(\d+)", r.stdout)]
    return min(lo), max(hi)


def _probe(path):
    return json.loads(subprocess.run([find_binary("ffprobe"), "-v", "error", "-print_format", "json",
                                      "-show_streams", "-show_format", str(path)],
                                     capture_output=True, text=True, encoding="utf-8").stdout)


def _clip(tmp_path):
    clip = tmp_path / "clip.mp4"  # 本体のクリップ（解像度・長さを合わせる元）
    subprocess.run([find_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=d=2:r=30000/1001:s=640x360", "-c:v", "libopenh264", str(clip)], check=True)
    return clip


@needs_ffmpeg
def test_render_overlay_default_is_cropped_qtrle_30fps(tmp_path):
    """初期値: QuickTime Animation（差分方式）・チャット欄だけ切り出し・30fps。背景は透明。"""
    ass = write_ass_vertical(tmp_path / "c.ass", [ChatMessage(0.2, "テスト", "こんにちは"),
                                                  ChatMessage(1.0, "b", "yo")], duration=2, width=640, height=360)
    geo = VideoGeometry.from_file(_clip(tmp_path))
    assert (geo.width, geo.height, geo.fps) == (640, 360, "30000/1001")
    res = render_overlay(ass, tmp_path / "名前 [テスト], 'x'.mov", geo)  # 特殊文字を含む名前でも書ける
    info = _probe(res.path)
    v = info["streams"][0]
    assert v["codec_name"] == "qtrle" and v["pix_fmt"] == "argb"     # アルファチャンネルあり
    x, w = crop_box(ass_margin_l(ass.read_text("utf-8-sig")), 640)
    assert (res.crop_x, res.width) == (x, w) and (v["width"], v["height"]) == (w, 360)
    assert v["r_frame_rate"] == "30/1"
    assert abs(float(info["format"]["duration"]) - geo.duration) < 0.05
    lo, hi = _alpha_range(res.path)
    assert lo == 0 and hi > 0                                         # 透明な部分と、文字の部分がある
    px, py = res.premiere_position(640, 360)
    assert (px, py) == (x + w / 2, 180)


@needs_ffmpeg
@pytest.mark.parametrize("codec,name,pix", [("prores", "prores", "yuva444p"), ("png", "png", "rgba")])
def test_render_overlay_other_codecs_full_frame(tmp_path, codec, name, pix):
    ass = write_ass_vertical(tmp_path / "c.ass", [ChatMessage(0.2, "a", "x")], duration=2, width=640, height=360)
    geo = VideoGeometry.from_file(_clip(tmp_path))
    res = render_overlay(ass, tmp_path / "o.mov", geo, options=OverlayOptions(codec=codec, crop=False, fps=None))
    v = _probe(res.path)["streams"][0]
    assert v["codec_name"] == name and v["pix_fmt"].startswith(pix)
    assert (v["width"], v["height"], v["r_frame_rate"]) == (640, 360, "30000/1001")  # 本体に合わせる
    assert res.crop_x == 0 and res.premiere_position(640, 360) == (320, 180)


def test_crop_box_and_margin():
    assert crop_box(1359, 1920) == (1350, 570)      # 実データ（1080p）と同じ値
    assert crop_box(5, 640) == (0, 640)
    ass = write_ass_vertical(Path(tempfile.mkdtemp()) / "c.ass", [], duration=1)
    assert ass_margin_l(ass.read_text("utf-8-sig")) == 1359


def test_estimate_size():
    assert estimate_size_mb(20) == pytest.approx(21.4, abs=0.1)     # 実測 21.3 MB
    assert estimate_size_mb(60, options=OverlayOptions("prores", False, None), fps=60) == pytest.approx(729, abs=1)
    assert estimate_size_mb(20, height=720) < estimate_size_mb(20)
    assert format_size(2450) == "約 2.4 GB" and format_size(64.2) == "約 64 MB" and format_size(640) == "約 640 MB"


def test_wrap_japanese_long_message():
    """libass は空白でしか改行しないため、日本語の長文を自分で折り返す（実データで見つかった問題）。"""
    lines = wrap_em("たけのこ好きなやつは人に流されやすいし、きのこ派は脅されて意見変えるような奴",
                    first_width=8, width=12)
    assert len(lines) >= 3
    assert all(text_em(l) <= 12 for l in lines) and text_em(lines[0]) <= 8
    assert "".join(lines) == "たけのこ好きなやつは人に流されやすいし、きのこ派は脅されて意見変えるような奴"
    assert wrap_em("hello world foo bar", 100, 100) == ["hello world foo bar"]
    assert wrap_em("hello world foo", 4, 4)[0] == "hello"   # 英語は空白で区切る


def test_ass_lines_fit_panel(tmp_path):
    long = "あ" * 60
    text = write_ass_vertical(tmp_path / "c.ass", [ChatMessage(0, "user", long)], duration=5).read_text("utf-8-sig")
    d = next(l for l in text.splitlines() if l.startswith("Dialogue:"))
    assert d.count(r"\N") >= 2


def test_geometry_fallback_caps_to_1080p():
    g = VideoGeometry.fallback(2560, 1440, 59.94, 30)
    assert (g.width, g.height, g.fps, g.duration) == (1920, 1080, "60000/1001", 30)
    assert VideoGeometry.fallback(None, None, None, 5).width == 1920
