"""リリースに署名する Ed25519 の鍵を作る（最初に 1 回だけ。鍵を替えるときも使う）。

使い方（ご自分のターミナルで実行。パスフレーズを 2 回尋ねます）:
  .venv\\Scripts\\python.exe packaging\\keygen.py

- 秘密鍵: パスフレーズで暗号化して %USERPROFILE%\\clipdl-keys\\clipdl-release-ed25519.pem に保存
  （OneDrive・リポジトリの中には保存しない。既にあれば上書きしない）
- 公開鍵: src/clipdl/core/keys/clipdl_release_<鍵ID>.pub に書き出す（アプリに埋め込まれる。git に入れてよい）
"""
from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from Cryptodome.PublicKey import ECC  # noqa: E402

from clipdl.core.selfupdate import KEYS_DIR, key_id  # noqa: E402

DEFAULT_DIR = Path.home() / "clipdl-keys"
KEY_NAME = "clipdl-release-ed25519.pem"
PROTECTION = "PBKDF2WithHMAC-SHA512AndAES256-CBC"


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    out_dir = out_dir.resolve()
    if "onedrive" in str(out_dir).lower():
        print(f"OneDrive の中（{out_dir}）には保存しません。別の場所を指定してください。", file=sys.stderr)
        return 1
    if ROOT in out_dir.parents or out_dir == ROOT:
        print("リポジトリの中には保存しません。", file=sys.stderr)
        return 1
    key_path = out_dir / KEY_NAME
    if key_path.exists():
        print(f"{key_path} は既にあります。上書きしません（鍵を替えるときは、先に手で別名にしてください）。",
              file=sys.stderr)
        return 1

    print("秘密鍵を守るパスフレーズを決めてください（12 文字以上。リリースのたびに入力します）。")
    while True:
        p1 = getpass.getpass("パスフレーズ: ")
        if len(p1) < 12:
            print("12 文字以上にしてください。")
            continue
        if getpass.getpass("もう一度: ") != p1:
            print("一致しません。もう一度入力してください。")
            continue
        break

    key = ECC.generate(curve="Ed25519")
    pub = key.public_key()
    kid = key_id(pub)
    out_dir.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key.export_key(format="PEM", passphrase=p1, protection=PROTECTION), "ascii")
    # 読めるのを自分のアカウントだけにする（継承を切り、自分にだけフル権限）
    user = getpass.getuser()
    for target in (out_dir, key_path):
        subprocess.run(["icacls", str(target), "/inheritance:r", "/grant:r", f"{user}:(F)"],
                       check=False, capture_output=True)
    # 読み戻して確かめる
    ECC.import_key(key_path.read_text("ascii"), passphrase=p1)

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    pub_path = KEYS_DIR / f"clipdl_release_{kid}.pub"
    pub_path.write_text(pub.export_key(format="PEM") + "\n", "ascii")
    print()
    print(f"秘密鍵（暗号化済み）: {key_path}")
    print(f"公開鍵（アプリに埋め込む）: {pub_path}")
    print(f"鍵 ID: {kid}")
    print("次に、秘密鍵のファイルを USB メモリなどオフラインの場所に 2 つコピーし、")
    print("パスフレーズはパスワードマネージャーに保存してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
