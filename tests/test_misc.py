import threading

import pytest

from clipdl.core.cfr import judge, nearest_standard_rate, parse_rate, parse_vfrdet, target_rate
from clipdl.core.cookies import CookieOptionError, parse_browser_spec
from clipdl.core.errors import Cancelled, classify
from clipdl.core.logging_setup import mask
from clipdl.core.models import ClipRange, VideoInfo
from clipdl.core.naming import TemplateError, render, sanitize
from clipdl.core.queue import InvalidTransition, JobQueue, JobState
from clipdl.core.pipeline import JobSpec


# ---------------------------------------------------------------- エラーの日本語化

@pytest.mark.parametrize("raw,code", [
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video", "members_only"),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "geo"),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] abc: This video is DRM protected", "drm"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot", "bot_check"),
    ("ERROR: [youtube] abc: Sign in to confirm your age", "age"),
    ("ERROR: Unsupported URL: https://example.com", "unsupported"),
    ("ERROR: unable to download: HTTP Error 403: Forbidden", "forbidden"),
    ("ERROR: <urlopen error [Errno 11001] getaddrinfo failed>", "network"),
    ("something totally unexpected", "unknown"),
])
def test_classify(raw, code):
    e = classify(raw)
    assert e.code == code
    assert e.raw == raw
    assert e.message_ja and e.message_en


def test_classify_strips_ansi():
    assert classify("\x1b[0;31mERROR:\x1b[0m Private video").raw == "ERROR: Private video"


# ---------------------------------------------------------------- ファイル名

def _info(**kw):
    base = dict(url="u", id="abc", extractor="youtube", title='A/B: "test"?', channel="ch|1", duration=100,
                thumbnail=None, upload_date="20261106", live_status=None, has_live_chat=False, fps=30,
                width=1920, height=1080)
    base.update(kw)
    return VideoInfo(**base)


def test_render_template_sanitizes_values():
    assert render("{date}_{channel}_{title}", _info()) == "20261106_ch｜1_A／B： ”test”？"


def test_render_appends_clip_tag():
    assert render("{title}", _info(title="t"), ClipRange(60, 90, 2)) == "t_c02_000100-000130"
    assert render("{clip}_{title}", _info(title="t"), ClipRange(60, 90, 2)) == "c02_000100-000130_t"


def test_render_unknown_field():
    with pytest.raises(TemplateError):
        render("{nope}", _info())


@pytest.mark.parametrize("raw,expected", [("CON", "_CON"), ("a. ", "a"), ("", "untitled"), ("x" * 300, "x" * 150)])
def test_sanitize(raw, expected):
    assert sanitize(raw) == expected


# ---------------------------------------------------------------- CFR

def test_parse_vfrdet():
    assert parse_vfrdet("... VFR:0.000000 (0/59)\n") == (0.0, 0, 59)
    assert parse_vfrdet("x VFR:0.250000 (25/100) min: 1 max: 3 avg: 2") == (0.25, 25, 100)
    assert parse_vfrdet("VFR:nan (0/0)") == (None, 0, 0)
    assert parse_vfrdet("no output") == (None, 0, 0)


def test_rates():
    assert parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert parse_rate("0/0") is None
    assert nearest_standard_rate(59.94) == "60000/1001"
    assert nearest_standard_rate(60.02) == "60"
    assert nearest_standard_rate(None) == "30"


def test_judge():
    assert judge(60, 60, 0.0) is False
    assert judge(60, 60, 0.2) is True
    assert judge(60, 45, None) is True
    assert judge(None, None, None) is False
    # vfrdet の比率が低くても、公称と平均が大きくずれていれば VFR（実測で見つかったケース）
    assert judge(30, 20, 0.008) is True
    assert judge(60, 59.97, 0.0) is False


def test_target_rate_prefers_nominal():
    assert target_rate(30, 20) == "30"
    assert target_rate(1000, 29.97) == "30000/1001"
    assert target_rate(None, None) == "30"


# ---------------------------------------------------------------- Cookie / ログ

def test_parse_browser_spec():
    assert parse_browser_spec("firefox") == ("firefox", None, None, None)
    assert parse_browser_spec("Chrome:Profile 1") == ("chrome", "Profile 1", None, None)
    with pytest.raises(CookieOptionError):
        parse_browser_spec("netscape")


def test_mask_hides_secrets():
    out = mask("cookie: SAPISID=abcdef123; token=xyz")
    assert "abcdef123" not in out and "xyz" not in out


# ---------------------------------------------------------------- キュー

def test_queue_runs_and_handles_failure():
    def runner(spec, on_progress, token):
        if spec.url == "bad":
            raise RuntimeError("Private video")
        return "ok"

    q = JobQueue(runner=runner)
    good, bad = q.add(JobSpec(url="good")), q.add(JobSpec(url="bad"))
    q.run_until_idle()
    assert good.state == JobState.DONE and good.result == "ok"
    assert bad.state == JobState.FAILED and bad.error.code == "private"
    q.resume(bad)
    assert bad.state == JobState.WAITING


def test_queue_pause_running_job_then_resume():
    started, attempts = threading.Event(), []

    def runner(spec, on_progress, token):
        attempts.append(1)
        if len(attempts) == 1:
            started.set()
            token.event.wait(5)
            token.check()
        return "done"

    q = JobQueue(runner=runner)
    job = q.add(JobSpec(url="x"))
    t = threading.Thread(target=q.run_until_idle)
    t.start()
    assert started.wait(5)
    q.pause(job)
    t.join(5)
    assert job.state == JobState.PAUSED
    q.resume(job)
    q.run_until_idle()
    assert job.state == JobState.DONE and len(attempts) == 2


def test_queue_invalid_transition():
    q = JobQueue(runner=lambda *a: None)
    job = q.add(JobSpec(url="x"))
    q.run_until_idle()
    with pytest.raises(InvalidTransition):
        q.pause(job)


def test_cancelled_is_exception():
    assert issubclass(Cancelled, Exception)


# ---------------------------------------------------------------- 環境変数

@pytest.mark.parametrize("raw,val,flag", [("1 ", "1", True), ("  true\t", "true", True), ('"1"', "1", True),
                                          (" ", None, False), ("0 ", "0", False), ("", None, False)])
def test_env_strips_spaces(monkeypatch, raw, val, flag):
    """cmd の `set X=1 && ...` は値が "1 " になる。前後の空白を取り除いてから判定する。"""
    from clipdl.core.paths import env, env_flag
    monkeypatch.setenv("CLIPDL_TEST_X", raw)
    assert env("CLIPDL_TEST_X") == val and env_flag("CLIPDL_TEST_X") is flag


def test_treat_old_and_encoder_env_with_trailing_space(monkeypatch):
    from clipdl.core import ffmpeg as ff
    from clipdl.core import updater
    monkeypatch.setenv(updater.TEST_TREAT_OLD, "1 ")
    assert updater._treat_old()
    monkeypatch.setenv("CLIPDL_DISABLE_ENCODERS", " h264_nvenc , h264_qsv ")
    monkeypatch.setenv("CLIPDL_ENCODER", "auto ")
    ff.configure_encoders()
    try:
        assert ff._disabled == {"h264_nvenc", "h264_qsv"} and ff._forced is None
    finally:
        monkeypatch.delenv("CLIPDL_DISABLE_ENCODERS")
        monkeypatch.delenv("CLIPDL_ENCODER")
        ff.configure_encoders()
