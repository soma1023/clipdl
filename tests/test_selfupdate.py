"""ワンクリック更新: 署名・検証・ダウンロードと展開・入れ替えと巻き戻し。通信はローカルのサーバーだけ。"""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import threading
import zipfile
from pathlib import Path

import pytest
from Cryptodome.PublicKey import ECC

from clipdl.core import selfupdate as su
from clipdl.core import swap

# ------------------------------------------------------------------------ 署名と検証


@pytest.fixture(scope="module")
def key():
    return ECC.generate(curve="Ed25519")


@pytest.fixture(scope="module")
def other_key():
    return ECC.generate(curve="Ed25519")


def _zip(path: Path, version: str, *, extra: dict[str, bytes] | None = None, version_text: str | None = None) -> Path:
    files = {"clipdl/clipdl.exe": b"new-exe", "clipdl/clipdl-updater.exe": b"upd", "clipdl/_internal/x.dll": b"x",
             "clipdl/VERSION": ((version_text or version) + "\n").encode()}
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def _signed(tmp_path, key, version="0.2.0", **kw):
    zip_name, man_name, sig_name = su.asset_names(version)
    z = _zip(tmp_path / zip_name, version, **kw)
    man = su.make_manifest(version, z, su.key_id(key.public_key()))
    (tmp_path / man_name).write_bytes(man)
    (tmp_path / sig_name).write_bytes(su.sign(man, key))
    return man, (tmp_path / sig_name).read_bytes()


def _keys(*ks):
    return {su.key_id(k.public_key()): k.public_key() for k in ks}


def test_sign_and_verify_roundtrip(tmp_path, key):
    man, sig = _signed(tmp_path, key)
    info = su.verify_manifest(man, sig, _keys(key), expected_version="0.2.0", current="0.1.0b2")
    assert info["zip"]["name"] == "clipdl-0.2.0-win64.zip" and len(info["zip"]["sha256"]) == 64


@pytest.mark.parametrize("mutate,msg", [
    (lambda m, s: (m.replace(b'"0.2.0"', b'"0.2.1"', 1), s), "署名の検証に失敗"),
    (lambda m, s: (m + b" ", s), "署名の検証に失敗"),
    (lambda m, s: (m, b"!!!"), "署名ファイルの形式"),
    (lambda m, s: (m, s[:20] + b"\n"), "署名ファイルの形式"),
])
def test_tampered_manifest_or_signature_rejected(tmp_path, key, mutate, msg):
    man, sig = _signed(tmp_path, key)
    m2, s2 = mutate(man, sig)
    with pytest.raises(su.SelfUpdateError, match=msg):
        su.verify_manifest(m2, s2, _keys(key), expected_version="0.2.0", current="0.1.0b2")


def test_unknown_key_rejected(tmp_path, key, other_key):
    man, sig = _signed(tmp_path, key)
    with pytest.raises(su.SelfUpdateError, match="知らない鍵"):
        su.verify_manifest(man, sig, _keys(other_key), expected_version="0.2.0", current="0.1.0b2")


def test_signature_by_other_key_with_forged_key_id_rejected(tmp_path, key, other_key):
    # 鍵 ID だけ信頼済みの鍵のものに書き換え、別の鍵で署名したもの
    version = "0.2.0"
    z = _zip(tmp_path / su.asset_names(version)[0], version)
    man = su.make_manifest(version, z, su.key_id(key.public_key()))
    with pytest.raises(su.SelfUpdateError, match="署名の検証に失敗"):
        su.verify_manifest(man, su.sign(man, other_key), _keys(key), expected_version=version, current="0.1.0b2")


def test_downgrade_and_mismatch_rejected(tmp_path, key):
    man, sig = _signed(tmp_path, key, version="0.1.0b1")
    with pytest.raises(su.SelfUpdateError, match="新しくありません"):
        su.verify_manifest(man, sig, _keys(key), expected_version="0.1.0b1", current="0.1.0b2")
    man, sig = _signed(tmp_path, key, version="0.3.0")
    with pytest.raises(su.SelfUpdateError, match="一致しません"):   # 別のリリースの更新情報を使い回した
        su.verify_manifest(man, sig, _keys(key), expected_version="0.4.0", current="0.1.0b2")


