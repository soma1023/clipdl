"""配布物を作る: PyInstaller → vendor と文書をコピー → zip と SHA-256。

使い方: .venv\\Scripts\\python.exe packaging\\build.py [--skip-pyinstaller]
出力:
  build/dist/clipdl/                 配布フォルダ（clipdl.exe = GUI、clipdl-cli.exe = コマンドライン）
  build/release/clipdl-<版>-win64.zip
  build/release/clipdl-<版>-win64.zip.sha256   （リリースに載せる SHA-256）
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from clipdl import __version__  # noqa: E402

DIST = ROOT / "build" / "dist" / "clipdl"
RELEASE = ROOT / "build" / "release"
VENDOR = ROOT / "vendor"

# vendor から配布物に入れるもの（開発用の lib / include / pdb は入れない）
VENDOR_FILES = [
    ("ffmpeg/bin", "*.exe"), ("ffmpeg/bin", "*.dll"), ("ffmpeg", "LICENSE.txt"),
    ("deno", "deno.exe"),
    ("TwitchDownloaderCLI", "TwitchDownloaderCLI.exe"), ("TwitchDownloaderCLI", "COPYRIGHT.txt"),
    ("TwitchDownloaderCLI", "THIRD-PARTY-LICENSES.txt"),
]
DOCS = ["README.md", "LICENSE", "TERMS.md", "THIRD_PARTY_LICENSES.txt"]


def run_pyinstaller() -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "clipdl.spec"), "--noconfirm",
                    "--distpath", str(ROOT / "build" / "dist"), "--workpath", str(ROOT / "build" / "work"),
                    "--log-level", "WARN"], check=True, cwd=ROOT)


def copy_vendor() -> None:
    for sub, pattern in VENDOR_FILES:
        files = sorted((VENDOR / sub).glob(pattern))
        if not files:
            raise SystemExit(f"vendor/{sub}/{pattern} がありません")
        for f in files:
            dest = DIST / "vendor" / sub / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    # ffmpeg のビルド情報・ソースの入手先（LGPL v3 対応）
    shutil.copytree(ROOT / "third_party", DIST / "third_party", dirs_exist_ok=True)


def copy_updater_and_version() -> None:
    """入れ替え用プログラムと、版番号のファイル（自動更新で展開した版を確かめるのに使う）を置く。"""
    upd = ROOT / "build" / "dist" / "clipdl-updater.exe"
    if not upd.is_file():
        raise SystemExit(f"{upd} がありません（PyInstaller の出力を確認してください）")
    shutil.copy2(upd, DIST / "clipdl-updater.exe")
    (DIST / "VERSION").write_bytes((__version__ + "\n").encode("ascii"))


def copy_docs() -> None:
    for name in DOCS:
        src = ROOT / name
        if not src.exists():
            raise SystemExit(f"{name} がありません（packaging/collect_licenses.py などで先に作ってください）")
        shutil.copy2(src, DIST / name)


def make_zip() -> Path:
    RELEASE.mkdir(parents=True, exist_ok=True)
    out = RELEASE / f"clipdl-{__version__}-win64.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(DIST.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, Path("clipdl") / f.relative_to(DIST))
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    # 改行は LF（sha256sum -c でもそのまま照合できるように。write_text だと Windows では CRLF になる）
    (out.parent / (out.name + ".sha256")).write_bytes(f"{digest}  {out.name}\n".encode())
    return out


def preflight() -> None:
    """壊れたまま配布物を作らないよう、構文チェックとテストを先に通す。"""
    subprocess.run([sys.executable, "-m", "compileall", "-q", str(ROOT / "src")], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], check=True, cwd=ROOT)


def main() -> int:
    if "--skip-tests" not in sys.argv:
        preflight()
    if "--skip-pyinstaller" not in sys.argv:
        run_pyinstaller()
    copy_vendor()
    copy_updater_and_version()
    copy_docs()
    z = make_zip()
    print(f"配布フォルダ: {DIST}")
    print(f"zip: {z}（{z.stat().st_size / 1048576:.0f} MB）")
    print((z.parent / (z.name + ".sha256")).read_text("utf-8").strip())
    print(r"次に packaging\sign_release.py で署名してください（更新情報と署名ファイルを作ります）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
