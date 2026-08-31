"""Config flow for Hermes Conversation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import HermesAuthError, HermesClient, HermesConnectionError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    DEFAULT_BASE_URL,
    DEFAULT_PROFILE,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_PROFILE, default=DEFAULT_PROFILE): TextSelector(),
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def _async_validate(hass: Any, data: Mapping[str, Any]) -> str | None:
    """Return an error key, or None when the credentials work."""
    client = HermesClient(
        hass,
        data[CONF_BASE_URL],
        data[CONF_PROFILE],
        data[CONF_API_KEY],
    )
    try:
        await client.async_list_models()
    except HermesAuthError:
        return "invalid_auth"
    except HermesConnectionError:
        return "cannot_connect"
    return None


class HermesConversationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hermes Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {**user_input, CONF_BASE_URL: user_input[CONF_BASE_URL].rstrip("/")}

            if error := await _async_validate(self.hass, data):
                errors["base"] = error
            else:
                await self.async_set_unique_id(
                    f"{data[CONF_BASE_URL]}#{data[CONF_PROFILE]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Hermes {data[CONF_PROFILE]}", data=data
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the gateway, profile or key without re-adding the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {**user_input, CONF_BASE_URL: user_input[CONF_BASE_URL].rstrip("/")}

            if error := await _async_validate(self.hass, data):
                errors["base"] = error
            else:
                unique_id = f"{data[CONF_BASE_URL]}#{data[CONF_PROFILE]}"
                await self.async_set_unique_id(unique_id)

                # The profile may legitimately change here, so the unique id
                # changes with it. Only a collision with a *different* entry
                # is a problem.
                clash = self.hass.config_entries.async_entry_for_domain_unique_id(
                    self.handler, unique_id
                )
                if clash is not None and clash.entry_id != entry.entry_id:
                    return self.async_abort(reason="already_configured")

                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, entry.data
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a rotated or revoked API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a replacement API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {**entry.data, **user_input}

            if error := await _async_validate(self.hass, data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={CONF_PROFILE: entry.data[CONF_PROFILE]},
            errors=errors,
        )
