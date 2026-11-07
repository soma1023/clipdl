"""リリースの zip に署名する（build.py のあとに実行。パスフレーズを尋ねます）。

使い方:
  .venv\\Scripts\\python.exe packaging\\sign_release.py [秘密鍵のパス]

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

from Cryptodome.PublicKey import ECC  # noqa: E402

from clipdl import __version__  # noqa: E402
from clipdl.core.selfupdate import (asset_names, key_id, load_trusted_keys, make_manifest, sign,  # noqa: E402
                                    verify_manifest)

RELEASE = ROOT / "build" / "release"
DEFAULT_KEY = Path.home() / "clipdl-keys" / "clipdl-release-ed25519.pem"
PASSPHRASE_ENV = "CLIPDL_SIGNING_PASSPHRASE"   # 自動テスト用（ふだんは入力欄で尋ねる）


def main() -> int:
    key_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_KEY
    version = os.environ.get("CLIPDL_SIGN_VERSION", __version__)
    zip_name, manifest_name, sig_name = asset_names(version)
    zip_path = RELEASE / zip_name
    if not zip_path.is_file():
        print(f"{zip_path} がありません。先に packaging\\build.py を実行してください。", file=sys.stderr)
        return 1
    passphrase = os.environ.get(PASSPHRASE_ENV) or getpass.getpass(f"{key_path.name} のパスフレーズ: ")
    try:
        key = ECC.import_key(key_path.read_text("ascii"), passphrase=passphrase)
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
