"""重い処理を別スレッドで動かし、結果を GUI スレッドへ Qt のシグナルで返す（GUI を固まらせない）。"""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..core.errors import classify
from ..core.queue import Job, JobQueue

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)   # UserError


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self.fn()
        except Exception as e:  # noqa: BLE001 — 例外はすべて日本語化して GUI に返す
            self.signals.failed.emit(classify(e))
            return
        self.signals.done.emit(result)


class ProgressRelay(QObject):
    """別スレッドの進捗を GUI スレッドへ渡す（0.2 秒に 1 回まで。段階が変わったときはすぐ渡す）。"""
    progress = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = (None, 0.0)

    def emit_throttled(self, stage: str, ratio) -> None:
        import time
        now = time.monotonic()
        last_stage, last_t = self._last
        if stage != last_stage or now - last_t >= 0.2 or ratio == 1.0:
            self._last = (stage, now)
            self.progress.emit(stage, ratio)


_pool_tasks: set = set()  # シグナルの受け手が先に消えないよう、終わるまで参照を持つ


def run_in_thread(fn: Callable[[], Any], on_done: Callable[[Any], None],
                  on_failed: Callable[[Any], None] | None = None) -> None:
    """fn を別スレッドで実行し、終わったら GUI スレッドで on_done / on_failed を呼ぶ。"""
    task = _Task(fn)
    sig = task.signals
    _pool_tasks.add(sig)
    sig.done.connect(on_done)
    if on_failed:
        sig.failed.connect(on_failed)
    sig.done.connect(lambda *_: _pool_tasks.discard(sig))
    sig.failed.connect(lambda *_: _pool_tasks.discard(sig))
    QThreadPool.globalInstance().start(task)


class QueueBridge(QObject):
    """core の JobQueue（専用スレッドで 1 件ずつ実行）と GUI をつなぐ。"""

    jobChanged = Signal(object)  # Job

    def __init__(self, parent=None):
        super().__init__(parent)
        # listener はキューのスレッドから呼ばれる。シグナルは自動的に GUI スレッドへ渡る
        self._alive = True
        self.queue = JobQueue(listener=self._notify)
        self.queue.start()

    def _notify(self, job: Job) -> None:
        if not self._alive:  # 画面を閉じたあとに届いた通知は捨てる
            return
        try:
            self.jobChanged.emit(job)
        except RuntimeError:
            pass

    def add(self, spec) -> Job:
        return self.queue.add(spec)

    def pause(self, job: Job) -> None:
        self.queue.pause(job)

    def resume(self, job: Job) -> None:
        self.queue.resume(job)

    def cancel(self, job: Job) -> None:
        self.queue.cancel(job)

    def shutdown(self) -> None:
        self._alive = False
        for job in list(self.queue.jobs):
            if job._token is not None:  # 実行中のものを止める
                job._token.cancel("cancel")
        self.queue.close()
