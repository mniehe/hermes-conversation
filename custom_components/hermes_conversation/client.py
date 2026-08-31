"""HTTP client for a Hermes profile's OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import REQUEST_TIMEOUT, VALIDATE_TIMEOUT

_LOGGER = logging.getLogger(__name__)

SSE_DATA_PREFIX = "data: "
SSE_DONE = "[DONE]"


class HermesError(Exception):
    """Base error for Hermes API failures."""


class HermesAuthError(HermesError):
    """Hermes rejected the API key."""


class HermesConnectionError(HermesError):
    """Hermes could not be reached, or answered with an error."""


class HermesClient:
    """Talk to one Hermes profile.

    Hermes serves each profile under ``/p/<profile>/v1`` when
    ``gateway.multiplex_profiles`` is on. With it off the prefix is silently
    ignored and every request lands on the default profile, so callers cannot
    infer the profile from a successful response.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str,
        profile: str,
        api_key: str,
    ) -> None:
        """Initialize the client."""
        self._session = async_get_clientsession(hass)
        self._base_url = base_url.rstrip("/")
        self._profile = profile
        self._api_key = api_key

    @property
    def profile_url(self) -> str:
        """Return the base URL of this client's profile API."""
        return f"{self._base_url}/p/{self._profile}/v1"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def async_list_models(self) -> list[str]:
        """Return the model ids this profile advertises."""
        payload = await self._request("GET", "models", timeout=VALIDATE_TIMEOUT)
        data = payload.get("data")
        if not isinstance(data, list):
            raise HermesConnectionError("Malformed model list")
        return [item["id"] for item in data if isinstance(item, dict) and "id" in item]

    async def async_stream_chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        timeout: int = REQUEST_TIMEOUT,
    ) -> AsyncIterator[str]:
        """Yield reply fragments as Hermes produces them."""
        body = {"model": model, "messages": messages, "stream": True}

        try:
            async with asyncio.timeout(timeout):
                async with self._session.post(
                    f"{self.profile_url}/chat/completions",
                    headers=self._headers,
                    json=body,
                ) as response:
                    if response.status == HTTPStatus.UNAUTHORIZED:
                        raise HermesAuthError("Hermes rejected the API key")
                    response.raise_for_status()

                    async for raw in response.content:
                        if (chunk := _parse_sse_line(raw)) is not None:
                            yield chunk
        except (TimeoutError, aiohttp.ClientError) as err:
            raise HermesConnectionError(str(err) or type(err).__name__) from err

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: int,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout):
                async with self._session.request(
                    method,
                    f"{self.profile_url}/{path}",
                    headers=self._headers,
                    json=json,
                ) as response:
                    if response.status == HTTPStatus.UNAUTHORIZED:
                        raise HermesAuthError("Hermes rejected the API key")
                    response.raise_for_status()
                    result: dict[str, Any] = await response.json()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise HermesConnectionError(str(err) or type(err).__name__) from err

        return result


def _parse_sse_line(raw: bytes) -> str | None:
    """Return the text of one SSE frame, or None when it carries no content.

    A single malformed frame is dropped rather than failing the turn: losing a
    fragment degrades the answer, but aborting loses all of it.
    """
    line = raw.decode("utf-8", errors="replace").strip()
    if not line.startswith(SSE_DATA_PREFIX):
        return None

    payload = line.removeprefix(SSE_DATA_PREFIX).strip()
    if not payload or payload == SSE_DONE:
        return None

    try:
        delta = json.loads(payload)["choices"][0]["delta"]
    except ValueError, KeyError, IndexError, TypeError:
        _LOGGER.debug("Skipping malformed stream frame: %s", payload[:120])
        return None

    content = delta.get("content")
    return content if isinstance(content, str) and content else None
