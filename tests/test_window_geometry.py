from __future__ import annotations

import unittest
from unittest.mock import patch

import customtkinter as ctk

from bili_client.ui.main_window import MainWindow


class WindowGeometryTests(unittest.TestCase):
    def test_initial_window_fits_screen_at_common_scales(self):
        for scale in (1, 1.25, 1.5, 2):
            with (
                self.subTest(scale=scale),
                patch.object(MainWindow, "_get_window_scaling", return_value=scale),
                patch.object(MainWindow, "winfo_screenwidth", return_value=1920),
                patch.object(MainWindow, "winfo_screenheight", return_value=1080),
            ):
                app = MainWindow()
                try:
                    # CTk remembers the logical geometry before Windows maps it.
                    self.assertLessEqual(app._current_width * scale, 1920 - 48)
                    self.assertLessEqual(app._current_height * scale, 1080 - 96)
                finally:
                    app.destroy()
        ctk.set_window_scaling(1)
