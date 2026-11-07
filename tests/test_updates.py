"""更新版の照合（bootstrap）と更新の取得・検証（updater）のテスト。通信はすべて差し替える。"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from clipdl.core import bootstrap, updater
from clipdl.core.errors import UserError

DATA = Path(__file__).parent / "data" / "ytdlp_2026.10.26"
SUMS = (DATA / "SHA2-256SUMS").read_bytes()
SIG = (DATA / "SHA2-256SUMS.sig").read_bytes()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPDL_DATA_DIR", str(tmp_path / "data"))
    bootstrap.ACTIVE.clear()
    bootstrap.REJECTED.clear()
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    for m in [m for m in sys.modules if m.startswith("clipdl_fake")]:
        del sys.modules[m]
    bootstrap.ACTIVE.clear()
    bootstrap.REJECTED.clear()


def make_zip(path: Path, module: str, version: str) -> str:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{module}/__init__.py", f"VERSION = {version!r}\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(root: Path, entries: dict):
    (root / bootstrap.MANIFEST).write_text(json.dumps(entries), "utf-8")


# ---------------------------------------------------------------------- 起動時の照合

def test_activates_only_when_hash_matches():
    root = bootstrap.updates_dir()
    h = make_zip(root / "fake-1.zip", "clipdl_fake_a", "1.0")
    write_manifest(root, {"fake": {"version": "1.0", "file": "fake-1.zip", "sha256": h}})
    got = bootstrap.activate_updates(components={"fake": "clipdl_fake_a"})
    assert "fake" in got and str(root / "fake-1.zip") == sys.path[0]
    import clipdl_fake_a
    assert clipdl_fake_a.VERSION == "1.0"


def test_tampered_file_is_not_used():
    root = bootstrap.updates_dir()
    h = make_zip(root / "fake-1.zip", "clipdl_fake_b", "1.0")
    write_manifest(root, {"fake": {"version": "1.0", "file": "fake-1.zip", "sha256": h}})
    with open(root / "fake-1.zip", "ab") as f:  # 検証後にファイルが書き換えられた
        f.write(b"x")
    assert bootstrap.activate_updates(components={"fake": "clipdl_fake_b"}) == {}
    assert "SHA-256" in bootstrap.REJECTED["fake"]
    assert str(root / "fake-1.zip") not in sys.path


@pytest.mark.parametrize("entry,reason", [
    ({"version": "1", "file": "missing.zip", "sha256": "0" * 64}, "見つかりません"),
    ({"version": "1", "file": "../evil.zip", "sha256": "0" * 64}, "正しくありません"),
    ({"version": "1", "file": "x.zip"}, "正しくありません"),
])
def test_bad_manifest_entries(entry, reason):
    write_manifest(bootstrap.updates_dir(), {"fake": entry})
    assert bootstrap.activate_updates(components={"fake": "clipdl_fake_c"}) == {}
    assert reason in bootstrap.REJECTED["fake"]


def test_broken_manifest_is_ignored():
    (bootstrap.updates_dir() / bootstrap.MANIFEST).write_text("{not json", "utf-8")
    assert bootstrap.activate_updates(components={"fake": "clipdl_fake_d"}) == {}


def test_already_imported_module_is_not_replaced():
    root = bootstrap.updates_dir()
    h = make_zip(root / "j.zip", "json", "x")   # json はすでに読み込まれている
    write_manifest(root, {"fake": {"version": "x", "file": "j.zip", "sha256": h}})
    assert bootstrap.activate_updates(components={"fake": "json"}) == {}
    assert "読み込んでいる" in bootstrap.REJECTED["fake"]


def test_ejs_not_used_without_matching_ytdlp_update():
    root = bootstrap.updates_dir()
    h1 = make_zip(root / "y.zip", "clipdl_fake_y", "1")
    h2 = make_zip(root / "e.zip", "clipdl_fake_e", "1")
    write_manifest(root, {"yt-dlp": {"version": "1", "file": "y.zip", "sha256": h1[::-1]},  # yt-dlp は不一致
                          "yt-dlp-ejs": {"version": "1", "file": "e.zip", "sha256": h2}})
    got = bootstrap.activate_updates(components={"yt-dlp": "clipdl_fake_y", "yt-dlp-ejs": "clipdl_fake_e"})
    assert got == {}
    assert "対になる" in bootstrap.REJECTED["yt-dlp-ejs"]


# ---------------------------------------------------------------------- 取得・検証

def test_parse_sums_and_zip_version(tmp_path):
    sums = updater.parse_sums(SUMS.decode())
    assert sums["yt-dlp"] == "e51e69e89489f199530f13d32a4342da3ddb2cf34842f364603a066db05afbf3"
    z = tmp_path / "y.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("yt_dlp/version.py", "__version__ = '2026.10.26'\nRELEASE_GIT_HEAD = 'x'\n")
    assert updater.zip_ytdlp_version(z) == "2026.10.26"


class FakeNet:
    """GitHub と PyPI の応答を差し替える。"""

    def __init__(self, sums=SUMS, sig=SIG, ytdlp=b"not the real file"):
        self.files = {"https://x/yt-dlp": ytdlp, "https://x/SHA2-256SUMS": sums, "https://x/SHA2-256SUMS.sig": sig}

    def get_json(self, url, timeout=15.0):
        if url == updater.GITHUB_LATEST:
            return {"tag_name": "2026.10.26",
                    "assets": [{"name": n, "browser_download_url": f"https://x/{n}"}
                               for n in ("yt-dlp", "SHA2-256SUMS", "SHA2-256SUMS.sig")]}
        if "pypi.org/pypi/yt-dlp/" in url:
            return {"info": {"requires_dist": ["yt-dlp-ejs==0.8.0; extra == 'default'"]}}
        raise AssertionError(url)

    def get_bytes(self, url, timeout=60.0):
        return self.files[url]


@pytest.fixture
def net(monkeypatch):
    def install(fake: FakeNet):
        monkeypatch.setattr(updater, "_get_json", fake.get_json)
        monkeypatch.setattr(updater, "_get_bytes", fake.get_bytes)
    return install


def _manifest_unchanged(before: str):
    p = bootstrap.updates_dir() / bootstrap.MANIFEST
    assert p.read_text("utf-8") == before
    leftovers = [x.name for x in bootstrap.updates_dir().iterdir() if x.name != bootstrap.MANIFEST]
    assert leftovers == []  # 一時フォルダも残らない


def test_update_rejects_tampered_sums(net):
    write_manifest(bootstrap.updates_dir(), {"note": "before"})
    before = (bootstrap.updates_dir() / bootstrap.MANIFEST).read_text("utf-8")
    net(FakeNet(sums=SUMS.replace(b"e51e", b"0000", 1)))
    with pytest.raises(UserError) as e:
        updater.update()
    assert e.value.code == "update_signature" and "同梱版" in e.value.message_ja
    _manifest_unchanged(before)


def test_update_rejects_signature_from_other_key(net):
    from tests.pgp_forge import make_key, make_signature
    write_manifest(bootstrap.updates_dir(), {})
    before = (bootstrap.updates_dir() / bootstrap.MANIFEST).read_text("utf-8")
    net(FakeNet(sig=make_signature(make_key(), SUMS)))
    with pytest.raises(UserError) as e:
        updater.update()
    assert e.value.code == "update_signature"
    _manifest_unchanged(before)


def test_update_rejects_file_hash_mismatch(net):
    write_manifest(bootstrap.updates_dir(), {})
    before = (bootstrap.updates_dir() / bootstrap.MANIFEST).read_text("utf-8")
    net(FakeNet(ytdlp=b"evil"))  # 署名は本物だが、ファイルが SHA2-256SUMS と一致しない
    with pytest.raises(UserError) as e:
        updater.update()
    assert e.value.code == "update_hash"
    _manifest_unchanged(before)


def test_treat_bundled_old_flag(monkeypatch):
    st = updater.ComponentStatus("yt-dlp", "2026.10.26", "2026.10.26")
    assert not st.outdated
    monkeypatch.setenv(updater.TEST_TREAT_OLD, "1")
    assert st.outdated
    assert not updater.ComponentStatus("Deno", "2.9.8", None, updatable=False).outdated
