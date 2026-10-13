from __future__ import annotations

import threading
import time
from collections.abc import Iterable


class GlobalRateLimiter:
    def __init__(self, bytes_per_second: int = 0) -> None:
        self._bytes_per_second = max(0, int(bytes_per_second))
        self._tokens = float(self._bytes_per_second)
        self._updated_at = time.monotonic()
        self._lock = threading.Condition()

    def update_limit(self, bytes_per_second: int) -> None:
        with self._lock:
            self._refill_tokens()
            self._bytes_per_second = max(0, int(bytes_per_second))
            self._tokens = float(self._bytes_per_second or 0)
            self._lock.notify_all()

    def get_limit(self) -> int:
        with self._lock:
            return self._bytes_per_second

    def acquire(self, amount: int, cancel_events: Iterable[threading.Event] = ()) -> bool:
        if amount <= 0:
            return True

        events = tuple(cancel_events)
        remaining = float(amount)

        while remaining > 0:
            if any(event.is_set() for event in events):
                return False
            with self._lock:
                self._refill_tokens()
                if self._bytes_per_second <= 0:
                    return True

                granted = min(remaining, self._tokens)
                if granted > 0:
                    self._tokens -= granted
                    remaining -= granted
                    if remaining <= 0:
                        return True

                target = min(remaining, max(1.0, self._bytes_per_second * 0.1))
                wait_seconds = target / self._bytes_per_second
                self._lock.wait(timeout=min(max(wait_seconds, 0.01), 0.1))

        return True

    def _refill_tokens(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        self._updated_at = now

        if self._bytes_per_second <= 0:
            self._tokens = 0
            return

        self._tokens = min(
            self._bytes_per_second,
            self._tokens + elapsed * self._bytes_per_second,
        )
