"""リリースの zip に署名する（build.py のあとに実行。パスフレーズを尋ねます）。

使い方:
  .venv\\Scripts\\python.exe packaging\\sign_release.py [秘密鍵のパス]
  .venv\\Scripts\\python.exe packaging\\sign_release.py --save-passphrase    パスフレーズを 1 回だけ入力して保存
  .venv\\Scripts\\python.exe packaging\\sign_release.py --forget-passphrase  保存したパスフレーズを消す

パスフレーズを保存しておくと（Windows の DPAPI で暗号化。この PC のこのユーザーでしか読めない）、
次からは入力なしで署名できる。保存していなければ、これまでどおり入力欄で尋ねる。

作るもの（build/release/ に置く。zip・.sha256 と一緒にリリースに添付する）:
  clipdl-<版>-win64.update.json       更新情報（版・zip の名前・サイズ・SHA-256・鍵 ID）
  clipdl-<版>-win64.update.json.sig   その Ed25519 署名（base64）

署名したあと、アプリに埋め込んだ公開鍵で検証し直し、通らなければ失敗にする。
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dpapi  # noqa: E402

from Cryptodome.PublicKey import ECC  # noqa: E402

from clipdl import __version__  # noqa: E402
from clipdl.core.selfupdate import (asset_names, key_id, load_trusted_keys, make_manifest, sign,  # noqa: E402
                                    verify_manifest)

RELEASE = ROOT / "build" / "release"
DEFAULT_KEY = Path.home() / "clipdl-keys" / "clipdl-release-ed25519.pem"
PASSPHRASE_ENV = "CLIPDL_SIGNING_PASSPHRASE"   # 自動テスト用（ふだんは入力欄で尋ねる）


def _read_key(key_path: Path, passphrase: str):
    return ECC.import_key(key_path.read_text("ascii"), passphrase=passphrase)


def save_passphrase(key_path: Path) -> int:
    """パスフレーズを入力してもらい、秘密鍵を開けることを確かめてから DPAPI で保存する。"""
    passphrase = getpass.getpass(f"{key_path.name} のパスフレーズ（保存します）: ")
    try:
        _read_key(key_path, passphrase)
    except (OSError, ValueError) as e:
        print(f"秘密鍵を開けません（パスフレーズの間違いなど）。保存しませんでした: {e}", file=sys.stderr)
        return 1
    path = dpapi.save(passphrase)
    if dpapi.load() != passphrase:
        print("保存したパスフレーズを読み戻せませんでした。", file=sys.stderr)
        return 1
    print(f"保存しました: {path}")
    print("次からは入力なしで署名できます。やめるときは --forget-passphrase を付けて実行してください。")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    key_path = Path(args[0]) if args else DEFAULT_KEY
    if "--save-passphrase" in sys.argv:
        return save_passphrase(key_path)
    if "--forget-passphrase" in sys.argv:
        print("消しました。" if dpapi.forget() else "保存されたパスフレーズはありません。")
        return 0
    version = os.environ.get("CLIPDL_SIGN_VERSION", __version__)
    zip_name, manifest_name, sig_name = asset_names(version)
    zip_path = RELEASE / zip_name
    if not zip_path.is_file():
        print(f"{zip_path} がありません。先に packaging\\build.py を実行してください。", file=sys.stderr)
        return 1
    saved = dpapi.load()
    if saved and not os.environ.get(PASSPHRASE_ENV):
        print(f"保存したパスフレーズを使います（{dpapi.STORE}）")
    passphrase = os.environ.get(PASSPHRASE_ENV) or saved or getpass.getpass(f"{key_path.name} のパスフレーズ: ")
    try:
        key = _read_key(key_path, passphrase)
    except (OSError, ValueError) as e:
        print(f"秘密鍵を読めません（パスフレーズの間違いなど）: {e}", file=sys.stderr)
        return 1
    kid = key_id(key.public_key())
    trusted = load_trusted_keys()
    if kid not in trusted:
        print(f"この秘密鍵（鍵 ID {kid}）の公開鍵がアプリに埋め込まれていません。", file=sys.stderr)
        return 1
    manifest = make_manifest(version, zip_path, kid)
    sig = sign(manifest, key)
    # 埋め込んだ公開鍵で検証し直す（アプリと同じ手順。今の版は 1 つ前とみなす）
    verify_manifest(manifest, sig, trusted, expected_version=version, current="0.0.0")
    (RELEASE / manifest_name).write_bytes(manifest)
    (RELEASE / sig_name).write_bytes(sig)
    print(f"更新情報: {RELEASE / manifest_name}")
    print(f"署名:     {RELEASE / sig_name}（鍵 ID {kid}・検証済み）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
