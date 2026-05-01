from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any
from urllib.parse import parse_qsl, urlparse

from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA

from bili_client.services.http_client import HttpClient


class AuthError(RuntimeError):
    pass


@dataclass(slots=True)
class AuthSession:
    cookies: dict[str, str] = field(default_factory=dict)
    refresh_token: str = ""
    updated_at: float = field(default_factory=time.time)
    last_checked_at: float = 0.0

    @property
    def sessdata(self) -> str:
        return self.cookies.get("SESSDATA", "")

    @property
    def csrf(self) -> str:
        return self.cookies.get("bili_jct", "")

    def cookie_header(self) -> str:
        preferred_order = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")
        pairs = [(name, self.cookies[name]) for name in preferred_order if self.cookies.get(name)]
        pairs.extend((name, value) for name, value in self.cookies.items() if name not in preferred_order and value)
        return "; ".join(f"{name}={value}" for name, value in pairs)

    def to_json(self) -> dict[str, Any]:
        return {
            "cookies": self.cookies,
            "refresh_token": self.refresh_token,
            "updated_at": self.updated_at,
            "last_checked_at": self.last_checked_at,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "AuthSession":
        cookies = payload.get("cookies") or {}
        if not isinstance(cookies, dict):
            cookies = {}
        return cls(
            cookies={str(key): str(value) for key, value in cookies.items() if value},
            refresh_token=str(payload.get("refresh_token", "")),
            updated_at=float(payload.get("updated_at", 0) or 0),
            last_checked_at=float(payload.get("last_checked_at", 0) or 0),
        )


@dataclass(slots=True)
class QrLoginInfo:
    url: str
    qrcode_key: str


@dataclass(slots=True)
class QrPollResult:
    code: int
    message: str
    session: AuthSession | None = None


class AuthService:
    QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    COOKIE_INFO_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
    COOKIE_REFRESH_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/refresh"
    COOKIE_CONFIRM_URL = "https://passport.bilibili.com/x/passport-login/web/confirm/refresh"
    CORRESPOND_URL = "https://www.bilibili.com/correspond/1/{path}"
    PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""

    def __init__(
        self,
        http_client: HttpClient,
        auth_file: Path,
        logger: Callable[[str], None] | None = None,
        check_interval_seconds: float = 0.0,
    ) -> None:
        self.http_client = http_client
        self.auth_file = auth_file
        self.logger = logger or (lambda _message: None)
        self.check_interval_seconds = max(0.0, check_interval_seconds)
        self._session: AuthSession | None = None
        self._lock = threading.RLock()

    def load_session(self) -> AuthSession | None:
        if self._session is not None:
            return self._session
        if not self.auth_file.exists():
            return None
        try:
            data = json.loads(self.auth_file.read_text(encoding="utf-8"))
            self._session = AuthSession.from_json(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self._session = None
        return self._session

    def save_session(self, session: AuthSession) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(json.dumps(session.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._session = session

    def create_qr_login(self) -> QrLoginInfo:
        payload = self.http_client.get_json(self.QR_GENERATE_URL, retries=1)
        if payload.get("code") != 0:
            raise AuthError(payload.get("message") or "二维码申请失败")

        data = payload.get("data") or {}
        login_url = str(data.get("url") or "")
        qrcode_key = str(data.get("qrcode_key") or "")
        if not login_url or not qrcode_key:
            raise AuthError("二维码接口返回不完整")
        return QrLoginInfo(url=login_url, qrcode_key=qrcode_key)

    def poll_qr_login(self, qrcode_key: str) -> QrPollResult:
        response = self.http_client.get(self.QR_POLL_URL, params={"qrcode_key": qrcode_key}, retries=1)
        payload = response.json()
        if payload.get("code") != 0:
            raise AuthError(payload.get("message") or "登录状态查询失败")

        data = payload.get("data") or {}
        status_code = int(data.get("code", -1))
        message = str(data.get("message") or "")
        if status_code != 0:
            return QrPollResult(code=status_code, message=message)

        cookies = self._cookies_from_response(response)
        cookies.update(self._cookies_from_callback_url(str(data.get("url") or "")))
        refresh_token = str(data.get("refresh_token") or "")
        session = AuthSession(cookies=cookies, refresh_token=refresh_token, updated_at=time.time())
        if not session.sessdata:
            raise AuthError("登录成功，但没有拿到 SESSDATA")

        self.save_session(session)
        return QrPollResult(code=0, message="登录成功", session=session)

    def ensure_latest_session(self, fallback_sessdata: str = "", force: bool = False) -> AuthSession | None:
        with self._lock:
            session = self.load_session()
            if session is None and fallback_sessdata.strip():
                session = AuthSession(cookies={"SESSDATA": fallback_sessdata.strip()})

            if session is None or not session.sessdata:
                return None

            now = time.time()
            if (
                not force
                and self.check_interval_seconds > 0
                and session.last_checked_at
                and now - session.last_checked_at < self.check_interval_seconds
            ):
                return session

            info = self._cookie_info(session)
            if info.get("code") == -101:
                raise AuthError("登录已失效，请重新扫码登录")
            if info.get("code") != 0:
                raise AuthError(info.get("message") or "登录状态检查失败")

            data = info.get("data") or {}
            session.last_checked_at = now
            if not data.get("refresh"):
                self.save_session(session)
                return session

            if not session.refresh_token:
                self.save_session(session)
                raise AuthError("当前登录态缺少 refresh_token，请重新扫码登录")

            timestamp_ms = int(data.get("timestamp") or round(time.time() * 1000))
            return self._refresh_session(session, timestamp_ms)

    def _cookie_info(self, session: AuthSession) -> dict[str, Any]:
        params = {"csrf": session.csrf} if session.csrf else None
        return self.http_client.get_json(
            self.COOKIE_INFO_URL,
            extra_headers={"Cookie": session.cookie_header()},
            params=params,
            retries=1,
        )

    def _refresh_session(self, session: AuthSession, timestamp_ms: int) -> AuthSession:
        refresh_csrf = self._get_refresh_csrf(session, timestamp_ms)
        old_refresh_token = session.refresh_token
        response = self.http_client.post(
            self.COOKIE_REFRESH_URL,
            extra_headers={"Cookie": session.cookie_header()},
            data={
                "csrf": session.csrf,
                "refresh_csrf": refresh_csrf,
                "source": "main_web",
                "refresh_token": old_refresh_token,
            },
            retries=1,
        )
        payload = response.json()
        if payload.get("code") != 0:
            raise AuthError(payload.get("message") or "Cookie 刷新失败")

        cookies = dict(session.cookies)
        cookies.update(self._cookies_from_response(response))
        refresh_token = str((payload.get("data") or {}).get("refresh_token") or old_refresh_token)
        refreshed = AuthSession(
            cookies=cookies,
            refresh_token=refresh_token,
            updated_at=time.time(),
            last_checked_at=time.time(),
        )

        self._confirm_refresh(refreshed, old_refresh_token)
        self.save_session(refreshed)
        self.logger("B 站登录态已自动刷新")
        return refreshed

    def _confirm_refresh(self, session: AuthSession, old_refresh_token: str) -> None:
        response = self.http_client.post(
            self.COOKIE_CONFIRM_URL,
            extra_headers={"Cookie": session.cookie_header()},
            data={"csrf": session.csrf, "refresh_token": old_refresh_token},
            retries=1,
        )
        payload = response.json()
        if payload.get("code") != 0:
            self.logger(f"旧 refresh_token 确认失败：{payload.get('message') or payload.get('code')}")

    def _get_refresh_csrf(self, session: AuthSession, timestamp_ms: int) -> str:
        correspond_path = self._correspond_path(timestamp_ms)
        response = self.http_client.get(
            self.CORRESPOND_URL.format(path=correspond_path),
            extra_headers={"Cookie": session.cookie_header()},
            retries=1,
        )
        match = re.search(r'<div\s+id=["\']1-name["\']>([^<]+)</div>', response.text)
        if not match:
            raise AuthError("没有读取到 refresh_csrf")
        return match.group(1).strip()

    def _correspond_path(self, timestamp_ms: int) -> str:
        public_key = RSA.import_key(self.PUBLIC_KEY)
        cipher = PKCS1_OAEP.new(public_key, hashAlgo=SHA256)
        encrypted = cipher.encrypt(f"refresh_{timestamp_ms}".encode("utf-8"))
        return encrypted.hex()

    @staticmethod
    def _cookies_from_response(response) -> dict[str, str]:
        return {str(name): str(value) for name, value in response.cookies.items() if value}

    @staticmethod
    def _cookies_from_callback_url(url: str) -> dict[str, str]:
        if not url:
            return {}
        query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
        names = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")
        return {name: query[name] for name in names if query.get(name)}
