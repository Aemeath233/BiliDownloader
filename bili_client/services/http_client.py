from __future__ import annotations

import os
import random
import ssl
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any

import certifi
import httpx

from bili_client.core.security import is_account_host
from bili_client.services.cookies import normalize_cookie_value

DEFAULT_RETRY_STATUS_CODES = {412, 429, 500, 502, 503, 504}
RISK_CONTROL_STATUS_CODES = {412, 429}


def create_ssl_context() -> ssl.SSLContext:
    # Keep HTTPX's explicit CA overrides authoritative when configured.
    if cafile := os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=cafile)
    if capath := os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=capath)

    # HTTPX's default bundle omits locally trusted Windows certificates.
    # Load both sources, retaining hostname and certificate chain validation.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


class HttpClient:
    def __init__(self, timeout: float = 20.0, steady_mode: bool = True, *, transport=None) -> None:
        self.timeout = httpx.Timeout(timeout=timeout, connect=10.0, read=60.0)
        self.default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self._client = httpx.Client(
            verify=create_ssl_context(),
            follow_redirects=True,
            timeout=self.timeout,
            transport=transport,
            event_hooks={"request": [self._guard_credentials]},
        )
        self._steady_mode = bool(steady_mode)
        self._pacing_lock = threading.Lock()
        self._last_request_at = 0.0
        self._cooldown_until = 0.0

    def close(self) -> None:
        self._client.close()

    def clear_cookies(self) -> None:
        self._client.cookies.clear()

    def set_steady_mode(self, enabled: bool) -> None:
        self._steady_mode = bool(enabled)

    @staticmethod
    def _guard_credentials(request: httpx.Request) -> None:
        if request.url.scheme != "https":
            raise httpx.RequestError("网络请求必须使用 HTTPS，已阻止不安全的重定向", request=request)
        allowed_hosts = request.extensions.get("allowed_hosts")
        if allowed_hosts and request.url.host not in allowed_hosts:
            raise httpx.RequestError("已阻止视频链接跳转到非 B 站地址", request=request)
        if is_account_host(request.url.host):
            if request.headers.get("Cookie") or request.headers.get("Authorization"):
                request.extensions["bili_authenticated"] = True
        else:
            request.headers.pop("Cookie", None)
            request.headers.pop("Authorization", None)
            if request.extensions.get("bili_authenticated") and request.method not in {"GET", "HEAD"}:
                raise httpx.RequestError("已阻止登录请求跳转到非 B 站地址", request=request)

    def _build_headers(self, sessdata: str = "", extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.default_headers)
        if sessdata:
            headers["Cookie"] = f"SESSDATA={normalize_cookie_value(sessdata)}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def request(
        self,
        method: str,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retries: int = 0,
        retry_status_codes: Iterable[int] | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> httpx.Response:
        retry_codes = set(DEFAULT_RETRY_STATUS_CODES if retry_status_codes is None else retry_status_codes)
        headers = self._build_headers(sessdata, extra_headers)

        for attempt in range(retries + 1):
            status_code: int | None = None
            try:
                self._wait_for_request_slot()
                response = self._client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    data=data,
                    extensions={
                        "bili_authenticated": bool(
                            httpx.Headers(headers).get("Cookie") or httpx.Headers(headers).get("Authorization")
                        ),
                        "allowed_hosts": allowed_hosts,
                    },
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                self._record_status(status_code, attempt)
                if attempt >= retries or status_code not in retry_codes:
                    raise
                exc.response.close()
            except httpx.HTTPError:
                if attempt >= retries:
                    raise

            time.sleep(self._retry_delay(attempt, status_code))

        raise RuntimeError("unreachable")

    def head(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 0,
        retry_status_codes: Iterable[int] | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> httpx.Response:
        return self.request(
            "HEAD",
            url,
            sessdata=sessdata,
            extra_headers=extra_headers,
            params=params,
            retries=retries,
            retry_status_codes=retry_status_codes,
            allowed_hosts=allowed_hosts,
        )

    def get(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 0,
        retry_status_codes: Iterable[int] | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ) -> httpx.Response:
        return self.request(
            "GET",
            url,
            sessdata=sessdata,
            extra_headers=extra_headers,
            params=params,
            retries=retries,
            retry_status_codes=retry_status_codes,
            allowed_hosts=allowed_hosts,
        )

    def post(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retries: int = 0,
        retry_status_codes: Iterable[int] | None = None,
    ) -> httpx.Response:
        return self.request(
            "POST",
            url,
            sessdata=sessdata,
            extra_headers=extra_headers,
            params=params,
            data=data,
            retries=retries,
            retry_status_codes=retry_status_codes,
        )

    def get_json(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 0,
        retry_status_codes: Iterable[int] | None = None,
    ) -> dict[str, Any]:
        response = self.get(
            url,
            sessdata=sessdata,
            extra_headers=extra_headers,
            params=params,
            retries=retries,
            retry_status_codes=retry_status_codes,
        )
        return response.json()

    @contextmanager
    def stream(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        self._wait_for_request_slot()
        with self._client.stream("GET", url, headers=self._build_headers(sessdata, extra_headers)) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_status(exc.response.status_code, 0)
                raise
            yield response

    def _wait_for_request_slot(self) -> None:
        while True:
            with self._pacing_lock:
                now = time.monotonic()
                wait_seconds = max(0.0, self._cooldown_until - now)
                if self._steady_mode:
                    wait_seconds = max(wait_seconds, self._last_request_at + 0.18 - now)
                if wait_seconds <= 0:
                    self._last_request_at = now
                    return
            time.sleep(min(wait_seconds, 0.5))

    def _record_status(self, status_code: int, attempt: int) -> None:
        if status_code not in RISK_CONTROL_STATUS_CODES:
            return
        pause_seconds = self._risk_control_pause(attempt)
        with self._pacing_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + pause_seconds)

    @staticmethod
    def _retry_delay(attempt: int, status_code: int | None = None) -> float:
        if status_code in RISK_CONTROL_STATUS_CODES:
            return min(2.5 * (2**attempt) + random.uniform(0.2, 0.7), 15.0)
        return min(0.35 * (2**attempt), 2.0)

    @staticmethod
    def _risk_control_pause(attempt: int) -> float:
        return min(2.5 * (2**attempt) + random.uniform(0.2, 0.7), 15.0)
