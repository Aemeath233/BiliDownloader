from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from bili_client.ui.async_tasks import UiTaskRunner


class _FakeWidget:
    def __init__(self) -> None:
        self.callbacks = []
        self.after_thread_ids: list[int] = []

    def after(self, _delay: int, callback) -> None:
        self.after_thread_ids.append(threading.get_ident())
        self.callbacks.append(callback)


class UiTaskRunnerTests(unittest.TestCase):
    def test_busy_frame_still_processes_at_least_one_callback(self) -> None:
        widget = _FakeWidget()
        runner = UiTaskRunner(widget)
        results = []
        runner.post(lambda: results.append(1))
        runner.post(lambda: results.append(2))
        with patch("bili_client.ui.async_tasks.time.perf_counter", side_effect=[0, 1]):
            widget.callbacks.pop(0)()
        self.assertEqual([1], results)
        self.assertFalse(runner._callbacks.empty())

    def test_pages_share_the_same_dispatcher(self) -> None:
        root = _FakeWidget()
        page = type("Page", (), {"winfo_toplevel": lambda _self: root})()
        first, second = UiTaskRunner(root), UiTaskRunner(page)
        self.assertIs(first._dispatcher, second._dispatcher)
        self.assertEqual(1, len(root.callbacks))

    def test_callback_burst_yields_before_draining_everything(self) -> None:
        widget = _FakeWidget()
        runner = UiTaskRunner(widget)
        results = []
        for index in range(1000):
            runner.post(lambda index=index: results.append(index))
        widget.callbacks.pop(0)()
        self.assertGreater(len(results), 0)
        self.assertLessEqual(len(results), 64)
        self.assertFalse(runner._callbacks.empty())

    def test_failed_callback_does_not_drop_the_next_result(self) -> None:
        widget = _FakeWidget()
        errors = []
        widget.report_callback_exception = lambda *args: errors.append(args)
        runner = UiTaskRunner(widget)
        runner.post(lambda: 1 / 0)
        results = []
        runner.post(lambda: results.append(42))
        widget.callbacks.pop(0)()
        self.assertEqual([42], results)
        self.assertEqual(1, len(errors))

    def test_closed_page_drops_callbacks_already_posted(self) -> None:
        widget = _FakeWidget()
        runner = UiTaskRunner(widget)
        results = []
        runner.post(lambda: results.append(42))
        runner.close()
        widget.callbacks.pop(0)()
        self.assertEqual([], results)

    def test_worker_posts_without_calling_tk_from_worker_thread(self) -> None:
        widget = _FakeWidget()
        main_thread_id = threading.get_ident()
        runner = UiTaskRunner(widget)
        results: list[int] = []

        runner.run("test-ui-task", lambda: 42, on_success=results.append)
        deadline = time.time() + 1
        while runner._callbacks.empty() and time.time() < deadline:
            time.sleep(0.01)
        widget.callbacks.pop(0)()

        self.assertEqual([42], results)
        self.assertTrue(widget.after_thread_ids)
        self.assertEqual({main_thread_id}, set(widget.after_thread_ids))


if __name__ == "__main__":
    unittest.main()
