"""README 用のデモ画像を、実在の配信を使わずに撮るためのダミー（gui_walkthrough.py --dummy から使う）。

- 動画情報・サムネイル・ダウンロードはすべて作り物で、通信もファイルの書き込みもしない
- タイトル・チャンネル名・URL は架空のもの
"""
from __future__ import annotations

import functools
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

from clipdl.core.models import Progress, VideoInfo
from clipdl.core.pipeline import JobResult
from clipdl.core.queue import JobQueue
from clipdl.gui import main_window, workers

DUMMY_URL = "https://www.twitch.tv/videos/0000000000"
DUMMY_OUTPUT_DIR = r"C:\clipdl-demo"
DUMMY_INFO = VideoInfo(
    url=DUMMY_URL, id="0000000000", extractor="twitch:vod", title="サンプル配信アーカイブ（デモ用）",
    channel="sample_channel", duration=3 * 3600 + 12 * 60 + 34, thumbnail="dummy://thumb", upload_date="20261101",
    live_status="was_live", has_live_chat=True, fps=60.0, width=1920, height=1080,
)


def _thumbnail_png() -> bytes:
    img = QImage(480, 270, QImage.Format.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(QPointF(0, 0), QPointF(480, 270))
    g.setColorAt(0, QColor("#3b5bdb"))
    g.setColorAt(1, QColor("#0ca678"))
    p.fillRect(img.rect(), g)
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI", 44, QFont.Weight.Bold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "SAMPLE")
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(QByteArray(buf.data()))


def _fake_run(spec, on_progress=None, cancel=None, info=None) -> JobResult:
    """ダウンロードの進み方だけを再現する（ファイルは作らない）。"""
    emit = on_progress or (lambda p: None)
    emit(Progress("info", 1.0, DUMMY_INFO.title))
    clips = spec.ranges or [None]
    for i, _ in enumerate(clips):
        for step in range(1, 21):
            if cancel is not None and cancel.event.is_set():
                break
            emit(Progress("video", step / 20, "ダウンロード中", i))
            time.sleep(0.15)
    emit(Progress("chat", 1.0, "チャットを取得中"))
    emit(Progress("done", 1.0, "完了"))
    out = Path(DUMMY_OUTPUT_DIR)
    names = [f"20261101_sample_channel_サンプル配信アーカイブ_{i + 1}.mp4" for i in range(len(clips))]
    return JobResult(info=DUMMY_INFO, files=[out / n for n in names] + [out / "chat.json", out / "chat.ass", out / "chat.mov"])


def install() -> None:
    """GUI が使う通信部分を差し替える。QApplication を作ったあとに呼ぶ。"""
    png = _thumbnail_png()

    def fake_extract(url, cookies=None):
        time.sleep(0.3)
        return DUMMY_INFO

    main_window.extract_info = fake_extract
    main_window._download = lambda url: png
    main_window._thumbnail_url = lambda info: "dummy://thumb"
    workers.JobQueue = functools.partial(JobQueue, runner=_fake_run)
