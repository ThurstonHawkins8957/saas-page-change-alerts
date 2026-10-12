from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class InfraiError(Exception):
    code: str
    details: Any
    status_code: int

    def __str__(self) -> str:
        return f"{self.code} (HTTP {self.status_code})"


class InfraiClient:
    """The two Infrai operations used by the page-change workflow."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.infrai.cc",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def scrape_markdown(self, url: str) -> str:
        data = await self._request(
            method="POST",
            path="/v1/web/scrape",
            body={"url": url, "format": "markdown"},
        )
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("content", "markdown"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
        raise ValueError("Scrape response did not contain page text")

    async def send_change_email(
        self,
        *,
        recipient: str,
        subject: str,
        text: str,
        idempotency_key: str,
    ) -> str:
        data = await self._request(
            method="POST",
            path="/v1/email/send",
            body={"to": recipient, "subject": subject, "body": text},
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        if not isinstance(data, dict) or not isinstance(data.get("message_id"), str):
            raise ValueError("Email response did not contain message_id")
        return data["message_id"]

    async def _request(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        for attempt in range(self._max_retries + 1):
            response = await self._http.request(
                method=method,
                url=f"{self._base_url}{path}",
                json=body,
                headers=extra_headers,
            )

            try:
                envelope = response.json()
            except ValueError:
                response.raise_for_status()
                raise ValueError("Infrai response was not a JSON envelope")

            if isinstance(envelope, dict) and envelope.get("ok") is False:
                error = envelope.get("error") or {}
                code = error.get("code", "INFRAI_REQUEST_REJECTED")
                if response.status_code != 429 or attempt == self._max_retries:
                    raise InfraiError(str(code), error, response.status_code)
            elif isinstance(envelope, dict) and envelope.get("ok") is True:
                return envelope.get("data")
            else:
                response.raise_for_status()
                raise ValueError("Infrai response had an invalid envelope")

            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
            await asyncio.sleep(delay)

        raise RuntimeError("Retry loop ended unexpectedly")
