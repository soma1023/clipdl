"""同梱 ffmpeg のビルド情報を third_party/ffmpeg/ に記録する（LGPL v3 対応・THIRD_PARTY_LICENSES 用）。

使い方: .venv\\Scripts\\python.exe packaging\\record_ffmpeg_build.py
vendor/ffmpeg を差し替えたら必ず実行し直すこと。
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
OUT = ROOT / "third_party" / "ffmpeg"

# 取得元（vendor/CHECKSUMS.txt と合わせて更新する）
AUTOBUILD_TAG = "autobuild-2026-11-05-12-48"
DIST_ASSET = "ffmpeg-n9.0.2-47-gb204e94e90-win64-lgpl-shared-9.0.zip"
DIST_URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{AUTOBUILD_TAG}/{DIST_ASSET}"
BUILD_SCRIPTS_COMMIT = "c63bb308a1de656b425c132a8102f8fd5389afc5"  # 上記 autobuild 時点の BtbN/FFmpeg-Builds master

# GPL / nonfree になる部品（含まれていないことを確認する）
GPL_FLAGS = ("--enable-gpl", "--enable-nonfree")
GPL_LIBS = ("libx264", "libx265", "libxvid", "libvidstab", "frei0r", "librubberband", "libdavs2", "libxavs",
            "libxavs2", "avisynth", "libcdio", "libfdk-aac")


def run(*args: str) -> str:
    return subprocess.run([str(FFMPEG), "-hide_banner", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def main() -> int:
    if not FFMPEG.exists():
        print(f"{FFMPEG} がありません", file=sys.stderr)
        return 1
    version_out = run("-version")
    first = version_out.splitlines()[0]
    m = re.search(r"ffmpeg version (n?(\d+\.\d+(?:\.\d+)?)-(\d+)-g([0-9a-f]+)-(\d{8}))", first)
    if not m:
        print(f"バージョン行を解析できません: {first}", file=sys.stderr)
        return 1
    full_version, base_tag, ahead, commit, build_date = (m.group(1), "n" + m.group(2), m.group(3),
                                                         m.group(4), m.group(5))
    buildconf = run("-buildconf")
    flags = [x.strip() for x in buildconf.split() if x.strip().startswith("--")]
    enabled = set(flags)

    problems = [f for f in GPL_FLAGS if f in enabled]
    problems += [f"--enable-{lib}" for lib in GPL_LIBS if f"--enable-{lib}" in enabled]
    license_line = "LGPL v3 or later" if "--enable-version3" in enabled else "LGPL v2.1 or later"
    if problems:
        license_line = "GPL（同梱不可）"

    libs = sorted(f[len("--enable-"):] for f in flags if f.startswith("--enable-lib"))
    others = sorted(f[len("--enable-"):] for f in flags
                    if f.startswith("--enable-") and not f.startswith("--enable-lib")
                    and f not in ("--enable-shared", "--enable-version3", "--enable-pthreads"))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "buildconf.txt").write_text(version_out + "\n" + buildconf, "utf-8")
    text = f"""# 同梱 ffmpeg のビルド情報

記録日: {date.today().isoformat()}（packaging/record_ffmpeg_build.py で生成）

## バージョン
- ffmpeg: `{full_version}`（ビルド日 {build_date}）
- ベースのタグ: `{base_tag}`（このタグから {ahead} コミット先の版。下の compare で確認できる）
- FFmpeg のコミット: `{commit}`
- ライセンス: **{license_line}**（`--enable-version3`。`--enable-gpl` / `--enable-nonfree` は無し）
- 形式: DLL 分離版（shared）。利用者が DLL を差し替えられる

## ソースの入手先（LGPL v3 第4・6条の「対応するソース」）
- FFmpeg 本体（このコミットのソース一式）:
  - https://github.com/FFmpeg/FFmpeg/tree/{commit}
  - tarball: https://github.com/FFmpeg/FFmpeg/archive/{commit}.tar.gz
  - タグとの差分: https://github.com/FFmpeg/FFmpeg/compare/{base_tag}...{commit}
  - 公式リポジトリ: https://git.ffmpeg.org/ffmpeg.git
- ビルド手順（BtbN/FFmpeg-Builds）:
  - https://github.com/BtbN/FFmpeg-Builds/tree/{BUILD_SCRIPTS_COMMIT}
  - 外部ライブラリのバージョンは上記リポジトリの scripts.d/ に記載
- 配布物の取得元（固定リリース）: {DIST_URL}
  - リリースページ: https://github.com/BtbN/FFmpeg-Builds/releases/tag/{AUTOBUILD_TAG}
  - zip のハッシュは vendor/CHECKSUMS.txt を参照（GitHub の表示値・公式 checksums.sha256 と一致を確認済み）

> 配布時の方針（フェーズ3）: 上記 URL は第三者のサーバーなので、リリースごとに FFmpeg のソース tarball と
> ビルドスクリプトを自分たちの配布場所にも保存し、THIRD_PARTY_LICENSES から案内する。

## GPL 部品の確認
{"- 問題なし: GPL / nonfree の部品は含まれていない" if not problems else "- **GPL 部品あり: " + ", ".join(problems) + "**"}
- 無効を確認した部品: {", ".join(GPL_LIBS)}

## 有効な外部ライブラリ（THIRD_PARTY_LICENSES に個別のライセンスを載せる）
{chr(10).join("- " + x for x in libs)}

## その他の有効な機能
{", ".join(others)}

## ビルド設定の全文
buildconf.txt を参照
"""
    (OUT / "BUILD_INFO.md").write_text(text, "utf-8")
    print(f"記録しました: {OUT / 'BUILD_INFO.md'}")
    print(f"  {full_version} / commit {commit} / {license_line}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