def test_embedded_keys_are_valid_ed25519_public_keys():
    for p in su.KEYS_DIR.glob(su.KEY_GLOB):
        k = ECC.import_key(p.read_text("ascii"))
        assert k.curve == "Ed25519" and not k.has_private()
        assert p.name == f"clipdl_release_{su.key_id(k)}.pub"


# ------------------------------------------------------------------------ 取得・展開


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


def _install(tmp_path) -> Path:
    inst = tmp_path / "Tools" / "clipdl"
    (inst / "_internal").mkdir(parents=True)
    (inst / "clipdl.exe").write_bytes(b"old-exe")
    (inst / "clipdl-updater.exe").write_bytes(b"upd")
    (inst / "VERSION").write_text("0.1.0b2\n")
    return inst


@pytest.fixture
def env(monkeypatch, tmp_path, key, server):
    root, base = server
    pub = tmp_path / "trusted.pub"
    pub.write_text(key.public_key().export_key(format="PEM"))
    monkeypatch.setenv(su.TEST_TRUST_KEY, str(pub))
    monkeypatch.setenv(su.TEST_RELEASE_BASE, base)
    monkeypatch.setenv("CLIPDL_TEST_APP_VERSION", "0.1.0b2")
    monkeypatch.setenv("CLIPDL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLIPDL_LOG_DIR", str(tmp_path / "logs"))
    return root


def test_prepare_downloads_verifies_and_extracts(tmp_path, key, env):
    _signed(env, key, version="0.2.0")
    inst = _install(tmp_path)
    steps = []
    p = su.prepare("v0.2.0", progress=lambda s, r: steps.append(s), target=inst)
    assert p.staging_dir == inst.parent / "clipdl.new"
    assert (p.staging_dir / "clipdl.exe").read_bytes() == b"new-exe"
    assert not (inst.parent / su.DOWNLOAD_NAME).exists()
    assert {"verify", "download", "extract"} <= set(steps)
    found = su.find_prepared(inst)
    assert found and found.version == "0.2.0"


def test_prepare_missing_signature_falls_back(tmp_path, key, env):
    _signed(env, key, version="0.2.0")
    (env / su.asset_names("0.2.0")[2]).unlink()
    with pytest.raises(su.SelfUpdateError, match="署名つきの更新ファイルがありません"):
        su.prepare("v0.2.0", target=_install(tmp_path))


def test_prepare_rejects_swapped_zip(tmp_path, key, env):
    _signed(env, key, version="0.2.0")
    zpath = env / su.asset_names("0.2.0")[0]
    size = zpath.stat().st_size
    data = bytearray(zpath.read_bytes())
    data[10] ^= 0xFF   # サイズは同じで中身だけ違う
    zpath.write_bytes(bytes(data))
    assert zpath.stat().st_size == size
    inst = _install(tmp_path)
    with pytest.raises(su.SelfUpdateError, match="SHA-256"):
        su.prepare("v0.2.0", target=inst)
    assert not (inst.parent / "clipdl.new").exists() and not (inst.parent / su.DOWNLOAD_NAME).exists()


@pytest.mark.parametrize("extra,version_text,msg", [
    ({"clipdl/../evil.txt": b"x"}, None, "想定外のパス"),
    ({"other/x.txt": b"x"}, None, "想定外のパス"),
    (None, "0.1.9", "想定と違います"),
])
def test_prepare_rejects_bad_zip_contents(tmp_path, key, env, extra, version_text, msg):
    _signed(env, key, version="0.2.0", extra=extra, version_text=version_text)
    inst = _install(tmp_path)
    with pytest.raises(su.SelfUpdateError, match=msg):
        su.prepare("v0.2.0", target=inst)
    assert not (inst.parent / "evil.txt").exists() and not (inst.parent / "clipdl.new").exists()


def test_prepare_refuses_to_delete_foreign_staging_dir(tmp_path, key, env):
    _signed(env, key, version="0.2.0")
    inst = _install(tmp_path)
    foreign = inst.parent / "clipdl.new"
    foreign.mkdir()
    (foreign / "mine.txt").write_text("keep")
    with pytest.raises(su.SelfUpdateError, match="clipdl 以外"):
        su.prepare("v0.2.0", target=inst)
    assert (foreign / "mine.txt").exists()


