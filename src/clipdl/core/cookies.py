"""ブラウザ Cookie / cookies.txt の指定を yt-dlp のパラメータにする。Cookie の中身はログに出さない。"""
from __future__ import annotations

from pathlib import Path

SUPPORTED_BROWSERS = ("firefox", "chrome", "edge", "brave", "chromium", "opera", "vivaldi", "whale", "safari")


class CookieOptionError(ValueError):
    pass


def parse_browser_spec(spec: str) -> tuple[str, str | None, str | None, str | None]:
    """'firefox' / 'chrome:Profile 1' / 'firefox::container' を yt-dlp の cookiesfrombrowser タプルにする。"""
    browser, _, rest = spec.strip().partition(":")
    browser = browser.lower()
    if browser not in SUPPORTED_BROWSERS:
        raise CookieOptionError(f"対応していないブラウザです: {browser}（{', '.join(SUPPORTED_BROWSERS)}）")
    profile, _, container = rest.partition("::")
    return (browser, profile or None, None, container or None)


def cookie_params(browser_spec: str = "", cookie_file: str = "") -> dict:
    params: dict = {}
    if cookie_file:
        p = Path(cookie_file).expanduser()
        if not p.is_file():
            raise CookieOptionError(f"cookies.txt が見つかりません: {p}")
        params["cookiefile"] = str(p)
    elif browser_spec:
        params["cookiesfrombrowser"] = parse_browser_spec(browser_spec)
    return params
