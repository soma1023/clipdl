"""複数ジョブのキュー。1 件ずつ順番に実行し、一時停止・再開・キャンセルを扱う。

yt-dlp には一時停止の機能がないため「一時停止 = 中断して待機に戻す」で実現する。
再開すると、通常 DL は .part から続きを取り、範囲 DL はその範囲をやり直す。
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .downloader import CancelToken
from .errors import Cancelled, UserError, classify
from .models import Progress
from .pipeline import JobResult, JobSpec, run_job

log = logging.getLogger(__name__)


class JobState(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 許される状態遷移
_TRANSITIONS = {
    JobState.WAITING: {JobState.RUNNING, JobState.PAUSED, JobState.CANCELLED},
    JobState.RUNNING: {JobState.DONE, JobState.FAILED, JobState.PAUSED, JobState.CANCELLED},
    JobState.PAUSED: {JobState.WAITING, JobState.CANCELLED},
    JobState.FAILED: {JobState.WAITING, JobState.CANCELLED},
    JobState.DONE: set(),
    JobState.CANCELLED: {JobState.WAITING},
}


class InvalidTransition(Exception):
    pass


@dataclass
class Job:
    spec: JobSpec
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: JobState = JobState.WAITING
    progress: Progress | None = None
    result: JobResult | None = None
    error: UserError | None = None
    _token: CancelToken | None = field(default=None, repr=False)
    _stop_as: JobState | None = field(default=None, repr=False)

    def move(self, new: JobState) -> None:
        if new not in _TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state.value} → {new.value} はできません")
        self.state = new


Listener = Callable[[Job], None]


class JobQueue:
    def __init__(self, listener: Listener | None = None, runner=run_job):
        self.jobs: list[Job] = []
        self.listener = listener
        self._runner = runner
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closed = False
        self._thread: threading.Thread | None = None

    # ---- 操作
    def add(self, spec: JobSpec) -> Job:
        job = Job(spec)
        with self._lock:
            self.jobs.append(job)
        self._notify(job)
        self._wake.set()
        return job

    def pause(self, job: Job) -> None:
        self._stop(job, JobState.PAUSED)

    def cancel(self, job: Job) -> None:
        self._stop(job, JobState.CANCELLED)

    def resume(self, job: Job) -> None:
        with self._lock:
            job.move(JobState.WAITING)
            job.error = None
        self._notify(job)
        self._wake.set()

    def _stop(self, job: Job, as_state: JobState) -> None:
        with self._lock:
            if job.state == JobState.RUNNING:
                job._stop_as = as_state
                if job._token:
                    job._token.cancel("pause" if as_state == JobState.PAUSED else "cancel")
                return
            job.move(as_state)
        self._notify(job)

    # ---- 実行
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="clipdl-queue", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def run_until_idle(self) -> None:
        """待機中が無くなるまで、呼び出し元のスレッドで実行する（CLI 用）。"""
        while (job := self._next()) is not None:
            self._run(job)

    def _next(self) -> Job | None:
        with self._lock:
            return next((j for j in self.jobs if j.state == JobState.WAITING), None)

    def _loop(self) -> None:
        while not self._closed:
            job = self._next()
            if job is None:
                self._wake.wait()
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        with self._lock:
            job.move(JobState.RUNNING)
            job._token = CancelToken()
            job._stop_as = None
        self._notify(job)

        def on_progress(p: Progress):
            job.progress = p
            self._notify(job)

        try:
            job.result = self._runner(job.spec, on_progress, job._token)
            new = JobState.DONE
        except (Cancelled, KeyboardInterrupt) as e:
            new = job._stop_as or JobState.CANCELLED
            if isinstance(e, KeyboardInterrupt):
                with self._lock:
                    job.move(JobState.CANCELLED)
                self._notify(job)
                raise
        except Exception as e:  # noqa: BLE001
            if job._stop_as:
                new = job._stop_as
            else:
                job.error = classify(e)
                log.error("ジョブ失敗 %s: %s", job.spec.url, job.error.raw)
                new = JobState.FAILED
        with self._lock:
            job.move(new)
            job._token = None
        self._notify(job)

    def _notify(self, job: Job) -> None:
        if self.listener:
            try:
                self.listener(job)
            except Exception:  # noqa: BLE001
                log.exception("listener error")