def test_prepare_not_newer_and_unsupported(tmp_path, env):
    inst = _install(tmp_path)
    with pytest.raises(su.SelfUpdateError, match="新しくありません"):
        su.prepare("v0.1.0b2", target=inst)
    (inst / "clipdl-updater.exe").unlink()
    with pytest.raises(su.SelfUpdateError, match="必要なファイル"):
        su.prepare("v0.2.0", target=inst)
    assert su.unsupported_reason(None) is not None or su.install_dir() is not None


def test_start_swap_writes_plan_and_copies_updater(tmp_path, key, env):
    _signed(env, key, version="0.2.0")
    inst = _install(tmp_path)
    p = su.prepare("v0.2.0", target=inst)
    launched = []
    plan = su.start_swap(p, pid=1234, launch=launched.append)
    saved = swap.Plan.load(su.plan_path())
    assert saved.install_dir == inst and saved.backup_dir == inst.parent / "clipdl.old" and saved.pid == 1234
    assert saved.old_version == "0.1.0b2" and saved.new_version == "0.2.0"
    cmd = launched[0]
    assert Path(cmd[0]).name.startswith("clipdl-updater-") and Path(cmd[0]).read_bytes() == b"upd"
    assert cmd[1] == str(su.plan_path()) and plan.result == su.result_path()
    Path(cmd[0]).unlink()


# ------------------------------------------------------------------------ 入れ替えと巻き戻し


class FakeProc:
    def __init__(self, exits=False, pid=4321):
        self.returncode = 1 if exits else None
        self.pid = pid
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakeOps(swap.Ops):
    def __init__(self, plan, *, new_ok=True, new_exits=False, pid_exits=True):
        self.plan, self.new_ok, self.new_exits, self.pid_exits = plan, new_ok, new_exits, pid_exits
        self.launched: list[bytes] = []
        self.t = 0.0

    def wait_pid_exit(self, pid, timeout):
        return self.pid_exits

    def launch(self, exe):
        content = Path(exe).read_bytes()
        self.launched.append(content)
        if content == b"new-exe" and self.new_ok:
            self.plan.ok_marker.write_text(json.dumps({"version": self.plan.new_version}))
        return FakeProc(exits=self.new_exits and content == b"new-exe")

    def sleep(self, sec):
        self.t += sec

    def now(self):
        return self.t


def _plan(tmp_path, inst: Path) -> swap.Plan:
    staging = inst.parent / "clipdl.new"
    (staging / "_internal").mkdir(parents=True)
    (staging / "clipdl.exe").write_bytes(b"new-exe")
    (staging / "VERSION").write_text("0.2.0\n")
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    return swap.Plan(install_dir=inst, staging_dir=staging, backup_dir=inst.parent / "clipdl.old",
                     failed_dir=inst.parent / "clipdl.failed", pid=1, old_version="0.1.0b2", new_version="0.2.0",
                     ok_marker=d / "ok.json", result=d / "result.json", wait_exit=5, wait_ok=5)


def _result(plan):
    return json.loads(plan.result.read_text("utf-8"))


def test_swap_success_keeps_previous_version(tmp_path):
    inst = _install(tmp_path)
    plan = _plan(tmp_path, inst)
    ops = FakeOps(plan)
    assert swap.run(plan, ops) == "ok"
    assert (inst / "clipdl.exe").read_bytes() == b"new-exe"
    assert (plan.backup_dir / "clipdl.exe").read_bytes() == b"old-exe"   # 1 つ前の版は残す
    assert not plan.staging_dir.exists() and _result(plan)["status"] == "ok"


def test_swap_rolls_back_when_new_version_does_not_start(tmp_path):
    inst = _install(tmp_path)
    plan = _plan(tmp_path, inst)
    ops = FakeOps(plan, new_ok=False, new_exits=True)
    assert swap.run(plan, ops) == "rolled_back"
    assert (inst / "clipdl.exe").read_bytes() == b"old-exe"
    assert not plan.failed_dir.exists() and not plan.backup_dir.exists()
    assert ops.launched == [b"new-exe", b"old-exe"]
    r = _result(plan)
    assert r["status"] == "rolled_back" and "元の版" in r["message"]


