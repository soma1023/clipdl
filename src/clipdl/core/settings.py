"""設定の読み書き（config_dir()/settings.json）。

詳細設定を開かなくても編集にそのまま使える初期値にしている。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import config_dir

log = logging.getLogger(__name__)

DEFAULT_TEMPLATE = "{date}_{channel}_{title}"
# 利用規約の版（TERMS.md の「規約の版」と合わせる。上げると次回の起動時にあらためて同意を求める）
TERMS_VERSION = "2"


@dataclass
class Settings:
    preset: str = "edit1080"
    cut_mode: str = "fast"
    cfr_mode: str = "auto"
    save_chat: bool = True
    chat_formats: list[str] = field(default_factory=lambda: ["json", "ass"])
    save_comments: bool = False
    output_dir: str = str(Path.home() / "Videos" / "clipdl")
    filename_template: str = DEFAULT_TEMPLATE
    cookies_from_browser: str = ""   # "firefox" / "chrome:Profile 1" など。空なら使わない
    cookies_file: str = ""
    language: str = "ja"
    ass_style: str = "vertical"      # "vertical"（縦流れ・Twitch 風）/ "scroll"（横流れ・ニコニコ風）
    overlay_codec: str = "qtrle"     # オーバーレイ動画の形式（qtrle / png / prores）
    overlay_crop: bool = True        # チャット欄の幅だけ切り出す（縦流れのときだけ有効）
    overlay_fps: str = "30"          # "30" / "60" / ""（クリップ本体に合わせる）
    terms_accepted_version: str = ""
    check_app_update: bool = True    # 起動時に clipdl の新しい版を確認する（1 日 1 回まで）
    app_update_checked_at: float = 0.0   # 前回確認した時刻（UNIX 時刻）
    app_update_latest: str = ""      # 前回確認したときの最新の版（タグ名）
    app_update_url: str = ""         # そのリリースページ
    window_geometry: str = ""        # GUI のウィンドウ位置（Qt の saveGeometry を base64 で保存）

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError) as e:
            log.warning("設定ファイルを読めないため初期値を使います: %s", e)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        p = self.path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(p)
