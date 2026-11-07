"""画面の文言（日本語 / 英語）。言語の切り替えは再起動後に反映する。"""
from __future__ import annotations

_LANG = "ja"

STRINGS: dict[str, tuple[str, str]] = {
    # 見出し・ラベル
    "app.title": ("clipdl {version}（ベータ版）", "clipdl {version} (beta)"),
    "step.url": ("URL を貼る（貼り付けると自動で取得）", "Paste a URL (fetched automatically)"),
    "step.range": ("範囲（空なら動画全体）　つまみをドラッグ、または時刻を入力",
                   "Ranges (empty = whole video) — drag the handles or type times"),
    "step.download": ("ダウンロード", "Download"),
    "url.placeholder": ("https://www.youtube.com/watch?v=… または https://www.twitch.tv/videos/…",
                        "https://www.youtube.com/watch?v=… or https://www.twitch.tv/videos/…"),
    "info.fetching": ("動画情報を取得中…", "Fetching video info…"),
    "info.chat_yes": ("チャットあり", "chat available"),
    "info.chat_no": ("チャットなし", "no chat"),
    "range.add": ("＋ 範囲を追加", "+ Add range"),
    "range.remove": ("範囲を削除", "Remove range"),
    "range.single_only": ("範囲指定は URL が 1 本のときだけ使えます", "Ranges are available for a single URL only"),
    "quality": ("画質", "Quality"),
    "save_chat": ("チャットも保存", "Save chat too"),
    "output_dir": ("保存先", "Save to"),
    "browse": ("変更", "Browse"),
    "advanced": ("詳細設定", "Advanced"),
    "queue": ("キュー", "Queue"),
    # 詳細設定
    "adv.cut": ("カット", "Cut"),
    "adv.cut.fast": ("高速（キーフレーム単位）", "Fast (keyframe)"),
    "adv.cut.precise": ("正確（再エンコード）", "Precise (re-encode)"),
    "adv.cfr": ("CFR 変換", "CFR conversion"),
    "adv.cfr.auto": ("自動", "Auto"),
    "adv.cfr.always": ("常に", "Always"),
    "adv.cfr.off": ("オフ", "Off"),
    "adv.chat_formats": ("チャット形式", "Chat formats"),
    "adv.overlay": ("オーバーレイ動画（.mov）", "Overlay video (.mov)"),
    "adv.overlay.estimate": ("予想サイズ {size}（{detail}）", "Estimated {size} ({detail})"),
    "adv.chat_style": ("スタイル", "Style"),
    "style.twitch": ("Twitch配信風", "Twitch stream style"),
    "style.vertical": ("シンプル（縦流れ）", "Simple (vertical)"),
    "style.scroll": ("横流れ（ニコニコ風）", "Scrolling (Niconico-like)"),
    "adv.template": ("ファイル名", "File name"),
    "adv.cookies": ("Cookie（メンバー限定動画）", "Cookies (members-only videos)"),
    "adv.cookies.none": ("使わない", "Don't use"),
    "adv.cookies.file": ("cookies.txt を選ぶ…", "Choose cookies.txt…"),
    "adv.language": ("言語 ／ サポート", "Language / Support"),
    "adv.language.restart": ("言語は再起動後に切り替わります", "Language changes after restart"),
    "adv.export_logs": ("ログを書き出す", "Export logs"),
    "adv.check_update": ("更新を確認", "Check for updates"),
    "adv.update.checking": ("更新を確認中…", "Checking for updates…"),
    "adv.update.apply": ("更新する", "Update"),
    "adv.update.applying": ("更新ファイルを取得して署名を検証中…", "Downloading and verifying the update…"),
    "adv.update.done": ("yt-dlp {version} を検証して保存しました。次回の起動から使います", "yt-dlp {version} was verified and saved. It will be used from the next launch"),
    "adv.app_update": ("新しい版を確認する", "Check for new versions"),
    "app_update.available": ("新しい版 {version} があります", "A new version {version} is available"),
    "app_update.open": ("ダウンロードページを開く", "Open download page"),
    "selfupdate.update": ("更新する", "Update"),
    "selfupdate.verifying": ("更新ファイルの署名を確認中…", "Verifying the update signature…"),
    "selfupdate.downloading": ("新しい版をダウンロード中…", "Downloading the new version…"),
    "selfupdate.extracting": ("新しい版を展開中…", "Extracting the new version…"),
    "selfupdate.ready": ("clipdl {version} の準備ができました。再起動して更新しますか？",
                         "clipdl {version} is ready. Restart to update?"),
    "selfupdate.restart": ("再起動して更新", "Restart and update"),
    "selfupdate.later": ("あとで", "Later"),
    "selfupdate.busy": ("ダウンロードが終わってから更新できます", "You can update after the downloads finish"),
    "selfupdate.failed": ("自動更新できませんでした（{reason}）。ダウンロードページから入手してください",
                          "Could not update automatically ({reason}). Please get it from the download page"),
    "selfupdate.rolled_back": ("更新に失敗したため、元の版に戻しました", "The update failed, so the previous version was restored"),
    "adv.update.rejected": ("更新版の {name} を使わず、同梱版で動いています", "Using the bundled {name} instead of the update"),
    # キュー
    "q.waiting": ("待機中", "Waiting"),
    "q.info": ("動画情報を取得中", "Fetching info"),
    "q.video": ("ダウンロード中", "Downloading"),
    "q.encode": ("エンコード中", "Encoding"),
    "q.chat": ("チャットを取得中", "Fetching chat"),
    "q.overlay": ("オーバーレイ動画を書き出し中", "Rendering overlay"),
    "q.comments": ("コメントを取得中", "Fetching comments"),
    "q.paused": ("一時停止中", "Paused"),
    "q.done": ("完了 ・ {n} ファイル", "Done · {n} files"),
    "q.failed": ("失敗", "Failed"),
    "q.cancelled": ("キャンセルしました", "Cancelled"),
    "q.clip": ("クリップ {i}/{n}", "Clip {i}/{n}"),
    "q.pause": ("一時停止", "Pause"),
    "q.resume": ("再開", "Resume"),
    "q.cancel": ("キャンセル", "Cancel"),
    "q.open": ("フォルダを開く", "Open folder"),
    "q.retry": ("やり直す", "Retry"),
    "q.empty": ("ダウンロードしたものがここに並びます", "Downloads will appear here"),
    # 複数 URL
    "multi.confirm": ("{n} 件をキューに追加（動画全体・現在の設定）", "Add {n} items to the queue (whole video, current settings)"),
    "multi.add": ("追加", "Add"),
    "multi.cancel": ("やめる", "Cancel"),
    # エラーバー
    "err.details": ("詳しく", "Details"),
    "err.hide": ("閉じる", "Hide"),
    "err.no_url": ("URL を入力してください。", "Enter a URL."),
    "err.time": ("時刻の形式が正しくありません（例: 01:23:45）。", "Invalid time (e.g. 01:23:45)."),
    # 利用規約
    "terms.title": ("利用規約", "Terms of use"),
    "terms.body": (
        "本ソフトウェアは、権利者の許諾がある、または利用者自身が権利を持つコンテンツのダウンロードにのみ使用できます。\n\n"
        "DRM で保護されたコンテンツには対応していません。各サイトの利用規約と著作権法を守ってご利用ください。\n\n"
        "規約の全文は、アプリと同じフォルダの TERMS.md にあります。",
        "Use this software only for content you own or have permission from the rights holder to download.\n\n"
        "DRM-protected content is not supported. Follow each site's terms of service and copyright law.\n\n"
        "The full terms are in TERMS.md in the app folder."),
    "terms.accept": ("同意して使う", "Accept and continue"),
    "terms.decline": ("同意しない（終了）", "Decline (quit)"),
    # その他
    "logs.saved": ("ログを書き出しました: {path}", "Logs exported: {path}"),
    "added": ("キューに追加しました", "Added to the queue"),
}


def set_language(lang: str) -> None:
    global _LANG
    _LANG = "en" if lang == "en" else "ja"


def language() -> str:
    return _LANG


def tr(key: str, **kw) -> str:
    ja, en = STRINGS.get(key, (key, key))
    text = en if _LANG == "en" else ja
    return text.format(**kw) if kw else text
