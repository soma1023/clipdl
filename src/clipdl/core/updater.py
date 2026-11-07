"""yt-dlp / yt-dlp-ejs の更新（確認・取得・検証・記録）と、Deno の版の表示。

yt-dlp:
  1. GitHub の最新リリースから SHA2-256SUMS と SHA2-256SUMS.sig を取得
  2. 署名を検証（core/pgp.py。アプリに埋め込んだ yt-dlp の鍵と指紋で）
  3. zipimport 版の `yt-dlp` を取得し、SHA2-256SUMS に書かれた SHA-256 と照合
  4. 中の yt_dlp/version.py の版がリリースの版と一致することを確認
yt-dlp-ejs:
  yt-dlp がその版で指定する yt-dlp-ejs（PyPI の requires_dist）を、PyPI が示す SHA-256 と照合して取得
記録:
  data_dir()/updates/ に置き、manifest.json に検証が通ったときの SHA-256 を書く。
  起動のたびに core/bootstrap.py が照合し、一致したものだけを読み込む（次回の起動から反映）。
Deno:
  自動更新の対象にしない（アプリの新しい版を出すときに更新する）。版の表示だけ行う。

テスト用: 環境変数 CLIPDL_TEST_TREAT_BUNDLED_OLD=1 で、同梱版（と使用中の版）を古いものとみなす。
  最新版が同梱版と同じでも、取得〜検証〜記録の流れを確かめられる。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from .bootstrap import ACTIVE, MANIFEST, read_manifest, sha256_file, updates_dir
from .errors import UserError
from .paths import env_flag, find_binary
from .pgp import YTDLP_FINGERPRINT, SignatureError, load_ytdlp_key, verify_detached

log = logging.getLogger(__name__)
_UA = {"User-Agent": "clipdl-updater"}
GITHUB_LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
ZIPIMPORT_ASSET = "yt-dlp"          # 「Platform-independent zipimport binary」
TEST_TREAT_OLD = "CLIPDL_TEST_TREAT_BUNDLED_OLD"


def _treat_old() -> bool:
    return env_flag(TEST_TREAT_OLD)


# ---------------------------------------------------------------------- 確認


@dataclass
class ComponentStatus:
    name: str
    installed: str | None
    latest: str | None
    error: str | None = None
    source: str = "同梱版"        # 「同梱版」/「更新版」
    updatable: bool = True       # Deno は False（表示のみ）

    @property
    def outdated(self) -> bool:
        if not (self.installed and self.latest):
            return False
        return _treat_old() and self.updatable or _norm(self.installed) != _norm(self.latest)

    def summary(self) -> str:
        if self.error:
            return f"{self.name}: 確認できませんでした"
        if not self.updatable:
            return f"{self.name}: {self.installed}（アプリの更新時に更新）"
        if self.outdated:
            return f"{self.name}: {self.installed}（{self.source}）→ {self.latest} があります"
        return f"{self.name}: {self.installed}（{self.source}・最新）"


def _norm(v: str) -> str:
    return v.lstrip("v").strip()


def _get_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={**_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get_bytes(url: str, timeout: float = 60.0) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return r.read()


def current_ytdlp_version() -> str | None:
    """いま読み込まれている yt-dlp の版（更新版を使っていればその版）。"""
    try:
        import yt_dlp.version as v
        return v.__version__
    except ImportError:
        return None


def current_ejs_version() -> str | None:
    if "yt-dlp-ejs" in ACTIVE:
        return ACTIVE["yt-dlp-ejs"].version
    try:
        return metadata.version("yt-dlp-ejs")
    except metadata.PackageNotFoundError:
        return None


def _deno_version() -> str | None:
    exe = find_binary("deno")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"deno (\S+)", out)
    return m.group(1) if m else None


def check_updates() -> list[ComponentStatus]:
    out = []
    yt = ComponentStatus("yt-dlp", current_ytdlp_version(), None,
                         source="更新版" if "yt-dlp" in ACTIVE else "同梱版")
    try:
        yt.latest = _get_json(GITHUB_LATEST)["tag_name"]
    except Exception as e:  # noqa: BLE001 — ネットワークの失敗は表示だけにとどめる
        yt.error = str(e)
    out.append(yt)
    ejs = ComponentStatus("yt-dlp-ejs", current_ejs_version(), None,
                          source="更新版" if "yt-dlp-ejs" in ACTIVE else "同梱版")
    try:  # 最新の yt-dlp が指定する版を「最新」として表示する
        ejs.latest = required_ejs_version(yt.latest) if yt.latest else None
    except Exception as e:  # noqa: BLE001
        ejs.error = str(e)
    out.append(ejs)
    out.append(ComponentStatus("Deno", _deno_version(), None, updatable=False))
    log.info("更新確認: %s", " / ".join(s.summary() for s in out))
    return out


# ---------------------------------------------------------------------- 取得・検証


def parse_sums(text: str) -> dict[str, str]:
    """SHA2-256SUMS（「ハッシュ  ファイル名」）を辞書にする。"""
    out = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            out[parts[1].lstrip("*")] = parts[0]
    return out


def zip_ytdlp_version(path: Path) -> str:
    """zipimport 版の中の yt_dlp/version.py から版を読む（import はしない）。"""
    with zipfile.ZipFile(path) as z:
        text = z.read("yt_dlp/version.py").decode("utf-8")
    m = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    if not m:
        raise UserError("update_bad_file", "更新ファイルの版を読めませんでした。", "Could not read the update version.",
                        "version.py without __version__")
    return m.group(1)


def required_ejs_version(ytdlp_version: str) -> str | None:
    """その版の yt-dlp が指定する yt-dlp-ejs の版（PyPI の requires_dist から）。"""
    info = _get_json(f"https://pypi.org/pypi/yt-dlp/{ytdlp_version}/json")["info"]
    for req in info.get("requires_dist") or []:
        m = re.match(r"yt-dlp-ejs\s*==\s*([\w.]+)", req)
        if m:
            return m.group(1)
    return None


def _fail(code: str, ja: str, raw: str) -> UserError:
    return UserError(code, ja, "The update could not be verified. The bundled version will be used.", raw)


@dataclass
class UpdateResult:
    ytdlp_version: str
    ejs_version: str | None
    notes: list[str]


def update(progress=None) -> UpdateResult:
    """最新の yt-dlp（と対になる yt-dlp-ejs）を取得・検証して記録する。反映は次回の起動から。

    どこかで検証に失敗したら UserError を投げ、manifest.json は書き換えない（同梱版／今の版で動き続ける）。
    """
    def step(msg):
        log.info("更新: %s", msg)
        if progress:
            progress(msg)

    notes: list[str] = []
    root = updates_dir()
    step("最新版を確認中")
    rel = _get_json(GITHUB_LATEST)
    tag = rel["tag_name"]
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    for need in (ZIPIMPORT_ASSET, "SHA2-256SUMS", "SHA2-256SUMS.sig"):
        if need not in assets:
            raise _fail("update_missing", f"更新ファイル（{need}）がリリースにありません。", str(sorted(assets)))

    step("署名を検証中")
    sums = _get_bytes(assets["SHA2-256SUMS"])
    sig = _get_bytes(assets["SHA2-256SUMS.sig"])
    try:
        info = verify_detached(sums, sig, load_ytdlp_key())
    except SignatureError as e:
        raise _fail("update_signature", f"yt-dlp の署名を検証できませんでした（{e}）。同梱版で動きます。",
                    f"SHA2-256SUMS.sig: {e}") from e
    notes.append(f"署名を検証（鍵 {YTDLP_FINGERPRINT[-16:]}、{info.hash_name}）")
    expected = parse_sums(sums.decode("utf-8", "replace")).get(ZIPIMPORT_ASSET)
    if not expected:
        raise _fail("update_sums", "SHA2-256SUMS に yt-dlp の行がありません。", sums[:500].decode("utf-8", "replace"))

    step(f"yt-dlp {tag} を取得中")
    tmp = Path(tempfile.mkdtemp(prefix="clipdl-update-", dir=root))
    try:
        yt_tmp = tmp / "yt-dlp.zip"
        yt_tmp.write_bytes(_get_bytes(assets[ZIPIMPORT_ASSET], timeout=180))
        actual = sha256_file(yt_tmp)
        if actual != expected:
            raise _fail("update_hash", "yt-dlp のファイルが署名付きのハッシュと一致しません。",
                        f"expected {expected} actual {actual}")
        inner = zip_ytdlp_version(yt_tmp)
        if _norm(inner) != _norm(tag):
            raise _fail("update_version", "yt-dlp のファイルの版がリリースと一致しません。", f"{inner} != {tag}")
        notes.append(f"yt-dlp {tag}: SHA-256 一致（{actual[:12]}…）")

        ejs_entry = None
        ejs_ver = required_ejs_version(tag)
        cur_ejs = current_ejs_version()
        if ejs_ver and (_treat_old() or ejs_ver != cur_ejs):
            step(f"yt-dlp-ejs {ejs_ver} を取得中")
            ejs_entry = _fetch_ejs(ejs_ver, tmp, notes)
        elif ejs_ver:
            notes.append(f"yt-dlp-ejs {ejs_ver}: いまの版のまま（更新不要）")

        # 検証が通ったものだけを置き、最後に manifest を書き換える（途中で失敗しても前の状態のまま）
        step("記録中")
        old = read_manifest(root)
        manifest = {
            "yt-dlp": {"version": tag, "file": f"yt-dlp-{tag}.zip", "sha256": actual,
                       "verified_at": int(time.time()), "key_fingerprint": YTDLP_FINGERPRINT,
                       "signature_created": info.created},
        }
        yt_tmp.replace(root / manifest["yt-dlp"]["file"])
        if ejs_entry:
            (tmp / ejs_entry["file"]).replace(root / ejs_entry["file"])
            manifest["yt-dlp-ejs"] = ejs_entry
        elif "yt-dlp-ejs" in old and old["yt-dlp-ejs"].get("version") == ejs_ver:
            manifest["yt-dlp-ejs"] = old["yt-dlp-ejs"]   # 前に取った ejs がそのまま使える
        tmp_manifest = root / (MANIFEST + ".tmp")
        tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
        tmp_manifest.replace(root / MANIFEST)
        _remove_unreferenced(root, manifest)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)  # アプリが作った一時フォルダ
    notes.append("次回の起動から更新版を使います")
    log.info("更新を記録: %s", "; ".join(notes))
    return UpdateResult(tag, manifest.get("yt-dlp-ejs", {}).get("version"), notes)


def _fetch_ejs(version: str, tmp: Path, notes: list[str]) -> dict:
    data = _get_json(f"https://pypi.org/pypi/yt-dlp-ejs/{version}/json")
    wheel = next((f for f in data.get("urls", []) if f.get("packagetype") == "bdist_wheel"
                  and f["filename"].endswith("-py3-none-any.whl")), None)
    if wheel is None:
        raise _fail("update_ejs", "yt-dlp-ejs の配布ファイルが見つかりません。", json.dumps(data.get("urls"))[:500])
    body = _get_bytes(wheel["url"], timeout=120)
    actual = hashlib.sha256(body).hexdigest()
    if actual != wheel["digests"]["sha256"]:
        raise _fail("update_ejs_hash", "yt-dlp-ejs のファイルが PyPI のハッシュと一致しません。",
                    f"expected {wheel['digests']['sha256']} actual {actual}")
    (tmp / wheel["filename"]).write_bytes(body)
    notes.append(f"yt-dlp-ejs {version}: PyPI の SHA-256 と一致（{actual[:12]}…）")
    return {"version": version, "file": wheel["filename"], "sha256": actual, "verified_at": int(time.time())}


def _remove_unreferenced(root: Path, manifest: dict) -> None:
    """記録に無くなった古い更新ファイルを片付ける（アプリが置いたものだけ）。"""
    keep = {e["file"] for e in manifest.values()} | {MANIFEST}
    for p in root.iterdir():
        if p.is_file() and p.name not in keep and (p.name.startswith("yt-dlp-") or p.name.startswith("yt_dlp_ejs-")):
            try:
                p.unlink()
            except OSError:
                pass


def reset_updates() -> None:
    """更新版の記録を消して、次回から同梱版を使う。"""
    root = updates_dir()
    (root / MANIFEST).unlink(missing_ok=True)
    _remove_unreferenced(root, {})
