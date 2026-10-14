"""Minimal Bitrix24 REST client working through an incoming webhook."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

# Errors Bitrix returns when the portal throttles requests (~2 req/s per portal).
_RETRYABLE_ERRORS = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"}


class BitrixError(RuntimeError):
    def __init__(self, method: str, code: str, description: str = "") -> None:
        self.method = method
        self.code = code
        self.description = description
        super().__init__(f"{method}: {code} {description}".strip())


class BitrixClient:
    """Calls REST methods as ``POST {webhook}{method}.json`` with a JSON body."""

    def __init__(
        self,
        webhook_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self.webhook_url = webhook_url.rstrip("/") + "/"
        self._http = httpx.Client(transport=transport, timeout=timeout)
        self._max_retries = max_retries
        self._sleep = sleep

    @property
    def portal(self) -> str:
        return httpx.URL(self.webhook_url).host

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BitrixClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call_raw(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the full response body (``result``, ``total``, ``next``...)."""
        url = f"{self.webhook_url}{method}.json"
        for attempt in range(self._max_retries + 1):
            response = self._http.post(url, json=params or {})
            try:
                body = response.json()
            except ValueError as exc:
                response.raise_for_status()
                raise BitrixError(method, "INVALID_RESPONSE", response.text[:200]) from exc

            if isinstance(body, dict) and "error" in body:
                code = str(body["error"])
                if code in _RETRYABLE_ERRORS and attempt < self._max_retries:
                    self._sleep(0.5 * 2**attempt)
                    continue
                raise BitrixError(method, code, str(body.get("error_description", "")))
            response.raise_for_status()
            return body
        raise AssertionError("unreachable")

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self.call_raw(method, params)["result"]

    def iter_list(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        items_key: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate over a paginated list method, following ``next`` offsets.

        ``items_key`` is for methods wrapping the list in an object, e.g.
        ``crm.type.list`` returns ``{"result": {"types": [...]}}``.
        """
        params = dict(params or {})
        while True:
            body = self.call_raw(method, params)
            result = body.get("result")
            items = result.get(items_key, []) if items_key else result
            yield from items or []
            next_start = body.get("next")
            if next_start is None:
                return
            params["start"] = next_start
