"""GUI の一通りの操作を再現して、各段階の画面を PNG に保存する（動作確認・README 用）。

  1. 起動（初回は利用規約） 2. URL を貼る → 自動で動画情報を取得 3. 範囲を 2 つ追加して時刻を入力
  4. ダウンロードを押す（実行中） 5. 完了

使い方（実際にダウンロードします）:
  .venv\\Scripts\\python.exe docs\\demo\\gui_walkthrough.py <URL> <出力フォルダ> [範囲1] [範囲2]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from clipdl.core.logging_setup import setup_logging
from clipdl.core.queue import JobState
from clipdl.core.settings import Settings
from clipdl.gui import theme
from clipdl.gui.i18n import set_language
from clipdl.gui.main_window import MainWindow
from clipdl.gui.widgets import TermsDialog

DUMMY = sys.argv[1] == "--dummy"
if DUMMY:
    import tempfile
    from demo_dummy import DUMMY_OUTPUT_DIR, DUMMY_URL, install
    _tmp = tempfile.mkdtemp(prefix="clipdl-demo-")
    for _k in ("CONFIG", "DATA", "CACHE", "LOG"):  # 本物の設定・ログに触れない
        os.environ[f"CLIPDL_{_k}_DIR"] = _tmp
    URL, OUT = DUMMY_URL, Path(sys.argv[2])
    RANGES = [r.split("-") for r in (sys.argv[3:5] or ["01:10:00-01:10:20", "01:30:00-01:30:15"])]
else:
    URL = sys.argv[1]
    OUT = Path(sys.argv[2])
    RANGES = [r.split("-") for r in (sys.argv[3:5] or ["01:10:00-01:10:20", "01:30:00-01:30:15"])]
OUT.mkdir(parents=True, exist_ok=True)

setup_logging()
app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)  # 利用規約を閉じた瞬間に終了しないように
settings = Settings.load()
if DUMMY:
    install()
    settings.output_dir = DUMMY_OUTPUT_DIR
set_language(settings.language)
theme.apply(app)
shots: list[tuple[str, Path]] = []


def shot(widget, name: str, caption: str):
    p = OUT / f"{len(shots) + 1:02d}_{name}.png"
    widget.grab().save(str(p))
    shots.append((caption, p))
    print("shot", p.name, flush=True)


def wait_until(cond, timeout: float, then):
    start = time.monotonic()

    def tick():
        if cond():
            then()
        elif time.monotonic() - start > timeout:
            print("timeout", flush=True)
            app.exit(2)
        else:
            QTimer.singleShot(200, tick)
    tick()


# 1. 利用規約
terms = TermsDialog()
terms.show()
QTimer.singleShot(400, lambda: (shot(terms, "terms", "初回起動: 利用規約（同意しないと使えない）"), terms.accept()))
QTimer.singleShot(600, lambda: start_main())

win: MainWindow


def start_main():
    global win
    win = MainWindow(settings)
    win.resize(880, 940)
    win.show()
    QTimer.singleShot(500, step_url)


def step_url():
    shot(win, "empty", "起動直後")
    win.url_edit.setFocus()
    win.url_edit.insert(URL)  # 貼り付けと同じ（クリップボードは使わない）
    wait_until(lambda: win.info is not None, 60, lambda: QTimer.singleShot(1500, step_info))


def step_info():
    shot(win, "info", "1. URL を貼ると自動で動画情報を取得")
    for _ in RANGES:
        QTest.mouseClick(_add_button(), Qt.MouseButton.LeftButton)
    for row, (s, e) in zip(win.rows, RANGES):
        for edit, val in ((row.start_edit, s), (row.end_edit, e)):
            edit.setFocus()
            edit.selectAll()
            QTest.keyClicks(edit, val)
            QTest.keyClick(edit, Qt.Key.Key_Return)
    win.adv_toggle.setChecked(True)
    win.fmt["mov"].setChecked(True)  # オーバーレイ動画も書き出す（予想サイズの表示を確認）
    QTimer.singleShot(600, step_ranges)


def _add_button():
    from PySide6.QtWidgets import QPushButton
    return next(b for b in win.findChildren(QPushButton) if b.text().startswith("＋") or b.text().startswith("+"))


def step_ranges():
    shot(win, "ranges", "2. 範囲を 2 つ（時刻を入力。バーのつまみとも連動）・詳細設定でオーバーレイ動画を選択")
    QTest.mouseClick(win.dl_btn, Qt.MouseButton.LeftButton)
    wait_until(lambda: any(r.job.state == JobState.RUNNING and r.job.progress
                           and r.job.progress.stage in ("video", "encode") and r.job.progress.ratio
                           and r.job.progress.ratio > 0.3 for r in win.queue_rows.values()),
               300, lambda: QTimer.singleShot(400, lambda: (  # 描画が追いつくのを待ってから撮る
                   shot(win, "running", "3. ダウンロード中（キューの行に進捗と一時停止・キャンセル）"), wait_done())))


def wait_done():
    wait_until(lambda: all(r.job.state in (JobState.DONE, JobState.FAILED) for r in win.queue_rows.values()),
               900, finish)


def finish():
    QTimer.singleShot(500, lambda: (shot(win, "done", "4. 完了（フォルダを開くボタン）"), _write_index(), app.exit(0)))


def _write_index():
    lines = [f"{p.name}\t{c}" for c, p in shots]
    (OUT / "index.txt").write_text("\n".join(lines) + "\n", "utf-8")
    job = next(iter(win.queue_rows.values())).job
    if job.result:
        for f in job.result.files:
            print("file", f, flush=True)
        for n in job.result.notes:
            print("note", n, flush=True)
    if job.error:
        print("error", job.error.raw, flush=True)


code = app.exec()
win.bridge.shutdown()
sys.exit(code)
