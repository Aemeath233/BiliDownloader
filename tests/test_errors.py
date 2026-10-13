from __future__ import annotations

import unittest

from bili_client.core.errors import humanize_error


class ErrorMessageTests(unittest.TestCase):
    def test_certificate_error_is_not_reported_as_login_or_timeout(self) -> None:
        message = humanize_error(
            "网络请求失败: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate (_ssl.c:1010)"
        )
        self.assertIn("证书校验失败", message)
        self.assertNotIn("登录", message)
        self.assertNotIn("超时", message)

    def test_login_error_is_humanized(self) -> None:
        self.assertIn("扫码登录", humanize_error("login required: SESSDATA expired"))

    def test_stream_url_error_is_humanized(self) -> None:
        message = humanize_error("video 流下载失败：https://example.invalid/video.m4s: HTTP 403")
        self.assertIn("权限", message)

    def test_preview_error_is_preserved(self) -> None:
        message = humanize_error("当前账号只能获取预览片段，完整内容可能需要会员、购片或版权权限")
        self.assertIn("预览片段", message)

    def test_unknown_error_is_preserved(self) -> None:
        self.assertEqual("奇怪的错误", humanize_error("奇怪的错误"))


if __name__ == "__main__":
    unittest.main()
