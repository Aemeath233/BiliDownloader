from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

SHARE_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv", "www.b23.tv"})
_SECRET_VALUE = re.compile(
    r"(?i)((?:SESSDATA|bili_jct|refresh_token|access_token|qrcode_key|csrf|password|token|sign|auth_key)"
    r"[\"']?\s*[=:]\s*[\"']?)([^\s;,&\"'<>]+)"
)
_AUTH_HEADER = re.compile(r"(?im)((?:proxy-)?authorization\s*:\s*)[^\r\n]+")


def is_account_host(host: str) -> bool:
    hostname = host.lower().rstrip(".")
    return hostname == "bilibili.com" or hostname.endswith(".bilibili.com")


def normalize_share_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or host not in SHARE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("只支持 bilibili.com 和 b23.tv 的视频链接")
    return urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))


def normalize_media_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("媒体地址必须使用 HTTP 或 HTTPS，且不能包含登录信息")
    if parsed.scheme == "http":
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        netloc = host if parsed.port in {None, 80} else f"{host}:{parsed.port}"
        return urlunsplit(("https", netloc, parsed.path, parsed.query, parsed.fragment))
    return url


def redact_sensitive(message: str) -> str:
    return _SECRET_VALUE.sub(r"\1<已隐藏>", _AUTH_HEADER.sub(r"\1<已隐藏>", str(message)))
