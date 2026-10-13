from __future__ import annotations

from urllib.parse import quote, unquote


def normalize_cookie_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return quote(unquote(text), safe="")
