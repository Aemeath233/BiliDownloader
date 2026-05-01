from __future__ import annotations

import threading
import unittest

from bili_client.services.single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    def test_notify_existing_invokes_activation_callback(self) -> None:
        instance = SingleInstance(port=0)
        self.assertTrue(instance.acquire())
        self.addCleanup(instance.close)

        activated = threading.Event()
        instance.set_activate_callback(activated.set)

        notifier = SingleInstance(port=instance.port)

        self.assertTrue(notifier.notify_existing())
        self.assertTrue(activated.wait(timeout=1.0))


if __name__ == "__main__":
    unittest.main()
