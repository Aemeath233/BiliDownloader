from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterable

import httpx


DEFAULT_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(self, timeout: float = 20.0) -> None:
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
        self._client = httpx.Client(follow_redirects=True, timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def _build_headers(self, sessdata: str = "", extra_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.default_headers)
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata.strip()}"
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
    ) -> httpx.Response:
        retry_codes = set(DEFAULT_RETRY_STATUS_CODES if retry_status_codes is None else retry_status_codes)
        headers = self._build_headers(sessdata, extra_headers)

        for attempt in range(retries + 1):
            try:
                response = self._client.request(method, url, headers=headers, params=params, data=data)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if attempt >= retries or exc.response.status_code not in retry_codes:
                    raise
                exc.response.close()
            except httpx.HTTPError:
                if attempt >= retries:
                    raise

            time.sleep(self._retry_delay(attempt))

        raise RuntimeError("unreachable")

    def head(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 0,
        retry_status_codes: Iterable[int] | None = None,
    ) -> httpx.Response:
        return self.request(
            "HEAD",
            url,
            sessdata=sessdata,
            extra_headers=extra_headers,
            params=params,
            retries=retries,
            retry_status_codes=retry_status_codes,
        )

    def get(
        self,
        url: str,
        sessdata: str = "",
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 0,
        retry_status_codes: Iterable[int] | None = None,
    ) -> httpx.Response:
        return self.request(
            "GET",
            url,
            sessdata=sessdata,
            extra_headers=extra_headers,
            params=params,
            retries=retries,
            retry_status_codes=retry_status_codes,
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
        with self._client.stream("GET", url, headers=self._build_headers(sessdata, extra_headers)) as response:
            response.raise_for_status()
            yield response

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(0.35 * (2**attempt), 2.0)
