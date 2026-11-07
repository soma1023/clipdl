# clipdl 実装計画

YouTube / Twitch の動画（VOD・アーカイブ）を、範囲指定とチャット同時取得つきで
ダウンロードするデスクトップアプリ。主な利用者は配信切り抜きの編集者。一般配布前提。

最終更新: 2026-11-06（承認済みの計画＋追加要件 A〜E を反映）

---

## 1. 基本方針

- DL処理は自前実装しない。**yt-dlp を Python ライブラリとして利用**する
- 結合・カット・変換は **ffmpeg / ffprobe**
- **DRM保護コンテンツは非対応**。検出したら丁寧なエラーを出して中止
- 初回起動時に利用規約を表示し、同意しないと使えない
  （「権利者の許諾がある、または自分が権利を持つコンテンツのみ使用可」）
- core/ と gui/ を分離。core は CLI 単体で使える
- 重い処理はワーカースレッドで実行し、GUI を固まらせない

## 2. 技術スタック

| 項目 | 採用 | 理由 |
|---|---|---|
| 言語 | Python 3.12 | yt-dlp・PySide6 の両方が安定対応 |
| GUI | PySide6 (LGPL) | Qt 公式。OS のダーク/ライト追従が容易 |
| DLエンジン | yt-dlp（Unlicense） + yt-dlp-ejs | YouTube の解析に EJS（JSチャレンジ解決）が必要 |
| JS実行環境 | Deno（MIT, 2.3.0以上） | yt-dlp の推奨ランタイム。`--js-runtimes deno:<path>` 相当で指定 |
| 動画処理 | ffmpeg / ffprobe（配布物は LGPL ビルド） | |
| Twitchチャット | TwitchDownloaderCLI（MIT, lay295） | 下記 §6 |
| 保存場所 | platformdirs | 設定・ログ・キャッシュの場所を OS 標準に統一 |
| テスト | pytest | |
| 配布 | PyInstaller **onedir** | LGPL 部品（Qt, ffmpeg）を差し替え可能に保つ・起動が速い |
| ライセンス一覧 | pip-licenses ＋ 手動追記（exe 類） | THIRD_PARTY_LICENSES を生成 |

## 3. ディレクトリ構成

```
clipdl/
├─ pyproject.toml
├─ PLAN.md / README.md / TERMS.md / THIRD_PARTY_LICENSES
├─ src/clipdl/
│  ├─ core/
│  │  ├─ models.py          Job / ClipRange / Preset / ChatMessage / VideoInfo
│  │  ├─ timeutil.py        hh:mm:ss ⇔ 秒、範囲の検証、ASS時刻
│  │  ├─ probe.py           メタ情報取得・DRM検出
│  │  ├─ downloader.py      yt-dlp ラッパー（download_ranges・進捗・キャンセル）
│  │  ├─ presets.py         編集用1080p MP4 / 最高画質 / 音声のみ
│  │  ├─ naming.py          {date}_{channel}_{title} 展開・禁止文字処理
│  │  ├─ errors.py          例外 → 日本語メッセージ（原文も保持）
│  │  ├─ ffmpeg.py          バイナリ検出・エンコーダ選択・再エンコード
│  │  ├─ cfr.py             VFR判定（ffprobe + vfrdet）・CFR変換
│  │  ├─ cookies.py         ブラウザCookie / cookies.txt
│  │  ├─ queue.py           キューと状態遷移
│  │  ├─ chat/
│  │  │  ├─ base.py         ChatProvider インターフェース
│  │  │  ├─ youtube_live.py live_chat（ライブチャットリプレイ）解析
│  │  │  ├─ youtube_comments.py 通常コメント
│  │  │  ├─ twitch.py       TwitchDownloaderCLI 呼び出し・解析
│  │  │  ├─ cache.py        動画IDごとのチャットキャッシュ
│  │  │  ├─ clipping.py     範囲抽出・クリップ開始を0とした時刻へ変換
│  │  │  └─ export.py       JSON / CSV / ASS 出力
│  │  ├─ updater.py         yt-dlp / yt-dlp-ejs / Deno の更新（検証つき）
│  │  ├─ paths.py / settings.py / logging_setup.py
│  ├─ cli/__main__.py       python -m clipdl ...
│  └─ gui/                  （フェーズ2）
├─ tests/
├─ packaging/               PyInstaller spec・ライセンス収集
└─ vendor/                  ffmpeg / deno / TwitchDownloaderCLI（git 管理外）
```

## 4. 機能ごとの実現方法

### 4.1 基本DL
- URL → `extract_info(download=False)` でタイトル・長さ・サムネ・チャンネル名
- プリセット（初期値は **編集用 1080p MP4**）
  - 編集用 1080p MP4: `bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b` → mp4 にマージ
  - 最高画質: `bv*+ba/b`（mkv も可）
  - 音声のみ: `ba` → m4a
