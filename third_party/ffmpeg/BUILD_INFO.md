# 同梱 ffmpeg のビルド情報

記録日: 2026-11-06（packaging/record_ffmpeg_build.py で生成）

## バージョン
- ffmpeg: `n9.0.2-47-gb204e94e90-20261105`（ビルド日 20261105）
- ベースのタグ: `n9.0.2`（このタグから 47 コミット先の版。下の compare で確認できる）
- FFmpeg のコミット: `b204e94e90`
- ライセンス: **LGPL v3 or later**（`--enable-version3`。`--enable-gpl` / `--enable-nonfree` は無し）
- 形式: DLL 分離版（shared）。利用者が DLL を差し替えられる

## ソースの入手先（LGPL v3 第4・6条の「対応するソース」）
- FFmpeg 本体（このコミットのソース一式）:
  - https://github.com/FFmpeg/FFmpeg/tree/b204e94e90
  - tarball: https://github.com/FFmpeg/FFmpeg/archive/b204e94e90.tar.gz
  - タグとの差分: https://github.com/FFmpeg/FFmpeg/compare/n9.0.2...b204e94e90
  - 公式リポジトリ: https://git.ffmpeg.org/ffmpeg.git
- ビルド手順（BtbN/FFmpeg-Builds）:
  - https://github.com/BtbN/FFmpeg-Builds/tree/c63bb308a1de656b425c132a8102f8fd5389afc5
  - 外部ライブラリのバージョンは上記リポジトリの scripts.d/ に記載
- 配布物の取得元（固定リリース）: https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-11-05-12-48/ffmpeg-n9.0.2-47-gb204e94e90-win64-lgpl-shared-9.0.zip
  - リリースページ: https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-11-05-12-48
  - zip のハッシュは vendor/CHECKSUMS.txt を参照（GitHub の表示値・公式 checksums.sha256 と一致を確認済み）

> 配布時の方針（フェーズ3）: 上記 URL は第三者のサーバーなので、リリースごとに FFmpeg のソース tarball と
> ビルドスクリプトを自分たちの配布場所にも保存し、THIRD_PARTY_LICENSES から案内する。

## GPL 部品の確認
- 問題なし: GPL / nonfree の部品は含まれていない
- 無効を確認した部品: libx264, libx265, libxvid, libvidstab, frei0r, librubberband, libdavs2, libxavs, libxavs2, avisynth, libcdio, libfdk-aac

## 有効な外部ライブラリ（THIRD_PARTY_LICENSES に個別のライセンスを載せる）
- libaom
- libaribb24
- libaribcaption
- libass
- libbluray
- libdav1d
- libfreetype
- libfribidi
- libgme
- libharfbuzz
- libjxl
- libkvazaar
- liblcevc-dec
- libmp3lame
- liboapv
- libopencore-amrnb
- libopencore-amrwb
- libopenh264
- libopenjpeg
- libopenmpt
- libopus
- libplacebo
- librav1e
- librist
- librsvg
- libsnappy
- libsoxr
- libsrt
- libssh
- libsvtav1
- libtheora
- libtwolame
- libuavs3d
- libvmaf
- libvorbis
- libvpl
- libvpx
- libvvenc
- libwebp
- libxml2
- libzimg
- libzmq
- libzvbi

## その他の有効な機能
amf, chromaprint, cuda-llvm, ffnvcodec, fontconfig, gmp, iconv, lcms2, lv2, lzma, openal, opencl, schannel, sdl2, vaapi, vapoursynth, vulkan, zlib

## ビルド設定の全文
buildconf.txt を参照
