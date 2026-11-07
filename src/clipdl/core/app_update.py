"""clipdl 本体の新しい版があるかを、GitHub のリリース一覧で確かめる（知らせるだけ。ダウンロードや入れ替えはしない）。

- プレリリースも対象。下書き（draft）と、版番号として読めないタグは無視する
- 「いちばん新しい版」は公開日ではなく版番号で選ぶ（0.1.0b1 < 0.1.0b2 < 0.1.0rc1 < 0.1.0 < 0.1.1）
- 起動時の確認は 1 日 1 回まで。通信に失敗しても例外を外に出さない（起動を妨げない）
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .. import __version__
from .paths import env

log = logging.getLogger(__name__)

REPO = "soma1023/clipdl"
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = 24 * 3600   # 起動時の確認は 1 日 1 回まで
TIMEOUT = 5.0

TEST_APP_VERSION = "CLIPDL_TEST_APP_VERSION"   # テスト用: 今の版をこの値とみなす（更新の流れを試す）


def current_version() -> str:
    return env(TEST_APP_VERSION) or __version__


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]?(a|alpha|b|beta|rc)[-.]?(\d*))?$", re.IGNORECASE)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}


def version_key(text: str) -> tuple[int, int, int, int, int] | None:
    """版番号を比べられる形にする。読めなければ None。正式版はベータ・RC より新しい扱い。"""
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    major, minor, patch, pre, pre_n = m.groups()
    rank = _PRE_RANK[pre.lower()] if pre else 3
    return int(major), int(minor), int(patch or 0), rank, int(pre_n or 0)


def is_newer(candidate: str, current: str) -> bool:
    a, b = version_key(candidate), version_key(current)
    return a is not None and b is not None and a > b


@dataclass
class Release:
    tag: str      # "v0.1.0b2" など
    url: str      # リリースページ


def pick_latest(releases: list[dict]) -> Release | None:
    """API の結果から、下書き以外でいちばん新しい版を選ぶ。"""
    best: tuple | None = None
    found: Release | None = None
    for r in releases:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        tag = str(r.get("tag_name") or "")
        key = version_key(tag)
        if key is None:
            continue
        if best is None or key > best:
            best = key
            url = str(r.get("html_url") or "")
            found = Release(tag, url if url.startswith(RELEASES_PAGE + "/") else RELEASES_PAGE)
    return found


def fetch_latest(timeout: float = TIMEOUT) -> Release | None:
    req = urllib.request.Request(API_URL, headers={"User-Agent": "clipdl-update-check",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if not isinstance(data, list):
        raise ValueError("リリース一覧の形式が想定と違います")
    return pick_latest(data)


@dataclass
class AppUpdateStatus:
    current: str
    latest: Release | None = None
    error: str | None = None

    @property
    def newer(self) -> Release | None:
        """今の版より新しいリリース（無ければ None）。"""
        if self.latest and is_newer(self.latest.tag, self.current):
            return self.latest
        return None

    def summary(self) -> str:
        if self.error:
            return f"clipdl: {self.current}（新しい版を確認できませんでした）"
        if self.newer:
            return f"clipdl: {self.current} → 新しい版 {self.newer.tag} があります（{self.newer.url}）"
        return f"clipdl: {self.current}（最新）"


def check_now(fetch: Callable[[], Release | None] = fetch_latest, current: str | None = None) -> AppUpdateStatus:
    """その場で確かめる（CLI の --check-update 用）。例外は出さない。"""
    current = current or current_version()
    try:
        return AppUpdateStatus(current, fetch())
    except Exception as e:  # noqa: BLE001  通信・JSON などどんな失敗でも起動や他の確認を止めない
        log.info("clipdl の新しい版を確認できませんでした: %s", e)
        return AppUpdateStatus(current, None, str(e))


def check_on_startup(settings, fetch: Callable[[], Release | None] = fetch_latest,
                     now: float | None = None, current: str | None = None) -> Release | None:
    """起動時の確認。設定で OFF なら何もしない。前回から 24 時間たっていなければ前回の結果を使う。

    settings の記録欄を書き換える（保存は呼び出し側で行う）。新しい版があればその Release を返す。
    """
    if not getattr(settings, "check_app_update", True):
        return None
    current = current or current_version()
    now = time.time() if now is None else now
    last = float(getattr(settings, "app_update_checked_at", 0) or 0)
    if last <= 0 or not (0 <= now - last < CHECK_INTERVAL):   # 未確認・24 時間経過・時計が戻ったときは確認する
        st = check_now(fetch, current)
        settings.app_update_checked_at = now
        if st.error is None:   # 失敗したときは前回分の記録を残す（その日はもう確認しない）
            settings.app_update_latest = st.latest.tag if st.latest else ""
            settings.app_update_url = st.latest.url if st.latest else ""
    tag = getattr(settings, "app_update_latest", "")
    if tag and is_newer(tag, current):
        return Release(tag, getattr(settings, "app_update_url", "") or RELEASES_PAGE)
    return None
