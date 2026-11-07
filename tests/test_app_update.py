"""clipdl 本体の新しい版の確認（版の比較・1 日 1 回・通信失敗）。通信はしない。"""
import pytest

from clipdl.core import app_update
from clipdl.core.app_update import RELEASES_PAGE, Release, check_now, check_on_startup, is_newer, pick_latest
from clipdl.core.settings import Settings

DAY = 24 * 3600


@pytest.mark.parametrize("newer,older", [
    ("0.1.0b2", "0.1.0b1"), ("0.1.0rc1", "0.1.0b9"), ("0.1.0", "0.1.0rc1"), ("0.1.1", "0.1.0"),
    ("v0.2.0b1", "0.1.9"), ("1.0.0", "0.99.99"), ("0.1.0b10", "0.1.0b9"), ("0.1.0-beta.2", "0.1.0b1"),
    ("0.2", "0.1.9"),
])
def test_is_newer(newer, older):
    assert is_newer(newer, older)
    assert not is_newer(older, newer)


@pytest.mark.parametrize("a,b", [("0.1.0b1", "v0.1.0b1"), ("0.1", "0.1.0"), ("x", "0.1.0"), ("0.1.0", "nightly")])
def test_not_newer_when_equal_or_unreadable(a, b):
    assert not is_newer(a, b)


def _rel(tag, draft=False, pre=True):
    return {"tag_name": tag, "draft": draft, "prerelease": pre,
            "html_url": f"{RELEASES_PAGE}/tag/{tag}"}


def test_pick_latest_uses_version_not_order_and_skips_drafts_and_junk():
    data = [_rel("v0.1.0b2"), _rel("v0.3.0", draft=True), _rel("nightly"), _rel("v0.2.0b1"), _rel("v0.1.0", pre=False)]
    r = pick_latest(data)
    assert r == Release("v0.2.0b1", f"{RELEASES_PAGE}/tag/v0.2.0b1")
    assert pick_latest([]) is None


def test_pick_latest_ignores_foreign_urls():
    r = pick_latest([{"tag_name": "v9.0.0", "html_url": "https://evil.example/clipdl"}])
    assert r.url == RELEASES_PAGE


class Fetch:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, 0

    def __call__(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPDL_CONFIG_DIR", str(tmp_path))
    return Settings()


def test_startup_check_notifies_and_caches_for_a_day(settings):
    f = Fetch(Release("v0.1.0b2", f"{RELEASES_PAGE}/tag/v0.1.0b2"))
    r = check_on_startup(settings, f, now=1000.0, current="0.1.0b1")
    assert r and r.tag == "v0.1.0b2" and f.calls == 1
    assert settings.app_update_latest == "v0.1.0b2" and settings.app_update_checked_at == 1000.0
    # 24 時間以内は通信せず、前回の結果で知らせる
    r2 = check_on_startup(settings, f, now=1000.0 + DAY - 1, current="0.1.0b1")
    assert r2 and r2.url.endswith("/v0.1.0b2") and f.calls == 1
    # 24 時間たったら確認し直す
    check_on_startup(settings, f, now=1000.0 + DAY, current="0.1.0b1")
    assert f.calls == 2


def test_startup_check_silent_when_up_to_date(settings):
    assert check_on_startup(settings, Fetch(Release("v0.1.0b1", RELEASES_PAGE)), now=1.0, current="0.1.0b1") is None
    assert check_on_startup(settings, Fetch(None), now=DAY * 2, current="0.1.0b1") is None


def test_startup_check_failure_is_silent_and_not_retried_same_day(settings):
    f = Fetch(exc=OSError("network down"))
    assert check_on_startup(settings, f, now=5000.0, current="0.1.0b1") is None
    assert settings.app_update_checked_at == 5000.0
    assert check_on_startup(settings, f, now=5000.0 + 60, current="0.1.0b1") is None
    assert f.calls == 1


def test_failure_keeps_previous_result(settings):
    check_on_startup(settings, Fetch(Release("v0.2.0", RELEASES_PAGE)), now=100.0, current="0.1.0b1")
    r = check_on_startup(settings, Fetch(exc=TimeoutError("slow")), now=DAY + 100, current="0.1.0b1")
    assert r and r.tag == "v0.2.0"


def test_startup_check_off_does_nothing(settings):
    settings.check_app_update = False
    f = Fetch(Release("v9.0.0", RELEASES_PAGE))
    assert check_on_startup(settings, f, now=1.0) is None and f.calls == 0


def test_clock_going_back_triggers_recheck(settings):
    f = Fetch(None)
    check_on_startup(settings, f, now=10 * DAY)
    check_on_startup(settings, f, now=1 * DAY)
    assert f.calls == 2


def test_check_now_summary():
    assert "v0.1.0b2 があります" in check_now(Fetch(Release("v0.1.0b2", RELEASES_PAGE)), "0.1.0b1").summary()
    assert check_now(Fetch(Release("v0.1.0b1", RELEASES_PAGE)), "0.1.0b1").summary() == "clipdl: 0.1.0b1（最新）"
    st = check_now(Fetch(exc=ValueError("bad json")), "0.1.0b1")
    assert st.error and "確認できませんでした" in st.summary()


def test_fetch_latest_parses_api(monkeypatch):
    import io
    import json

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = json.dumps([_rel("v0.1.0b1"), _rel("v0.1.0b3")]).encode()
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["timeout"] = req.full_url, timeout
        return Resp(body)

    monkeypatch.setattr(app_update.urllib.request, "urlopen", fake_urlopen)
    assert app_update.fetch_latest().tag == "v0.1.0b3"
    assert seen["url"].startswith("https://api.github.com/repos/soma1023/clipdl/releases") and seen["timeout"] == 5.0


def test_settings_roundtrip_keeps_update_fields(settings):
    settings.check_app_update = False
    settings.app_update_latest = "v0.1.0b2"
    settings.save()
    s2 = Settings.load()
    assert s2.check_app_update is False and s2.app_update_latest == "v0.1.0b2"
