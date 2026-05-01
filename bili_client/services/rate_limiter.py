from __future__ import annotations

import threading
import time


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

    def acquire(self, amount: int) -> None:
        if amount <= 0:
            return

        while True:
            with self._lock:
                self._refill_tokens()
                if self._bytes_per_second <= 0:
                    return
                if self._tokens >= amount:
                    self._tokens -= amount
                    return

                missing = amount - self._tokens
                wait_seconds = missing / self._bytes_per_second if self._bytes_per_second else 0.1

            time.sleep(min(max(wait_seconds, 0.01), 0.25))

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
