from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


class UiTaskRunner:
    def __init__(self, widget) -> None:
        self.widget = widget

    def run(
        self,
        name: str,
        work: Callable[[], T],
        on_success: Callable[[T], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_finally: Callable[[], None] | None = None,
    ) -> None:
        def worker() -> None:
            try:
                result = work()
            except Exception as exc:
                if on_error is not None:
                    self._schedule(lambda exc=exc: on_error(exc))
            else:
                if on_success is not None:
                    self._schedule(lambda result=result: on_success(result))
            finally:
                if on_finally is not None:
                    self._schedule(on_finally)

        threading.Thread(target=worker, daemon=True, name=name).start()

    def _schedule(self, callback: Callable[[], None]) -> None:
        try:
            self.widget.after(0, callback)
        except RuntimeError:
            pass
