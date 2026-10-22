from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TaskWriter:
    """One ordered writer; short bursts share a snapshot and failures stay observable."""

    def __init__(self, save_snapshot: Callable[[], None], logger: Callable[[str], None]) -> None:
        self._save_snapshot = save_snapshot
        self._logger = logger
        self._condition = threading.Condition()
        self._requested = 0
        self._attempted = 0
        self._running = False
        self._flush_requested = False
        self._error: Exception | None = None

    @property
    def error(self) -> str:
        with self._condition:
            return str(self._error) if self._error else ""

    def request(self) -> None:
        with self._condition:
            self._requested += 1
            if not self._running:
                self._running = True
                threading.Thread(target=self._run, daemon=True, name="task-writer").start()
            self._condition.notify_all()

    def flush(self, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            target = self._requested
            self._flush_requested = True
            self._condition.notify_all()
            while self._attempted < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("任务记录保存超时，最近的任务状态可能未保存")
                self._condition.wait(remaining)
            if self._error is not None:
                raise OSError(f"任务记录保存失败：{self._error}") from self._error

    def _run(self) -> None:
        while True:
            with self._condition:
                # A fixed batching window avoids both excessive writes and starvation.
                deadline = time.monotonic() + 0.1
                while not self._flush_requested and time.monotonic() < deadline:
                    self._condition.wait(deadline - time.monotonic())
                version = self._requested
                self._flush_requested = False
            error = None
            try:
                self._save_snapshot()
            except Exception as exc:
                error = exc
                try:
                    self._logger(f"任务记录保存失败，运行中的下载继续：{exc}")
                except Exception:
                    pass
            with self._condition:
                self._error = error
                self._attempted = version
                self._condition.notify_all()
                if self._requested == version:
                    self._running = False
                    return
