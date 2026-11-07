"""配布物に入る第三者ソフトウェアのライセンスを THIRD_PARTY_LICENSES.txt にまとめる。

使い方: .venv\\Scripts\\python.exe packaging\\collect_licenses.py
- Python パッケージは requirements.lock（配布用）に載っているものだけを対象にする
- ffmpeg のソースの入手先は third_party/ffmpeg/BUILD_INFO.md（record_ffmpeg_build.py が作成）から入れる
- TwitchDownloader の COPYRIGHT.txt は「変更せずに含める」条件があるので、そのまま全文を入れる
"""
from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "THIRD_PARTY_LICENSES.txt"
VENDOR = ROOT / "vendor"
SEP = "=" * 78


def section(title: str, body: str) -> str:
    return f"\n{SEP}\n{title}\n{SEP}\n\n{body.rstrip()}\n"


def runtime_packages() -> list[str]:
    names = []
    for line in (ROOT / "requirements.lock").read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "==" in line:
            names.append(line.split("==")[0])
    return names


def package_section(name: str) -> str:
    d = metadata.distribution(name)
    md = d.metadata
    lic = md.get("License-Expression") or md.get("License") or ""
    if len(lic) > 200:  # License 欄に全文が入っている場合
        lic = lic.splitlines()[0]
    classifiers = [c.split(" :: ")[-1] for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    home = md.get("Home-page") or next((u.split(", ", 1)[-1] for u in md.get_all("Project-URL") or []
                                         if re.match(r"(?i)(source|repository|homepage)", u)), "")
    head = [f"バージョン: {d.version}", f"ライセンス: {lic or ', '.join(classifiers) or '（記載なし）'}"]
    if home:
        head.append(f"入手先: {home}")
    texts = []
    for f in d.files or []:
        p = str(f).replace("\\", "/")
        if re.search(r"(?i)(^|/)(licen[cs]e|copying|notice|authors)[^/]*$", p) and ".dist-info/" in p:
            try:
                texts.append(f"--- {Path(p).name} ---\n" + Path(d.locate_file(f)).read_text("utf-8", "replace"))
            except OSError:
                continue
    return section(f"{name}（Python パッケージ）", "\n".join(head) + ("\n\n" + "\n\n".join(texts) if texts else ""))


def main() -> int:
    parts = [
        "clipdl に同梱している第三者のソフトウェアと、そのライセンス\n",
        "このファイルは packaging/collect_licenses.py で自動生成しています。",
        "各ソフトウェアの著作権はそれぞれの権利者に帰属します。",
        "clipdl 本体は MIT ライセンスです（LICENSE を参照）。MIT が適用されるのは clipdl 本体だけで、"
        "以下の第三者のソフトウェアにはそれぞれのライセンスが適用されます。",
    ]

    # --- ffmpeg（LGPL v3）
    info = (ROOT / "third_party" / "ffmpeg" / "BUILD_INFO.md").read_text("utf-8")
    parts.append(section(
        "FFmpeg（vendor/ffmpeg）— GNU LGPL v3 or later",
        "同梱しているのは LGPL 版のビルド（GPL / nonfree の部品なし）で、DLL を分けた形式です。\n"
        "vendor/ffmpeg/bin の DLL は利用者が差し替えられます。\n"
        "ビルド情報とソースの入手先（LGPL v3 第4・6条の「対応するソース」）は次のとおりです。\n"
        "配布物の third_party/ffmpeg/BUILD_INFO.md と buildconf.txt にも同じ内容があります。\n\n"
        + info + "\n\n--- LICENSE.txt（GNU LGPL v3）---\n" + (VENDOR / "ffmpeg" / "LICENSE.txt").read_text("utf-8")))

    # --- TwitchDownloaderCLI（MIT。COPYRIGHT.txt は変更せずに含める）
    td = VENDOR / "TwitchDownloaderCLI"
    parts.append(section(
        "TwitchDownloaderCLI（vendor/TwitchDownloaderCLI）— MIT License（lay295）",
        "入手先: https://github.com/lay295/TwitchDownloader\n"
        "以下の COPYRIGHT.txt は、配布条件に従い変更せずに含めています。\n\n"
        "--- COPYRIGHT.txt ---\n" + (td / "COPYRIGHT.txt").read_text("utf-8", "replace")
        + "\n\n--- THIRD-PARTY-LICENSES.txt ---\n" + (td / "THIRD-PARTY-LICENSES.txt").read_text("utf-8", "replace")))

    # --- Deno（MIT）
    parts.append(section(
        "Deno（vendor/deno）— MIT License",
        "入手先: https://github.com/denoland/deno（v2.9.8）\n"
        "Deno には V8 などの部品が含まれます。各部品のライセンスは Deno のリポジトリを参照してください。\n\n"
        + (ROOT / "third_party" / "deno" / "LICENSE.md").read_text("utf-8")))

    # --- Python 本体（PSF）
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    parts.append(section(f"Python {sys.version.split()[0]}（_internal/python312.dll など）— PSF License",
                         py_license.read_text("utf-8", "replace") if py_license.exists() else "https://docs.python.org/3/license.html"))

    # --- PyInstaller のブートローダー（GPL + 例外）
    pyi = metadata.distribution("pyinstaller")
    pyi_text = ""
    for f in pyi.files or []:
        if re.search(r"(?i)licen[cs]e|copying", str(f)) and ".dist-info" in str(f):
            pyi_text += Path(pyi.locate_file(f)).read_text("utf-8", "replace") + "\n"
    parts.append(section(
        f"PyInstaller {pyi.version} のブートローダー（clipdl.exe / clipdl-cli.exe の起動部分）— GPL v2 or later（ブートローダー例外つき）",
        "PyInstaller のブートローダーには、作った実行ファイルを任意のライセンスで配布してよいという例外が付いています。\n\n"
        + pyi_text))

    # --- Qt（PySide6）
    parts.append(section(
        "Qt / Qt for Python（PySide6・shiboken6）— GNU LGPL v3",
        "Qt の DLL（_internal/PySide6）は利用者が差し替えられます。\n"
        "Qt のソース: https://download.qt.io/official_releases/qt/\n"
        "Qt for Python のソース: https://download.qt.io/official_releases/QtForPython/pyside6/\n"
        "（同梱の版は下の PySide6_Essentials / shiboken6 の項を参照）"))

    # --- Python パッケージ
    for name in runtime_packages():
        try:
            parts.append(package_section(name))
        except metadata.PackageNotFoundError:
            parts.append(section(name, "（開発環境に見つかりません）"))

    OUT.write_text("\n".join(parts) + "\n", "utf-8")
    print(f"{OUT}（{OUT.stat().st_size // 1024} KB、{len(runtime_packages())} パッケージ）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