def test_swap_rolls_back_on_timeout_and_kills_new_process(tmp_path):
    inst = _install(tmp_path)
    plan = _plan(tmp_path, inst)
    procs = []

    class Ops2(FakeOps):
        def launch(self, exe):
            p = super().launch(exe)
            procs.append(p)
            return p

    assert swap.run(plan, Ops2(plan, new_ok=False, new_exits=False)) == "rolled_back"
    assert procs[0].killed and (inst / "clipdl.exe").read_bytes() == b"old-exe"


def test_swap_does_nothing_if_app_does_not_exit(tmp_path):
    inst = _install(tmp_path)
    plan = _plan(tmp_path, inst)
    ops = FakeOps(plan, pid_exits=False)
    assert swap.run(plan, ops) == "not_started"
    assert (inst / "clipdl.exe").read_bytes() == b"old-exe" and plan.staging_dir.exists() and ops.launched == []


def test_swap_does_nothing_when_folder_in_use(tmp_path, monkeypatch):
    inst = _install(tmp_path)
    plan = _plan(tmp_path, inst)
    real = Path.rename

    def rename(self, target):
        if self == inst:
            raise PermissionError("in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    ops = FakeOps(plan)
    assert swap.run(plan, ops) == "not_started"
    assert (inst / "clipdl.exe").read_bytes() == b"old-exe" and ops.launched == [b"old-exe"]
    assert "使用中" in _result(plan)["message"]


def test_swap_restores_when_new_folder_cannot_be_placed(tmp_path, monkeypatch):
    inst = _install(tmp_path)
    plan = _plan(tmp_path, inst)
    real = Path.rename

    def rename(self, target):
        if self == plan.staging_dir:
            raise PermissionError("in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    assert swap.run(plan, FakeOps(plan)) == "rolled_back"
    assert (inst / "clipdl.exe").read_bytes() == b"old-exe"


def test_swap_replaces_older_backup_but_never_deletes_foreign_folder(tmp_path):
    inst = _install(tmp_path)
    plan = _plan(tmp_path, inst)
    old = plan.backup_dir
    (old / "_internal").mkdir(parents=True)
    (old / "clipdl.exe").write_bytes(b"older")
    assert swap.run(plan, FakeOps(plan)) == "ok"
    assert (plan.backup_dir / "clipdl.exe").read_bytes() == b"old-exe"

    inst2 = _install(tmp_path / "b")
    plan2 = _plan(tmp_path / "b", inst2)
    plan2.backup_dir.mkdir()
    (plan2.backup_dir / "notes.txt").write_text("user data")
    assert swap.run(plan2, FakeOps(plan2)) == "not_started"
    assert (plan2.backup_dir / "notes.txt").exists() and (inst2 / "clipdl.exe").read_bytes() == b"old-exe"


def test_plan_roundtrip(tmp_path):
    plan = _plan(tmp_path, _install(tmp_path))
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan.dump()))
    assert swap.Plan.load(p) == plan


# ------------------------------------------------------------------------ 起動したあと


def test_after_startup_writes_ok_marker_for_new_version(tmp_path, env, monkeypatch):
    from clipdl import __version__
    inst = _install(tmp_path)
    monkeypatch.setattr(su, "install_dir", lambda: inst.resolve())
    su.plan_path().write_text(json.dumps({"new_version": __version__, "install_dir": str(inst)}))
    out = su.after_startup()
    assert out and out.status == "ok"
    assert json.loads(su.ok_marker_path().read_text())["version"] == __version__
    assert not su.plan_path().exists()


def test_after_startup_reports_rollback_then_cleans_up(tmp_path, env, monkeypatch):
    monkeypatch.setattr(su, "install_dir", lambda: None)
    su.result_path().write_text(json.dumps({"status": "rolled_back", "message": "新しい版が正しく起動しませんでした。",
                                            "new_version": "0.2.0"}), "utf-8")
    out = su.after_startup()
    assert out and out.status == "rolled_back" and "起動しませんでした" in out.message
    assert not su.result_path().exists() and su.after_startup() is None


# ------------------------------------------------------------------------ yt-dlp の更新版と同梱版


