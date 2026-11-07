"""例外を「人が読めるメッセージ」に変換する。原文は常に保持する（GUI の「詳しく」用）。"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (コード, 判定用の正規表現, 日本語, 英語) — 上から順に判定する
_RULES: list[tuple[str, str, str, str]] = [
    ("drm", r"DRM protected|has_drm",
     "DRMで保護された動画のため対応していません。",
     "This video is DRM-protected and is not supported."),
    ("members_only", r"members[- ]only|Join this channel to get access|available to this channel's members",
     "メンバー限定動画です。詳細設定の「Cookie」でログイン済みのブラウザを指定してください。",
     "This is a members-only video. Set a logged-in browser under Advanced > Cookies."),
    ("subscribers_only", r"subscriber[s]?[- ]only|only available to subscribers",
     "サブスク限定の動画です。",
     "This video is for subscribers only."),
    ("geo", r"not (?:made this video )?available in your (?:country|location)|geo[- ]?restrict|GeoRestricted",
     "地域制限があり、この国からは視聴できません。",
     "This video is geo-restricted in your region."),
    ("bot_check", r"confirm you(?:’|')?re not a bot|not a bot",
     "YouTube にボット確認を求められました。詳細設定の「Cookie」でログイン済みのブラウザを指定してください。",
     "YouTube requires a bot check. Set a logged-in browser under Advanced > Cookies."),
    ("age", r"age[- ]restrict|confirm your age|inappropriate for some users",
     "年齢制限のある動画です。Cookie でログイン済みのブラウザを指定してください。",
     "This video is age-restricted. Set a logged-in browser under Advanced > Cookies."),
    ("private", r"Private video|This video is private",
     "非公開の動画です。",
     "This video is private."),
    ("upcoming", r"Premieres in|This live event will begin|is_upcoming|will begin in",
     "まだ配信・公開が始まっていません。",
     "This stream or premiere has not started yet."),
    ("live", r"is currently live|live stream is still in progress|This live stream recording is not available",
     "配信中、またはアーカイブの処理中です。配信終了後にもう一度お試しください。",
     "The stream is live or still processing. Try again after it ends."),
    ("removed", r"Video unavailable|has been removed|account .*terminated|does not exist|HTTP Error 404",
     "動画が見つかりません（削除・非公開・URL の誤りの可能性があります）。",
     "Video not found (deleted, private, or wrong URL)."),
    ("unsupported", r"Unsupported URL|is not a valid URL",
     "対応していない URL です。YouTube または Twitch の動画 URL を入力してください。",
     "Unsupported URL. Enter a YouTube or Twitch video URL."),
    ("cookies", r"could not (?:find|copy|decrypt).*cookie|Failed to decrypt|cookies? database",
     "ブラウザの Cookie を読み込めませんでした。ブラウザを閉じてから再試行するか、Firefox か cookies.txt をお試しください。",
     "Could not read browser cookies. Close the browser and retry, or use Firefox or a cookies.txt file."),
    ("forbidden", r"HTTP Error 403|Forbidden",
     "アクセスを拒否されました（HTTP 403）。時間をおくか、yt-dlp を更新してください。",
     "Access was denied (HTTP 403). Wait a while or update yt-dlp."),
    ("rate_limit", r"HTTP Error 429|Too Many Requests|rate.?limit",
     "アクセスが多すぎるため一時的に制限されています。しばらく待ってから再試行してください。",
     "Too many requests. Please wait and try again."),
    ("network", r"timed out|getaddrinfo|Connection (?:reset|refused|aborted)|Unable to connect|urlopen error|"
                r"Name or service not known|SSL",
     "ネットワークに接続できませんでした。接続を確認して再試行してください。",
     "Network error. Check your connection and try again."),
    ("ffmpeg_missing", r"ffmpeg (?:could )?not (?:be )?found|ffmpeg is not installed|ffprobe.*not found",
     "ffmpeg が見つかりません。アプリを再インストールしてください。",
     "ffmpeg was not found. Please reinstall the app."),
    ("js_runtime", r"JavaScript runtime|js[_ -]runtime|deno",
     "YouTube の解析に必要な部品（Deno）が見つからないか動作していません。",
     "The JavaScript runtime (Deno) required for YouTube is missing or not working."),
    ("disk", r"No space left|Errno 28|disk full",
     "保存先の空き容量が足りません。",
     "Not enough free space at the destination."),
    ("permission", r"Permission denied|Errno 13|Access is denied",
     "保存先に書き込めません。保存先を変えるか、ファイルを開いているアプリを閉じてください。",
     "Cannot write to the destination. Choose another folder or close apps using the file."),
]

_GENERIC = ("unknown", "処理に失敗しました。「詳しく」で原因を確認できます。",
            "The operation failed. See details for more information.")


@dataclass
class UserError(Exception):
    code: str
    message_ja: str
    message_en: str
    raw: str = ""

    def __str__(self) -> str:  # CLI 表示用
        return self.message_ja

    def message(self, lang: str = "ja") -> str:
        return self.message_en if lang == "en" else self.message_ja


def _clean(raw: str) -> str:
    # 色付けのエスケープシーケンスを除く
    return re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()


def classify(exc_or_text: BaseException | str) -> UserError:
    if isinstance(exc_or_text, UserError):
        return exc_or_text
    raw = _clean(exc_or_text if isinstance(exc_or_text, str) else f"{type(exc_or_text).__name__}: {exc_or_text}")
    for code, pattern, ja, en in _RULES:
        if re.search(pattern, raw, re.IGNORECASE):
            return UserError(code, ja, en, raw)
    code, ja, en = _GENERIC
    return UserError(code, ja, en, raw)


def drm_error(raw: str = "") -> UserError:
    _, _, ja, en = _RULES[0]
    return UserError("drm", ja, en, raw or "DRM protected")


class Cancelled(Exception):
    """ユーザーが中止した。"""
