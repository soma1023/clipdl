"""チャット取得の共通インターフェース。サイトごとの実装はこれを継承する（差し替え可能にするため）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from ..models import ChatMessage, Progress, VideoInfo

ChatProgress = Callable[[Progress], None]


class ChatUnavailable(Exception):
    """この動画にはチャットが無い（エラーではなく「無し」として扱う）。"""


class ChatProvider(ABC):
    #: キャッシュのキーに使う名前（取得方法が変わったらキャッシュを分けるため）
    name: str = "base"

    @abstractmethod
    def supports(self, info: VideoInfo) -> bool: ...

    #: 元データファイルの拡張子
    raw_suffix: str = ".json"

    @abstractmethod
    def fetch(self, info: VideoInfo, span: tuple[float, float] | None, dest: Path,
              on_progress: ChatProgress | None, cancel) -> tuple[float, float] | None:
        """チャットの元データを dest に保存する。

        span: 必要な区間（動画先頭からの秒）。None なら全体。
        戻り値: 実際に取得した区間（None なら全体）。キャッシュの再利用判定に使う。
        """

    @abstractmethod
    def parse(self, path: Path) -> list[ChatMessage]:
        """元データを読み、ChatMessage（time は動画先頭からの秒、時刻順）にする。"""