def test_update_not_newer_than_bundled_is_skipped(tmp_path, monkeypatch):
    from clipdl.core import bootstrap
    bundled = tmp_path / "pylib" / "fakemod"
    bundled.mkdir(parents=True)
    (bundled / "version.py").write_text("__version__ = '2026.11.20'\n")
    monkeypatch.syspath_prepend(str(tmp_path / "pylib"))
    z = tmp_path / "upd" / "fake.zip"
    z.parent.mkdir()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("fakemod/__init__.py", "")
    h = hashlib.sha256(z.read_bytes()).hexdigest()
    (z.parent / "manifest.json").write_text(json.dumps({"fake": {"version": "2026.10.26", "file": "fake.zip",
                                                                  "sha256": h}}))
    bootstrap.REJECTED.clear()
    got = bootstrap.activate_updates(z.parent, {"fake": "fakemod"})
    assert got == {} and "fake" not in bootstrap.REJECTED   # 警告は出さずに同梱版を使う
    (bundled / "version.py").write_text("__version__ = '2026.01.01'\n")
    got = bootstrap.activate_updates(z.parent, {"fake": "fakemod"})
    assert "fake" in got
    import sys
    sys.path.remove(str(z))


def test_after_startup_prefers_rollback_result_over_plan(tmp_path, env, monkeypatch):
    # 巻き戻し後に起動した元の版の __version__ が計画の new_version と同じでも、巻き戻しを知らせる
    from clipdl import __version__
    inst = _install(tmp_path)
    monkeypatch.setattr(su, "install_dir", lambda: inst.resolve())
    su.plan_path().write_text(json.dumps({"new_version": __version__, "install_dir": str(inst)}))
    su.result_path().write_text(json.dumps({"status": "rolled_back", "message": "戻しました"}), "utf-8")
    out = su.after_startup()
    assert out and out.status == "rolled_back"
    assert not su.ok_marker_path().exists() and not su.plan_path().exists() and not su.result_path().exists()


# ------------------------------------------------------------------------ 一時フォルダの片付け


def test_cleanup_temp_updaters_removes_all_copies_regardless_of_age(tmp_path):
    for name in ("clipdl-updater-1-1.exe", "clipdl-updater-2-2.exe"):
        (tmp_path / name).write_bytes(b"upd")       # 作ったばかり（年齢は見ない）
    (tmp_path / "other.exe").write_bytes(b"x")       # 関係ないファイルは消さない
    assert su._cleanup_temp_updaters(tmp_path) == 2
    assert [f.name for f in tmp_path.iterdir()] == ["other.exe"]


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows の使用中ファイルの扱いを確かめる")
def test_cleanup_temp_updaters_keeps_running_copy(tmp_path):
    import shutil
    import subprocess
    import sys
    running = tmp_path / "clipdl-updater-9-9.exe"
    shutil.copy2(sys.executable, running)            # 動いている exe は Windows が消させない
    proc = subprocess.Popen([str(running), "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "clipdl-updater-1-1.exe").write_bytes(b"upd")
        assert su._cleanup_temp_updaters(tmp_path) == 1
        assert running.exists()
    finally:
        proc.kill()
        proc.wait()
    assert su._cleanup_temp_updaters(tmp_path) == 1   # 終わったあとの起動で消える


def test_cleanup_runs_after_successful_update_and_before_swap(tmp_path, key, env, monkeypatch):
    calls = []
    monkeypatch.setattr(su, "_cleanup_temp_updaters", lambda *a: calls.append("cleanup") or 0)
    _signed(env, key, version="0.2.0")
    inst = _install(tmp_path)
    p = su.prepare("v0.2.0", target=inst)
    launched = []
    su.start_swap(p, pid=1, launch=launched.append)
    assert calls == ["cleanup"]                      # コピーを作る前に、前回までの分を消す
    Path(launched[0][0]).unlink()
    monkeypatch.setattr(su, "__version__", "0.2.0")
    monkeypatch.setattr(su, "install_dir", lambda: inst.resolve())
    out = su.after_startup()
    assert out and out.status == "ok" and calls == ["cleanup", "cleanup"]   # 更新が成功した起動でも消す
