from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class _Dispatcher:
    MAX_CALLBACKS = 64
    BUDGET_SECONDS = 0.006

    def __init__(self, widget) -> None:
        self.widget = widget
        self.callbacks: queue.Queue[Callable[[], None]] = queue.Queue()
        self._closed = False
        self._after_id = None
        if hasattr(widget, "bind"):
            widget.bind("<Destroy>", lambda event: self.close() if event.widget is widget else None, add="+")
        self._arm(25)

    def close(self) -> None:
        self._closed = True
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _arm(self, delay: int) -> None:
        if self._closed:
            return
        try:
            self._after_id = self.widget.after(delay, self.drain)
        except (RuntimeError, tkinter.TclError):
            self._closed = True

    def drain(self) -> None:
        self._after_id = None
        deadline = time.perf_counter() + self.BUDGET_SECONDS
        try:
            for index in range(self.MAX_CALLBACKS):
                if self._closed or (index > 0 and time.perf_counter() >= deadline):
                    break
                try:
                    callback = self.callbacks.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback()
                except Exception:
                    handler = getattr(self.widget, "report_callback_exception", None)
                    if handler is not None:
                        handler(*sys.exc_info())
        finally:
            self._arm(1 if not self.callbacks.empty() else 25)


class UiTaskRunner:
    """Workers only enqueue callbacks; pages share their window's dispatcher."""

    def __init__(self, widget) -> None:
        self.widget = widget
        self._closed = False
        root = widget.winfo_toplevel() if hasattr(widget, "winfo_toplevel") else widget
        dispatcher = getattr(root, "_ui_dispatcher", None)
        if dispatcher is None:
            dispatcher = _Dispatcher(root)
            root._ui_dispatcher = dispatcher
        self._dispatcher = dispatcher
        self._callbacks = dispatcher.callbacks
        if hasattr(widget, "bind"):
            widget.bind("<Destroy>", lambda event: self.close() if event.widget is widget else None, add="+")

    def close(self) -> None:
        self._closed = True

    def run(
        self,
        name: str,
        work: Callable[[], T],
        on_success: Callable[[T], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_finally: Callable[[], None] | None = None,
    ) -> None:
        if self._closed:
            return

        def worker() -> None:
            try:
                result = work()
            except Exception as exc:
                if on_error is not None:
                    self.post(lambda exc=exc: on_error(exc))
            else:
                if on_success is not None:
                    self.post(lambda result=result: on_success(result))
            finally:
                if on_finally is not None:
                    self.post(on_finally)

        threading.Thread(target=worker, daemon=True, name=name).start()

    def post(self, callback: Callable[[], None]) -> None:
        if not self._closed and not self._dispatcher._closed:
            self._callbacks.put(lambda: callback() if not self._closed else None)

    def _drain_callbacks(self) -> None:
        self._dispatcher.drain()
