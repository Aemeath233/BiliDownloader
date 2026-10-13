from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx

from bili_client.core.security import normalize_share_url, redact_sensitive
from bili_client.services.bili_api import BiliApiError, BilibiliApi
from bili_client.services.http_client import HttpClient
from bili_client.services.log_store import LogStore


class RequestSecurityTests(unittest.TestCase):
    def client(self, handler):
        client = HttpClient(steady_mode=False, transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_foreign_share_urls_are_rejected_before_network_access(self):
        client = Mock()
        api = BilibiliApi(client)
        for url in (
            "https://evil.invalid/BV1xx411c7mD",
            "https://www.bilibili.com.evil.invalid/video/BV1xx411c7mD",
            "https://www.bilibili.com@evil.invalid/BV1xx411c7mD",
            "https://evil@www.bilibili.com/video/BV1xx411c7mD",
            "https://www.bilibili.com:444/video/BV1xx411c7mD",
        ):
            with self.subTest(url=url), self.assertRaises(BiliApiError):
                api.parse_media(url, sessdata="dummy-secret")
        client.head.assert_not_called()
        client.get.assert_not_called()

    def test_http_share_urls_are_upgraded_to_https(self):
        self.assertEqual(
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            normalize_share_url("http://www.bilibili.com/video/BV1xx411c7mD?p=2"),
        )

    def test_account_cookie_is_not_sent_to_media_or_unrelated_hosts(self):
        observed = []

        def handler(request):
            observed.append((request.url.host, request.headers.get("cookie")))
            return httpx.Response(200, json={"code": 0})

        client = self.client(handler)
        client.get("https://api.bilibili.com/api", sessdata="dummy-secret")
        client.get("https://media.bilivideo.com/file", sessdata="dummy-secret")
        client._client.cookies.set("SESSDATA", "jar-secret", domain="evil.invalid")
        client.get("https://evil.invalid/file", sessdata="dummy-secret")
        self.assertEqual("SESSDATA=dummy-secret", observed[0][1])
        self.assertIsNone(observed[1][1])
        self.assertIsNone(observed[2][1])

    def test_cookie_is_stripped_on_cross_origin_redirect(self):
        observed = []

        def handler(request):
            observed.append(request.headers.get("cookie"))
            if request.url.host == "www.bilibili.com":
                return httpx.Response(302, headers={"Location": "https://evil.invalid/end"})
            return httpx.Response(200)

        self.client(handler).get("https://www.bilibili.com/start", sessdata="dummy-secret")
        self.assertEqual(["SESSDATA=dummy-secret", None], observed)

    def test_authenticated_post_redirect_is_blocked_before_sending_body(self):
        observed = []

        def handler(request):
            observed.append(request.url.host)
            return httpx.Response(307, headers={"Location": "https://evil.invalid/end"})

        client = self.client(handler)
        with self.assertRaises(httpx.RequestError):
            client.post(
                "https://passport.bilibili.com/start", sessdata="dummy-secret", data={"refresh_token": "dummy-refresh"}
            )
        self.assertEqual(["passport.bilibili.com"], observed)

    def test_lowercase_cookie_header_cannot_bypass_post_guard(self):
        observed = []

        def handler(request):
            observed.append(request.url.host)
            return httpx.Response(200)

        with self.assertRaises(httpx.RequestError):
            self.client(handler).post(
                "https://evil.invalid/post", extra_headers={"cookie": "SESSDATA=dummy"}, data={"refresh_token": "dummy"}
            )
        self.assertEqual([], observed)

    def test_share_redirect_cannot_contact_external_host(self):
        observed = []

        def handler(request):
            observed.append(request.url.host)
            return httpx.Response(302, headers={"Location": "https://evil.invalid/BV1xx411c7mD"})

        client = self.client(handler)
        with self.assertRaises(httpx.RequestError):
            client.head("https://b23.tv/test", allowed_hosts=frozenset({"b23.tv", "www.bilibili.com"}))
        self.assertEqual(["b23.tv"], observed)

    def test_https_downgrade_is_blocked(self):
        observed = []

        def handler(request):
            observed.append(request.url.scheme)
            return httpx.Response(302, headers={"Location": "http://www.bilibili.com/end"})

        with self.assertRaises(httpx.RequestError):
            self.client(handler).get("https://www.bilibili.com/start", sessdata="dummy-secret")
        self.assertEqual(["https"], observed)


class LoggingSecurityTests(unittest.TestCase):
    def test_cookie_and_token_values_are_redacted(self):
        for message in (
            "SESSDATA=private; bili_jct=private",
            '{"refresh_token": "private"}',
            "https://api.bilibili.com?a=1&csrf=private",
            "Authorization: private",
            "Authorization: Bearer private-token",
        ):
            with self.subTest(message=message):
                self.assertNotIn("private", redact_sensitive(message))

    def test_disk_and_ui_logs_are_both_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = LogStore(Path(temporary))
            line = store.emit("请求失败: SESSDATA=private-token")
            content = store.current_log_file.read_text(encoding="utf-8")
        self.assertNotIn("private-token", line)
        self.assertNotIn("private-token", content)
