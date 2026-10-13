from __future__ import annotations

import unittest

from bili_client.ui.motion import Motion


class _Clock:
    now = 0.0

    def __call__(self):
        return self.now


class _Widget:
    def __init__(self):
        self.callbacks = {}
        self.counter = 0

    def after(self, delay, callback):
        self.counter += 1
        self.callbacks[self.counter] = callback
        return self.counter

    def after_cancel(self, identifier):
        self.callbacks.pop(identifier, None)

    def tick(self):
        callbacks = list(self.callbacks.values())
        self.callbacks.clear()
        for callback in callbacks:
            callback()


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.widget = _Widget()
        self.clock = _Clock()
        self.motion = Motion(self.widget, clock=self.clock)

    def test_many_animations_share_one_timer_and_stop_when_finished(self):
        values = []
        for key in range(20):
            self.motion.tween(key, 0, 1, values.append, 200)
        self.assertEqual(1, len(self.widget.callbacks))
        self.clock.now = 0.1
        self.widget.tick()
        self.assertTrue(all(0 < value < 1 for value in values[-20:]))
        self.clock.now = 1
        self.widget.tick()
        self.assertEqual([1] * 20, values[-20:])
        self.assertFalse(self.widget.callbacks)

    def test_replaced_animation_never_runs_stale_completion(self):
        completed = []
        values = []
        self.motion.tween("page", 0, 1, values.append, 100, lambda: completed.append("old"))
        self.motion.tween("page", 0, 2, values.append, 100, lambda: completed.append("new"))
        self.clock.now = 1
        self.widget.tick()
        self.assertEqual(["new"], completed)
        self.assertEqual(2, values[-1])

    def test_disabling_motion_settles_pending_work_immediately(self):
        values, completed = [], []
        self.motion.tween("progress", 0, 1, values.append, complete=lambda: completed.append(True))
        self.motion.set_enabled(False)
        self.assertEqual(1, values[-1])
        self.assertEqual([True], completed)
        self.assertFalse(self.widget.callbacks)

    def test_close_cancels_callbacks_and_rejects_future_animations(self):
        values = []
        self.motion.tween("page", 0, 1, values.append)
        self.motion.close()
        self.motion.tween("page", 0, 1, values.append)
        self.assertEqual([0], values)
        self.assertFalse(self.widget.callbacks)

    def test_one_failing_animation_does_not_block_other_updates(self):
        values, errors = [], []
        self.widget.report_callback_exception = lambda *args: errors.append(args)
        calls = 0

        def broken(_value):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ValueError("bad animation")

        self.motion.tween("bad", 0, 1, broken)
        self.motion.tween("good", 0, 1, values.append)
        self.clock.now = 1
        self.widget.tick()
        self.assertEqual(1, values[-1])
        self.assertEqual(1, len(errors))
        self.assertFalse(self.widget.callbacks)
