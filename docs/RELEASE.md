# 配布の手順（リリース担当向け）

## 1. 準備

```bash
cd clipdl
.venv\Scripts\python.exe -m pytest -q
```

- `vendor/` の ffmpeg・Deno・TwitchDownloaderCLI を更新した場合:
  - `vendor/CHECKSUMS.txt` を更新する
  - ffmpeg を替えたら `.venv\Scripts\python.exe packaging\record_ffmpeg_build.py` を実行する
    （GPL 部品が入っていないかの確認と、ソースの入手先の記録）
- `src/clipdl/__init__.py` の `__version__` を上げる

## 2. ライセンス一覧とビルド

```bash
.venv\Scripts\python.exe packaging\collect_licenses.py
.venv\Scripts\python.exe packaging\build.py
```

出力:

- `build/dist/clipdl/` … 配布フォルダ（`clipdl.exe` = GUI、`clipdl-cli.exe` = コマンドライン、
  `clipdl-updater.exe` = ワンクリック更新の入れ替え用、`VERSION` = 版番号）
- `build/release/clipdl-<版>-win64.zip` … 配布する zip
- `build/release/clipdl-<版>-win64.zip.sha256` … zip の SHA-256（リリースページに載せる）

### 署名（ワンクリック更新用）

```bash
.venv\Scripts\python.exe packaging\sign_release.py
```

秘密鍵のパスフレーズを尋ねられます。次の 2 つが `build/release/` にできます（どちらもリリースに添付する）。

- `clipdl-<版>-win64.update.json` … 更新情報（版・zip の名前・サイズ・SHA-256・鍵 ID）
- `clipdl-<版>-win64.update.json.sig` … その Ed25519 署名

署名のあと、アプリに埋め込んだ公開鍵で検証し直します（通らなければ失敗）。
**この 2 つを添付し忘れると、その版へはワンクリック更新できません**（ページを開く案内になります）。

### 署名用の鍵

- 秘密鍵: `%USERPROFILE%\clipdl-keys\clipdl-release-ed25519.pem`（パスフレーズで暗号化。OneDrive・リポジトリの外）
- 公開鍵: `src/clipdl/core/keys/clipdl_release_<鍵ID>.pub`（アプリに埋め込む）
- 最初の 1 回だけ `.venv\Scripts\python.exe packaging\keygen.py` で作る
- バックアップ: 暗号化された `.pem` を USB メモリなどオフラインの場所に 2 つ。パスフレーズはパスワードマネージャーへ
  （ファイルと同じ場所には置かない）
- 鍵を替えるとき: 新しい公開鍵を**古い鍵と並べて**埋め込んだ版を、古い鍵で署名して出す。
  利用者がその版に更新したあとで、新しい鍵での署名に切り替える
- 鍵をなくしたとき: 以降の版には署名できない。新しい鍵を埋め込んだ版を出し、利用者には一度だけ手動で入れ替えてもらう

## 3. 配布前の確認

1. zip を新しいフォルダに展開し、`clipdl.exe` が起動することを確認する
2. `clipdl-cli.exe --check-update` で「yt-dlp の読み込み元」が `_internal\pylib\yt_dlp`（同梱版）になっていること
3. 範囲を 1 つ指定したダウンロードが通ること（ffmpeg を PATH に入れていない PC で確認するのが望ましい）
4. 可能なら、Python を入れていない別の PC か仮想マシン（Windows Sandbox は Pro 以上）で起動する

## 4. VirusTotal での確認（アップロードは手動）

コード署名をしていない exe は、ウイルス対策ソフトに誤検知されることがあります。
配布前に VirusTotal（https://www.virustotal.com/）で確認し、結果のリンクをリリースページに載せます。

1. **まずハッシュで検索する**（アップロードせずに済む場合がある）
   - PowerShell で SHA-256 を出す:
     ```powershell
     Get-FileHash .\build\dist\clipdl\clipdl.exe, .\build\dist\clipdl\clipdl-cli.exe -Algorithm SHA256
     ```
   - VirusTotal の「SEARCH」タブにハッシュを貼る。見つからなければ次へ
2. **アップロードするファイル**（VirusTotal の上限は 650 MB。zip 全体は大きいので分けて確認する）
   - `clipdl.exe`、`clipdl-cli.exe`（アプリ本体の起動部分）
   - `clipdl-<版>-win64.zip`（中身もまとめて検査される。サイズの上限内であれば）
   - 同梱の `vendor\deno\deno.exe` などは公式配布物なので、必要に応じてハッシュ検索だけでよい
3. 「FILE」タブでファイルを選び、アップロードする
   - **注意: アップロードしたファイルは VirusTotal の契約者（セキュリティ企業など）に共有されます。**
     公開前提の配布物だけをアップロードし、個人情報や未公開のファイルは含めないこと
4. 結果を確認する
   - 数社だけが「PyInstaller」「Generic」「ML」などの名前で検出する場合は、PyInstaller 製 exe によくある誤検知のことが多い
   - 大手（Microsoft、Kaspersky、ESET、Bitdefender など）が検出した場合は配布を止め、原因を調べる
   - 誤検知は各社の誤検知報告フォームから申請できる（Microsoft: https://www.microsoft.com/wdsi/filesubmission ）
5. 結果ページの URL（`https://www.virustotal.com/gui/file/<SHA-256>`）をリリースページに載せる

## 5. リリースページに載せるもの

- `clipdl-<版>-win64.zip` と `clipdl-<版>-win64.zip.sha256`
- `clipdl-<版>-win64.update.json` と `clipdl-<版>-win64.update.json.sig`（ワンクリック更新用）

```bash
gh release create v<版> build\release\clipdl-<版>-win64.zip build\release\clipdl-<版>-win64.zip.sha256 ^
  build\release\clipdl-<版>-win64.update.json build\release\clipdl-<版>-win64.update.json.sig ^
  --target main --prerelease --title "clipdl <版>（ベータ版）" --notes-file <リリースノート>
```
- SHA-256 の値（本文にも書く）と、確かめ方（`Get-FileHash ... -Algorithm SHA256`）
- VirusTotal の結果ページの URL
- 変更点、既知の問題、SmartScreen の警告が出たときの対処（README の「インストール」へのリンク）
- 同梱している yt-dlp / ffmpeg / Deno / TwitchDownloaderCLI の版