- ファイル名テンプレート `{date}_{channel}_{title}`（範囲ありは `_{clip}` を自動付与）

### 4.2 範囲指定
- `download_ranges`（= `--download-sections`）で必要部分だけ取得。複数範囲を一括
- **高速**: キーフレーム単位でカット（数秒ずれることがある。README に明記）
- **正確**: `force_keyframes_at_cuts` 相当で再エンコード
- 一時停止・再開: yt-dlp に一時停止機能はないため「中断→再実行」で実現。
  通常DLは .part から再開、範囲DLはその範囲をやり直し

### 4.3 エンコーダ選択（正確モード・CFR共通）
実際に短いテストエンコードを行い、成功した最初のものを使う（ビルドに入っていても GPU がないと失敗するため）:
1. h264_nvenc（NVIDIA）
2. h264_qsv（Intel）
3. h264_amf（AMD）
4. h264_mf（Windows Media Foundation。LGPL ビルドでも使える）
5. libopenh264
6. libx264（ユーザーが GPL 版 ffmpeg を指定した場合のみ）

### 4.4 CFR変換（追加要件A）
- モード: **自動（初期値）** / 常に / オフ
- 判定: ffprobe の `r_frame_rate` と `avg_frame_rate` の不一致 ＋ `vfrdet` フィルタの VFR 比率
  （出力例 `VFR:0.000000 (0/59)`。比率が閾値を超えたら VFR）
- 変換: `fps=<目標fps>` で CFR 化。目標は r_frame_rate を 24/25/30/50/60 系に丸めた値
- 判定は「vfrdet の比率 > 1%」**または**「公称と平均の差 > 1%」（途中で 1 回だけ fps が変わる動画は
  vfrdet の比率が小さく出るため。実測で確認）。目標 fps は公称値（r_frame_rate）を優先
- **正確モードで再エンコードする場合は、カットと CFR 化を 1 回の ffmpeg 処理で行い二重エンコードを避ける**
  - 実装（フェーズ1で確定）: yt-dlp の範囲 DL は内部で ffmpeg を起動する。`force_keyframes_at_cuts` で
    `-c copy` を外させ、`external_downloader_args.ffmpeg_o` に「エンコーダ＋`fps` フィルタ」を渡す。
    入力側シークで再エンコードするのでフレーム単位で正確（実測: 6.75 秒指定 → 405 フレーム@60fps）
  - 正確モードでは CFR「自動」も常に CFR 化する（再エンコードのついでなので追加コストなし）
- 高速モード＋CFR が必要な場合は、CFR 変換の 1 回だけ再エンコード
- 中止: yt-dlp が起動した ffmpeg を追跡して終了させる。範囲 DL の途中ファイルは片付ける。
  通常 DL の一時停止は .part を残し、再開時に続きから取る

### 4.5 Cookie（追加要件B）
- `cookiesfrombrowser`（= `--cookies-from-browser`）: firefox / chrome / edge / brave など
- 注意: Windows の Chrome / Edge は暗号化方式の変更（App-Bound Encryption）により
  読めないことがある。UI では Firefox を推奨し、`cookies.txt` ファイル指定も用意
- Cookie の中身はログに出さない

### 4.6 チャット
| 種類 | 取得方法 | 範囲連動 |
|---|---|---|
| YouTube ライブチャットリプレイ | yt-dlp の `live_chat` 字幕 | する |
| YouTube 通常コメント | yt-dlp `getcomments` | しない（動画内時刻を持たないため全件出力） |
| Twitch VOD チャット | TwitchDownloaderCLI `chatdownload` | する |

- 共通形式 `ChatMessage(time_sec, user, text, color, raw)` に揃えてから出力
- **キャッシュ（追加要件D）**: 取得結果を `<cache>/chat/<extractor>_<video_id>.json` に保存し、
  同じ動画の複数範囲・再実行で使い回す
- **進捗表示（追加要件D）**: ライブチャットは取得中のメッセージ数・動画内位置／全体長で進捗を出す
- 範囲抽出: `start <= t < end` を抜き出し、`t - start` に変換
- 出力: JSON（元データ）/ CSV（時刻・ユーザー名・本文、Excel 用に UTF-8 BOM）/ ASS
  - 初期値は **JSON ＋ ASS**
  - ASS フェーズ1: **縦流れ（Twitch 風）**。画面右側に下から積み上げ、一定時間で消える
  - ASS フェーズ2: ニコニコ風（横流れ）を追加
  - オーバーレイ動画（.mov）: ASS を libass で描画した背景透明の動画。初期値は OFF
    - 形式の初期値は QuickTime Animation（qtrle・差分方式）＋チャット欄だけ切り出し＋30fps（実測 1 分 約 64 MB）。
      ProRes 4444 / PNG も選べる。比較表は README
    - 解像度（高さ）・長さはクリップ本体に合わせる。切り出し時は Premiere の「位置」（X/Y）をログと CLI に出す
    - GUI ではチェックの横に予想サイズ（実測値から計算）を 1 行で表示する
  - Twitch は時刻が 1 秒単位のため、ASS・オーバーレイだけ同じ秒のコメントを 1 秒の中で均等にずらす

### 4.7 エラーの日本語化
メンバー限定 / 地域制限 / 非公開・削除 / 年齢制限 / 配信中 / DRM / ログイン必要 / ネットワーク /
ffmpeg 不在 を判別。判別できなければ汎用メッセージ。原文は常に保持（「詳しく」で表示）。

### 4.8 updater（追加要件C）
- 対象: yt-dlp / yt-dlp-ejs / Deno
- yt-dlp: GitHub Releases の zipimport 版 `yt-dlp` を取得し、
  `SHA2-256SUMS` で SHA-256 を照合。`SHA2-256SUMS.sig` の GPG 署名を、
  同梱した公式 `public.key` で検証（検証ライブラリの選定・導入はフェーズ2で承認を得る）
- yt-dlp-ejs: PyPI の配布ファイルを取得し、PyPI が示す sha256 と照合
- Deno: GitHub Releases の zip と `.sha256sum` を照合
- **検証が 1 つでも失敗したら読み込まず、同梱版を使う**
- 更新物はユーザーデータフォルダに置き、起動時に優先して読み込む
  （読み込めなければ同梱版にフォールバック）

### 4.9 設定・ログ
- platformdirs で `設定 / ログ / キャッシュ` を統一
- ログは日ごとにローテーション。「ログを書き出す」で zip 化（ユーザー名などを伏せ字）

## 5. UI 方針（追加要件E）

- **1画面で完結**。基本の流れは「URL を貼る → 範囲 → ダウンロード」の3ステップ
- URL を貼り付けたら自動でメタ情報を取得（「取得」ボタンは置かない）
- メイン画面に出すもの:
  URL / サムネとタイトル / 範囲（時刻入力＋スライダー、＋追加）/ 画質プリセット /
  「チャットも保存」チェック / 保存先 / ダウンロードボタン / キュー
- 折りたたみの「詳細設定」:
  CFR、高速/正確、チャット形式（オーバーレイ動画 .mov を含む）、ファイル名テンプレート、Cookie、言語、ログ書き出し、更新確認
- 詳細設定を一度も開かなくても編集にそのまま使える初期値
  （編集用 1080p MP4、CFR 自動、チャットは JSON＋ASS）
- 一時停止・再開はキューの各行のボタンのみ。メニュー・ツールバーは作らない
- エラーは画面下部に1行。「詳しく」で原文を表示
- OS のダーク/ライトに追従。装飾は最小限
- 日本語 / 英語（i18n）

## 6. Twitch チャット取得の調査結果

- yt-dlp の rechat は Twitch 旧 API 廃止で機能せず、2026-06-09 に削除（yt-dlp PR #16660）
- chat-downloader: 最終リリース 0.2.8（2023-09-03）。更新停止状態
- **TwitchDownloaderCLI を採用**: MIT ライセンス、2026年もリリース継続（1.56.5）
  - `chatdownload --id <VOD> -b <開始> -e <終了> -o out.json`
  - 本体は ffmpeg を同梱しない（必要時にダウンロードする機能があるが、本アプリでは使わない）
  - 同梱時はライセンス表記に MIT 本文と著作権者 lay295 を記載
- ChatProvider で差し替え可能にしておく

## 7. フェーズ

### フェーズ1: CLI版 core
基本DL・範囲指定・正確/高速・CFR・Cookie・チャット（YouTube ライブ/コメント、Twitch）・
キャッシュ・JSON/CSV/ASS（縦流れ）・日本語エラー・設定/ログ・pytest

確認手順:
1. `pytest` が全件成功
2. 自分の動画（または許諾済みの動画）で
   `python -m clipdl <URL> --range 00:01:00-00:02:00 --range 00:05:00-00:05:30 --chat`
3. 出力された mp4 と .ass を VLC / mpv で重ねて、チャットのタイミングを確認
4. `--mode precise` で再エンコード、`--cfr always` で CFR 化されることを ffprobe で確認

### フェーズ2: GUI
§5 の UI・ワーカースレッド・キュー・i18n・利用規約・updater・ニコニコ風 ASS

### フェーズ3: 配布
PyInstaller onedir・LGPL ffmpeg 同梱・README（日本語）・利用規約・THIRD_PARTY_LICENSES
確認: Python 無し環境（Windows Sandbox 等）で起動・DL・更新確認
