"""HTTP client for a Hermes profile's OpenAI-compatible API."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import REQUEST_TIMEOUT, VALIDATE_TIMEOUT


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

    async def async_chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        timeout: int = REQUEST_TIMEOUT,
    ) -> str:
        """Send a chat completion and return the assistant's reply."""
        payload = await self._request(
            "POST",
            "chat/completions",
            json={"model": model, "messages": messages, "stream": False},
            timeout=timeout,
        )
        try:
            answer = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as err:
            raise HermesConnectionError("Malformed completion response") from err

        if not isinstance(answer, str) or not answer.strip():
            raise HermesConnectionError("Hermes returned an empty response")
        return answer.strip()

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
