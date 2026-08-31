"""Config flow for Hermes Conversation."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_API_KEY, CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


class HermesConversationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hermes Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            api_key = user_input[CONF_API_KEY]
            headers = {"Authorization": f"Bearer {api_key}"}

            try:
                async with asyncio.timeout(10):
                    async with async_get_clientsession(self.hass).get(
                        f"{base_url}/models", headers=headers
                    ) as response:
                        if response.status == HTTPStatus.UNAUTHORIZED:
                            errors["base"] = "invalid_auth"
                        else:
                            response.raise_for_status()
            except TimeoutError, aiohttp.ClientError:
                errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Hermes home-assist",
                    data={CONF_BASE_URL: base_url, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )
