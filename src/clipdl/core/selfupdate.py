"""clipdl 本体のワンクリック更新（アプリ側）。

流れ:
  1. GitHub リリースから「更新情報」（clipdl-<版>-win64.update.json）と署名（.sig）を取得
  2. アプリに埋め込んだ Ed25519 の公開鍵で署名を検証し、版番号・タグ・今の版より新しいことを確かめる
  3. zip をダウンロードし、更新情報に書かれたサイズと SHA-256 を照合
  4. アプリのフォルダの隣（clipdl.new）に展開し、VERSION ファイルと必要な exe を確かめる
  5. 利用者が「再起動して更新」を選んだら、clipdl-updater.exe を一時フォルダにコピーして起動し、アプリを終了
     （入れ替えと失敗時の巻き戻しは core/swap.py）

どこかで問題があれば SelfUpdateError を出す（画面ではダウンロードページを開く案内に戻す）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .. import __version__
from .app_update import REPO, current_version, is_newer
from .paths import data_dir, env, log_dir
from .swap import EXE_NAME, VERSION_FILE, Plan, looks_like_clipdl

log = logging.getLogger(__name__)

MANIFEST_FORMAT = 1
UPDATER_EXE = "clipdl-updater.exe"
STAGING_NAME, BACKUP_NAME, FAILED_NAME = "clipdl.new", "clipdl.old", "clipdl.failed"
DOWNLOAD_NAME = "clipdl.download.zip"
STAGING_MARKER = ".clipdl-staging.json"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
KEYS_DIR = Path(__file__).parent / "keys"
KEY_GLOB = "clipdl_release_*.pub"
MAX_MANIFEST = 64 * 1024
_UA = {"User-Agent": "clipdl-self-update"}

# テスト用（README の開発者向けを参照）。どちらも配布物の通常の動作には影響しない
TEST_RELEASE_BASE = "CLIPDL_TEST_RELEASE_BASE"   # http://127.0.0.1:<port>/ のローカルサーバーから取得する
TEST_TRUST_KEY = "CLIPDL_TEST_TRUST_KEY"         # 追加で信頼する公開鍵（PEM）のパス


class SelfUpdateError(Exception):
    """自動更新できない理由（日本語・1 行）。"""


def asset_names(version: str) -> tuple[str, str, str]:
    zip_name = f"clipdl-{version}-win64.zip"
    manifest = zip_name[:-4] + ".update.json"
    return zip_name, manifest, manifest + ".sig"


# ------------------------------------------------------------------------ 鍵と署名


def key_id(public_key) -> str:
    return hashlib.sha256(public_key.export_key(format="raw")).hexdigest()[:16]


def load_trusted_keys() -> dict:
    """アプリに埋め込んだ公開鍵（keys/clipdl_release_*.pub）を、鍵 ID → 鍵 の形で返す。"""
    from Cryptodome.PublicKey import ECC
    paths = sorted(KEYS_DIR.glob(KEY_GLOB))
    extra = env(TEST_TRUST_KEY)
    if extra:
        paths.append(Path(extra))
    keys = {}
    for p in paths:
        try:
            k = ECC.import_key(p.read_text("ascii"))
        except (OSError, ValueError) as e:
            log.warning("公開鍵 %s を読めません: %s", p.name, e)
            continue
        if k.curve != "Ed25519" or k.has_private():
            log.warning("公開鍵 %s は Ed25519 の公開鍵ではないため使いません", p.name)
            continue
        keys[key_id(k)] = k
    return keys


def make_manifest(version: str, zip_path: Path, kid: str) -> bytes:
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    data = {"format": MANIFEST_FORMAT, "app": "clipdl", "version": version, "tag": f"v{version}",
            "zip": {"name": zip_path.name, "size": zip_path.stat().st_size, "sha256": h.hexdigest()},
            "key_id": kid}
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sign(data: bytes, private_key) -> bytes:
    from Cryptodome.Signature import eddsa
    sig = eddsa.new(private_key, "rfc8032").sign(data)
    return base64.b64encode(sig) + b"\n"


def verify_manifest(data: bytes, sig_text: bytes, keys: dict, *, expected_version: str, current: str) -> dict:
    """署名と中身を確かめ、更新情報（dict）を返す。問題があれば SelfUpdateError。"""
    from Cryptodome.Signature import eddsa
    try:
        sig = base64.b64decode(sig_text.strip(), validate=True)
    except ValueError:
        raise SelfUpdateError("署名ファイルの形式が正しくありません") from None
    if len(sig) != 64:
        raise SelfUpdateError("署名ファイルの形式が正しくありません")
    try:
        info = json.loads(data.decode("utf-8"))
    except ValueError:
        raise SelfUpdateError("更新情報の形式が正しくありません") from None
    if not isinstance(info, dict):
        raise SelfUpdateError("更新情報の形式が正しくありません")
    key = keys.get(str(info.get("key_id")))
    if key is None:
        raise SelfUpdateError("この版のアプリが知らない鍵で署名されています")
    try:
        eddsa.new(key, "rfc8032").verify(data, sig)
    except ValueError:
        raise SelfUpdateError("署名の検証に失敗しました") from None
    # ここから下は署名で守られた中身の確認
    z = info.get("zip") if isinstance(info.get("zip"), dict) else {}
    if info.get("format") != MANIFEST_FORMAT or info.get("app") != "clipdl":
        raise SelfUpdateError("更新情報の形式が正しくありません")
    version = str(info.get("version"))
    if version != expected_version or info.get("tag") != f"v{expected_version}":
        raise SelfUpdateError(f"更新情報の版（{version}）がリリース（v{expected_version}）と一致しません")
    if not is_newer(version, current):
        raise SelfUpdateError(f"更新情報の版（{version}）が今の版（{current}）より新しくありません")
    if z.get("name") != asset_names(version)[0] or not isinstance(z.get("size"), int) \
            or not isinstance(z.get("sha256"), str) or len(z["sha256"]) != 64:
        raise SelfUpdateError("更新情報の zip の記録が正しくありません")
    return info


# ------------------------------------------------------------------------ 置き場所


def install_dir() -> Path | None:
    """配布版（exe）のときだけアプリのフォルダを返す。開発中（python で実行）は None。"""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def unsupported_reason(target: Path | None = None) -> str | None:
    """この環境で自動更新できない理由（できるなら None）。"""
    d = target or install_dir()
    if d is None:
        return "開発中の実行では自動更新できません"
    if not (d / EXE_NAME).is_file() or not (d / UPDATER_EXE).is_file():
        return "アプリのフォルダに必要なファイルがありません"
    parent = d.parent
    try:
        fd, probe = tempfile.mkstemp(prefix=".clipdl-write-test-", dir=parent)
        os.close(fd)
        os.unlink(probe)
    except OSError:
        return f"{parent} に書き込めないため（Program Files などに置いている場合）"
    return None


@dataclass
class Prepared:
    version: str
    install_dir: Path
    staging_dir: Path


# ------------------------------------------------------------------------ 取得と展開


def _base_url(tag: str) -> str:
    test = env(TEST_RELEASE_BASE)
    if test:
        if not test.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise SelfUpdateError("テスト用の取得元はローカルのサーバーだけにしてください")
        return test.rstrip("/") + "/"
    return DOWNLOAD_PREFIX + tag + "/"


def _get(url: str, limit: int, timeout: float = 15.0) -> bytes:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SelfUpdateError("このリリースには署名つきの更新ファイルがありません") from None
        raise
    if len(data) > limit:
        raise SelfUpdateError("更新情報が大きすぎます")
    return data


def _download(url: str, dest: Path, size: int, sha256: str, progress, cancelled) -> None:
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30) as r, open(dest, "wb") as f:
        while True:
            if cancelled():
                raise SelfUpdateError("中止しました")
            b = r.read(1 << 20)
            if not b:
                break
            done += len(b)
            if done > size:
                raise SelfUpdateError("zip のサイズが更新情報と一致しません")
            h.update(b)
            f.write(b)
            progress("download", done / size if size else None)
    if done != size:
        raise SelfUpdateError("zip のサイズが更新情報と一致しません")
    if h.hexdigest() != sha256.lower():
        raise SelfUpdateError("zip の SHA-256 が更新情報と一致しません")


def _safe_extract(zip_path: Path, dest: Path) -> None:
    """zip の中の clipdl/ 以下だけを展開する（絶対パスや .. を含むものは拒否）。"""
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or ":" in info.filename or not p.parts or p.parts[0] != "clipdl":
                raise SelfUpdateError(f"zip の中に想定外のパスがあります: {info.filename}")
            rel = Path(*p.parts[1:]) if len(p.parts) > 1 else None
            if rel is None:
                continue
            target = dest / rel
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)


def _clear_staging(staging: Path) -> None:
    """前回の準備の残りを消す（clipdl が作ったものだと分かるときだけ）。"""
    if not staging.exists():
        return
    if not ((staging / STAGING_MARKER).is_file() or looks_like_clipdl(staging)):
        raise SelfUpdateError(f"{staging} に clipdl 以外のフォルダがあるため、更新を準備できません")
    shutil.rmtree(staging, ignore_errors=True)
    if staging.exists():
        raise SelfUpdateError(f"前回の更新の残り（{staging.name}）を消せません")


def prepare(tag: str, progress: Callable[[str, float | None], None] | None = None,
            cancelled: Callable[[], bool] | None = None, target: Path | None = None) -> Prepared:
    """新しい版をダウンロード・検証して clipdl.new に展開する。"""
    progress = progress or (lambda stage, ratio: None)
    cancelled = cancelled or (lambda: False)
    inst = target or install_dir()
    reason = unsupported_reason(inst)
    if reason:
        raise SelfUpdateError(reason)
    assert inst is not None
    version = tag[1:] if tag.startswith("v") else tag
    current = current_version()
    if not is_newer(version, current):
        raise SelfUpdateError(f"{tag} は今の版（{current}）より新しくありません")
    keys = load_trusted_keys()
    if not keys:
        raise SelfUpdateError("アプリに更新用の公開鍵が入っていません")

    progress("verify", None)
    base = _base_url(tag)
    zip_name, manifest_name, sig_name = asset_names(version)
    manifest = _get(base + manifest_name, MAX_MANIFEST)
    sig = _get(base + sig_name, 1024)
    info = verify_manifest(manifest, sig, keys, expected_version=version, current=current)
    size, sha = info["zip"]["size"], info["zip"]["sha256"]
    log.info("更新情報の署名を確認しました: %s（鍵 %s）", version, info["key_id"])

    parent = inst.parent
    free = shutil.disk_usage(parent).free
    if free < size * 4:
        raise SelfUpdateError(f"ドライブの空きが足りません（必要 約 {size * 4 // 2**20} MB）")
    staging = parent / STAGING_NAME
    _clear_staging(staging)
    dl = parent / DOWNLOAD_NAME
    try:
        _download(base + zip_name, dl, size, sha, progress, cancelled)
        progress("extract", None)
        staging.mkdir()
        (staging / STAGING_MARKER).write_text(json.dumps({"version": version, "complete": False}), "utf-8")
        _safe_extract(dl, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        try:
            dl.unlink()
        except FileNotFoundError:
            pass
    found = (staging / VERSION_FILE).read_text("utf-8").strip() if (staging / VERSION_FILE).is_file() else ""
    if found != version or not looks_like_clipdl(staging) or not (staging / UPDATER_EXE).is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise SelfUpdateError("展開した新しい版のファイルが想定と違います")
    (staging / STAGING_MARKER).write_text(json.dumps({"version": version, "complete": True}), "utf-8")
    log.info("新しい版 %s を %s に準備しました", version, staging)
    return Prepared(version, inst, staging)


def find_prepared(target: Path | None = None) -> Prepared | None:
    """前回「あとで」を選んだときなど、準備済みの新しい版があれば返す。"""
    inst = target or install_dir()
    if inst is None:
        return None
    staging = inst.parent / STAGING_NAME
    try:
        m = json.loads((staging / STAGING_MARKER).read_text("utf-8"))
        version = (staging / VERSION_FILE).read_text("utf-8").strip()
    except (OSError, ValueError):
        return None
    if m.get("complete") and m.get("version") == version and is_newer(version, current_version()) \
            and looks_like_clipdl(staging):
        return Prepared(version, inst, staging)
    return None


# ------------------------------------------------------------------------ 入れ替えの開始と、そのあと


def plan_path() -> Path:
    return data_dir() / "update-plan.json"


def ok_marker_path() -> Path:
    return data_dir() / "update-ok.json"


def result_path() -> Path:
    return data_dir() / "update-result.json"


def start_swap(p: Prepared, pid: int | None = None, launch=None) -> Plan:
    """入れ替え用プログラムを一時フォルダにコピーして起動する。呼んだあと、アプリはすぐ終了すること。"""
    plan = Plan(install_dir=p.install_dir, staging_dir=p.staging_dir,
                backup_dir=p.install_dir.parent / BACKUP_NAME, failed_dir=p.install_dir.parent / FAILED_NAME,
                pid=pid if pid is not None else os.getpid(), old_version=current_version(), new_version=p.version,
                ok_marker=ok_marker_path(), result=result_path(), log=log_dir() / "clipdl-updater.log")
    pp = plan_path()
    pp.write_text(json.dumps(plan.dump(), ensure_ascii=False, indent=2), "utf-8")
    for f in (plan.ok_marker, plan.result):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
    _cleanup_temp_updaters()   # 前回までのコピー（コマンドライン版で更新したときなど）
    tmp = Path(tempfile.gettempdir()) / f"clipdl-updater-{int(time.time())}-{os.getpid()}.exe"
    shutil.copy2(p.install_dir / UPDATER_EXE, tmp)
    cmd = [str(tmp), str(pp)]
    log.info("入れ替え用プログラムを起動します: %s", cmd)
    if launch:
        launch(cmd)
    else:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
        subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=tempfile.gettempdir())
    return plan


@dataclass
class AfterUpdate:
    status: str        # "ok"（新しい版として起動した）/ "rolled_back" / "not_started" / "broken"
    message: str
    version: str = ""


def after_startup() -> AfterUpdate | None:
    """起動直後（画面を出したあと）に呼ぶ。更新の結果があれば返し、記録を片付ける。

    - 入れ替え用プログラムから起動された新しい版なら「起動できた」印を書く（これが無いと巻き戻される）
    - 元の版に戻された場合は、その理由を返す
    """
    pp, res = plan_path(), result_path()
    out: AfterUpdate | None = None
    try:
        plan = json.loads(pp.read_text("utf-8")) if pp.is_file() else None
    except (OSError, ValueError):
        plan = None
    try:
        r = json.loads(res.read_text("utf-8")) if res.is_file() else {}
    except (OSError, ValueError):
        r = {}
    # 1. 元の版に戻された・更新しなかった結果があれば、それを最優先で知らせる。
    #    入れ替え用プログラムは、この結果を書いてから元の版を起動する（成功のときは新しい版の起動後に書く）
    if r.get("status") and r.get("status") != "ok":
        out = AfterUpdate(str(r["status"]), str(r.get("message", "")), str(r.get("new_version", "")))
        _cleanup_files(pp, res)
        _cleanup_temp_updaters()
        return out
    # 2. 入れ替え用プログラムから起動された新しい版なら「起動できた」印を書く
    if plan and plan.get("new_version") == __version__ and install_dir() is not None \
            and Path(plan.get("install_dir", "")).resolve() == install_dir():
        ok_marker_path().write_text(json.dumps({"version": __version__, "time": time.time()}), "utf-8")
        out = AfterUpdate("ok", f"clipdl {__version__} に更新しました。", __version__)
        _cleanup_files(pp, res, wait_result=True)
        _cleanup_temp_updaters()   # 今動いているコピーは消せないので残り、次の起動で消える
        return out
    if res.is_file():   # 前回の成功の記録（status = ok）を片付ける
        _cleanup_files(pp, res)
    _cleanup_temp_updaters()
    return out


def _cleanup_files(*paths: Path, wait_result: bool = False) -> None:
    for p in paths:
        if wait_result and p == result_path():
            continue   # 入れ替え用プログラムがこれから書く。次回の起動で片付ける
        try:
            p.unlink()
        except OSError:
            pass


def _cleanup_temp_updaters(temp_dir: Path | None = None) -> int:
    """一時フォルダに残った入れ替え用プログラムのコピーを消す（消せた数を返す）。

    コピーは更新のたびに 1 つ作られる（約 8 MB）。まだ動いているコピーは Windows が使用中として
    消させないので、年齢は見ずに、消せるものを全部消す。
    """
    n = 0
    for f in Path(temp_dir or tempfile.gettempdir()).glob("clipdl-updater-*.exe"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass   # 動いている（入れ替えの途中）・権限が無いものは、次の起動で
    if n:
        log.info("一時フォルダの入れ替え用プログラムのコピーを %d 個消しました", n)
    return n
