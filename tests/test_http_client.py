from __future__ import annotations

import os
import ssl
import unittest
from unittest.mock import patch

import certifi
import httpx

from bili_client.services.http_client import HttpClient, create_ssl_context


class HttpClientCertificateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        os.environ.pop("SSL_CERT_FILE", None)
        os.environ.pop("SSL_CERT_DIR", None)

    def test_system_and_certifi_roots_are_trusted_with_verification_enabled(self) -> None:
        system_roots = set(ssl.create_default_context().get_ca_certs(binary_form=True))
        bundle_roots = set(ssl.create_default_context(cafile=certifi.where()).get_ca_certs(binary_form=True))

        context = create_ssl_context()
        trusted_roots = set(context.get_ca_certs(binary_form=True))

        self.assertTrue(system_roots <= trusted_roots)
        self.assertTrue(bundle_roots <= trusted_roots)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)

    def test_explicit_ca_file_is_authoritative_and_takes_precedence_over_directory(self) -> None:
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ["SSL_CERT_DIR"] = "unused-ca-directory"
        expected_roots = set(ssl.create_default_context(cafile=certifi.where()).get_ca_certs(binary_form=True))

        context = create_ssl_context()

        self.assertEqual(expected_roots, set(context.get_ca_certs(binary_form=True)))
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)

    def test_explicit_ca_directory_is_respected(self) -> None:
        os.environ["SSL_CERT_DIR"] = "configured-ca-directory"
        with patch("bili_client.services.http_client.ssl.create_default_context") as create_context:
            self.assertIs(create_context.return_value, create_ssl_context())
        create_context.assert_called_once_with(capath="configured-ca-directory")

    def test_invalid_explicit_ca_file_fails_instead_of_disabling_verification(self) -> None:
        os.environ["SSL_CERT_FILE"] = os.path.join(os.path.dirname(__file__), "missing-ca.pem")
        with self.assertRaises(FileNotFoundError):
            create_ssl_context()


class HttpClientRetryTests(unittest.TestCase):
    def test_sessdata_header_uses_browser_cookie_encoding(self) -> None:
        seen_cookie = ""

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_cookie
            seen_cookie = request.headers.get("cookie", "")
            return httpx.Response(200, request=request, json={"ok": True})

        client = HttpClient(steady_mode=False)
        client.close()
        client._client = httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True, timeout=client.timeout
        )
        self.addCleanup(client.close)

        client.get_json("https://example.com/api", sessdata="abc,123,*token")

        self.assertIn("SESSDATA=abc%2C123%2C%2Atoken", seen_cookie)

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
        client._client = httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True, timeout=client.timeout
        )
        self.addCleanup(client.close)

        data = client.get_json("https://example.com/api", retries=2)

        self.assertEqual({"ok": True}, data)
        self.assertEqual(3, attempts)

    def test_get_json_retries_risk_control_status(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(412, request=request)
            return httpx.Response(200, request=request, json={"ok": True})

        client = HttpClient(steady_mode=False)
        client.close()
        client._client = httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True, timeout=client.timeout
        )
        client._record_status = lambda _status_code, _attempt: None  # type: ignore[method-assign]
        client._retry_delay = lambda _attempt, _status_code=None: 0.0  # type: ignore[method-assign]
        self.addCleanup(client.close)

        data = client.get_json("https://example.com/api", retries=1)

        self.assertEqual({"ok": True}, data)
        self.assertEqual(2, attempts)


if __name__ == "__main__":
    unittest.main()
