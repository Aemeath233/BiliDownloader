from __future__ import annotations

import unittest

import httpx

from bili_client.services.http_client import HttpClient


class HttpClientRetryTests(unittest.TestCase):
    def test_get_json_retries_retryable_status(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(503, request=request)
            return httpx.Response(200, request=request, json={"ok": True})

        client = HttpClient()
        client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=client.timeout)
        self.addCleanup(client.close)

        data = client.get_json("https://example.com/api", retries=2)

        self.assertEqual({"ok": True}, data)
        self.assertEqual(3, attempts)


if __name__ == "__main__":
    unittest.main()
