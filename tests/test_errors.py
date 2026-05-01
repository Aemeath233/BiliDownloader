from __future__ import annotations

import unittest

from bili_client.core.errors import humanize_error


class ErrorMessageTests(unittest.TestCase):
    def test_login_error_is_humanized(self) -> None:
        self.assertIn("扫码登录", humanize_error("login required: SESSDATA expired"))

    def test_stream_url_error_is_humanized(self) -> None:
        message = humanize_error("video 流下载失败：https://example.invalid/video.m4s: HTTP 403")
        self.assertIn("权限", message)

    def test_unknown_error_is_preserved(self) -> None:
        self.assertEqual("奇怪的错误", humanize_error("奇怪的错误"))


if __name__ == "__main__":
    unittest.main()
