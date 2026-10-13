from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from bili_client.services.auth_service import AuthService, AuthSession
from bili_client.services.http_client import HttpClient
from bili_client.services.secure_store import protection_available


def make_http_client(handler) -> HttpClient:
    client = HttpClient()
    client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=client.timeout)
    return client


class AuthServiceTests(unittest.TestCase):
    def test_auth_session_cookie_header_encodes_sessdata_like_browser_cookie(self) -> None:
        session = AuthSession(cookies={"SESSDATA": "abc,123,*token", "bili_jct": "csrf"})

        self.assertIn("SESSDATA=abc%2C123%2C%2Atoken", session.cookie_header())
        self.assertIn("bili_jct=csrf", session.cookie_header())

    def test_callback_cookie_values_are_stored_encoded(self) -> None:
        cookies = AuthService._cookies_from_callback_url(
            "https://example.invalid/?SESSDATA=abc%2C123%2C%2Atoken&bili_jct=csrf"
        )

        self.assertEqual("abc%2C123%2C%2Atoken", cookies["SESSDATA"])

    def test_qr_login_success_saves_cookies_and_refresh_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "auth.json"

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/qrcode/generate"):
                    return httpx.Response(
                        200,
                        request=request,
                        json={"code": 0, "data": {"url": "https://qr.example/login", "qrcode_key": "key-1"}},
                    )
                if request.url.path.endswith("/qrcode/poll"):
                    return httpx.Response(
                        200,
                        request=request,
                        headers=[
                            ("set-cookie", "SESSDATA=sess-from-cookie; Path=/; HttpOnly"),
                            ("set-cookie", "bili_jct=csrf-from-cookie; Path=/"),
                        ],
                        json={"code": 0, "data": {"code": 0, "url": "", "refresh_token": "refresh-1"}},
                    )
                raise AssertionError(f"unexpected request: {request.url}")

            client = make_http_client(handler)
            self.addCleanup(client.close)
            service = AuthService(client, auth_file)

            qr = service.create_qr_login()
            result = service.poll_qr_login(qr.qrcode_key)

            self.assertEqual("https://qr.example/login", qr.url)
            self.assertIsNotNone(result.session)
            assert result.session is not None
            self.assertEqual("sess-from-cookie", result.session.sessdata)
            self.assertEqual("csrf-from-cookie", result.session.csrf)
            self.assertEqual("refresh-1", result.session.refresh_token)
            self.assertTrue(auth_file.exists())
            if protection_available():
                self.assertNotIn("sess-from-cookie", auth_file.read_text(encoding="utf-8"))

    def test_ensure_latest_session_keeps_valid_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "auth.json"

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertIn("SESSDATA=old-sess", request.headers.get("cookie", ""))
                return httpx.Response(200, request=request, json={"code": 0, "data": {"refresh": False}})

            client = make_http_client(handler)
            self.addCleanup(client.close)
            service = AuthService(client, auth_file)
            service.save_session(AuthSession(cookies={"SESSDATA": "old-sess", "bili_jct": "csrf"}, refresh_token="rt"))

            session = service.ensure_latest_session(force=True)

            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual("old-sess", session.sessdata)
            self.assertGreater(session.last_checked_at, 0)

    def test_ensure_latest_session_refreshes_cookie_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "auth.json"
            calls: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(request.url.path)
                if request.url.path.endswith("/cookie/info"):
                    return httpx.Response(
                        200,
                        request=request,
                        json={"code": 0, "data": {"refresh": True, "timestamp": 1710000000000}},
                    )
                if request.url.path.endswith("/cookie/refresh"):
                    body = request.content.decode("utf-8")
                    self.assertIn("refresh_csrf=mock-refresh-csrf", body)
                    self.assertIn("refresh_token=old-rt", body)
                    return httpx.Response(
                        200,
                        request=request,
                        headers=[
                            ("set-cookie", "SESSDATA=new-sess; Path=/; HttpOnly"),
                            ("set-cookie", "bili_jct=new-csrf; Path=/"),
                        ],
                        json={"code": 0, "data": {"refresh_token": "new-rt"}},
                    )
                if request.url.path.endswith("/confirm/refresh"):
                    self.assertIn("SESSDATA=new-sess", request.headers.get("cookie", ""))
                    return httpx.Response(200, request=request, json={"code": 0})
                raise AssertionError(f"unexpected request: {request.url}")

            client = make_http_client(handler)
            self.addCleanup(client.close)
            service = AuthService(client, auth_file)
            service._get_refresh_csrf = lambda _session, _timestamp: "mock-refresh-csrf"  # type: ignore[method-assign]
            service.save_session(
                AuthSession(cookies={"SESSDATA": "old-sess", "bili_jct": "old-csrf"}, refresh_token="old-rt")
            )

            session = service.ensure_latest_session(force=True)

            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual("new-sess", session.sessdata)
            self.assertEqual("new-csrf", session.csrf)
            self.assertEqual("new-rt", session.refresh_token)
            self.assertTrue(any(path.endswith("/confirm/refresh") for path in calls))

    def test_plaintext_auth_file_is_migrated_to_current_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "auth.json"
            auth_file.write_text(
                json.dumps(AuthSession(cookies={"SESSDATA": "legacy-secret"}).to_json()),
                encoding="utf-8",
            )
            client = make_http_client(lambda request: httpx.Response(200, request=request, json={"code": 0}))
            self.addCleanup(client.close)

            session = AuthService(client, auth_file).load_session()

            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual("legacy-secret", session.sessdata)
            migrated = json.loads(auth_file.read_text(encoding="utf-8"))
            self.assertEqual(2, migrated["version"])
            if protection_available():
                self.assertNotIn("legacy-secret", auth_file.read_text(encoding="utf-8"))
            reloaded = AuthService(client, auth_file).load_session()
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            self.assertEqual("legacy-secret", reloaded.sessdata)


if __name__ == "__main__":
    unittest.main()
