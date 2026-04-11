"""
Минимальный конфиг и helper-обвязка для Hostify API.

API-ключ читается из переменной окружения HOSTIFY_API_KEY.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HOSTIFY_BASE_URL = "https://api-rms.hostify.com/"
class HostifyHttpError(Exception):
    """Raised when the Hostify API returns a non-2xx response or network error."""


def hostify_api_key() -> str:
    """
    Resolve the Hostify API key at call time.

    This keeps CLI/web helpers compatible with environments that load `.env`
    after module import.
    """
    return os.environ.get("HOSTIFY_API_KEY", "")


def hostify_headers(extra: dict | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": hostify_api_key(),
    }
    if extra:
        headers.update(extra)
    return headers


def hostify_url(path: str, params: dict | None = None) -> str:
    path = str(path or "").lstrip("/")
    url = HOSTIFY_BASE_URL.rstrip("/") + "/" + path
    if params:
        url += "?" + urlencode(params, doseq=True)
    return url


def hostify_request(method: str, path: str, *, params: dict | None = None,
                    payload: dict | None = None, timeout: int = 30) -> dict:
    """
    Базовый JSON request helper без внешних зависимостей.
    Возвращает распарсенный JSON-ответ Hostify.

    Raises:
        HostifyHttpError: on HTTP error, network failure, or invalid JSON response.
    """
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = Request(
        hostify_url(path, params=params),
        data=data,
        headers=hostify_headers(),
        method=method.upper(),
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise HostifyHttpError(
            f"HTTP {e.code} {e.reason} for {method.upper()} /{path}: {body[:300]}"
        ) from e
    except URLError as e:
        raise HostifyHttpError(f"Network error for {method.upper()} /{path}: {e.reason}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise HostifyHttpError(
            f"Invalid JSON from Hostify ({method.upper()} /{path}): {e}. "
            f"Response starts with: {raw[:200]!r}"
        ) from e


def hostify_get(path: str, *, params: dict | None = None, timeout: int = 30) -> dict:
    return hostify_request("GET", path, params=params, timeout=timeout)


def hostify_post(path: str, *, payload: dict | None = None, params: dict | None = None,
                 timeout: int = 30) -> dict:
    return hostify_request("POST", path, params=params, payload=payload, timeout=timeout)


def hostify_put(path: str, *, payload: dict | None = None, params: dict | None = None,
                timeout: int = 30) -> dict:
    return hostify_request("PUT", path, params=params, payload=payload, timeout=timeout)


def hostify_delete(path: str, *, params: dict | None = None, timeout: int = 30) -> dict:
    return hostify_request("DELETE", path, params=params, timeout=timeout)
