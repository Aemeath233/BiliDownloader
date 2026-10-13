from __future__ import annotations

import threading
import time
import unittest

from bili_client.services.rate_limiter import GlobalRateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_amount_larger_than_bucket_capacity_completes(self) -> None:
        limiter = GlobalRateLimiter(20)
        result: list[bool] = []
        worker = threading.Thread(target=lambda: result.append(limiter.acquire(25)), daemon=True)

        worker.start()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([True], result)

    def test_wait_can_be_canceled(self) -> None:
        limiter = GlobalRateLimiter(1)
        cancel_event = threading.Event()
        result: list[bool] = []
        worker = threading.Thread(
            target=lambda: result.append(limiter.acquire(100, cancel_events=(cancel_event,))),
            daemon=True,
        )
        worker.start()
        time.sleep(0.05)

        cancel_event.set()
        limiter.update_limit(1)
        worker.join(timeout=0.5)

        self.assertFalse(worker.is_alive())
        self.assertEqual([False], result)


if __name__ == "__main__":
    unittest.main()
