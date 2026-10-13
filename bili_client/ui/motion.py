from __future__ import annotations

import sys
import time
import tkinter
from collections.abc import Callable
from dataclasses import dataclass


def ease_out_cubic(value: float) -> float:
    return 1 - (1 - value) ** 3


def mix_color(start: str, end: str, fraction: float) -> str:
    channels = [
        round(int(start[index : index + 2], 16) * (1 - fraction) + int(end[index : index + 2], 16) * fraction)
        for index in (1, 3, 5)
    ]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


@dataclass
class _Tween:
    started: float
    duration: float
    start: float
    end: float
    apply: Callable[[float], None]
    complete: Callable[[], None] | None


class Motion:
    """One event-loop timer per window, running only while something animates."""

    FRAME_MS = 16

    def __init__(self, widget, enabled: bool = True, clock=time.monotonic) -> None:
        self.widget = widget
        self.enabled = enabled
        self.clock = clock
        self._tweens: dict[object, _Tween] = {}
        self._after_id = None
        self._ticking = False
        self._closed = False

    def tween(self, key, start: float, end: float, apply, duration_ms: int = 180, complete=None) -> None:
        self.cancel(key)
        if self._closed:
            return
        if not self.enabled or duration_ms <= 0 or start == end:
            apply(end)
            if complete is not None:
                complete()
            return
        self._tweens[key] = _Tween(self.clock(), duration_ms / 1000, start, end, apply, complete)
        apply(start)
        self._arm()

    def cancel(self, key) -> None:
        self._tweens.pop(key, None)
        if not self._tweens and self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            pending = list(self._tweens.values())
            self._tweens.clear()
            if self._after_id is not None:
                self.widget.after_cancel(self._after_id)
                self._after_id = None
            for tween in pending:
                tween.apply(tween.end)
                if tween.complete is not None:
                    tween.complete()

    def close(self) -> None:
        self._closed = True
        self._tweens.clear()
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _arm(self) -> None:
        if self._tweens and not self._closed and not self._ticking and self._after_id is None:
            self._after_id = self.widget.after(self.FRAME_MS, self._tick)

    def _tick(self) -> None:
        self._after_id = None
        self._ticking = True
        now = self.clock()
        try:
            for key, tween in list(self._tweens.items()):
                if self._tweens.get(key) is not tween:
                    continue
                fraction = min(1.0, max(0.0, (now - tween.started) / tween.duration))
                try:
                    tween.apply(tween.start + (tween.end - tween.start) * ease_out_cubic(fraction))
                    if fraction >= 1 and self._tweens.get(key) is tween:
                        del self._tweens[key]
                        if tween.complete is not None:
                            tween.complete()
                except tkinter.TclError:
                    self._tweens.pop(key, None)
                except Exception:
                    self._tweens.pop(key, None)
                    handler = getattr(self.widget, "report_callback_exception", None)
                    if handler is not None:
                        handler(*sys.exc_info())
        finally:
            self._ticking = False
            self._arm()


def motion_for(widget) -> Motion:
    root = widget.winfo_toplevel()
    motion = getattr(root, "_ui_motion", None)
    if motion is None:
        parent = getattr(root, "master", None)
        parent_motion = getattr(parent.winfo_toplevel(), "_ui_motion", None) if parent is not None else None
        motion = Motion(root, enabled=parent_motion.enabled if parent_motion is not None else True)
        root._ui_motion = motion
        root.bind("<Destroy>", lambda event: motion.close() if event.widget is root else None, add="+")
    return motion
