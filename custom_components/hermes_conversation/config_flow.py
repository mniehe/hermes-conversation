"""Config flow for Hermes Conversation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_MODEL, CONF_NAME, CONF_PROMPT, CONF_TIMEOUT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.typing import VolDictType

from .client import HermesAuthError, HermesClient, HermesConnectionError, HermesError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    CONF_SESSION_TIMEOUT,
    DEFAULT_BASE_URL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MODEL,
    DEFAULT_PROFILE,
    DEFAULT_PROMPT,
    DEFAULT_SESSION_TIMEOUT,
    DOMAIN,
    MAX_SESSION_TIMEOUT,
    MAX_TIMEOUT,
    MIN_SESSION_TIMEOUT,
    MIN_TIMEOUT,
    REQUEST_TIMEOUT,
    SUBENTRY_TYPE_CONVERSATION,
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

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {SUBENTRY_TYPE_CONVERSATION: HermesSubentryFlowHandler}

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
                    title=f"Hermes {data[CONF_PROFILE]}",
                    data=data,
                    subentries=[
                        {
                            "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                            "data": {CONF_PROMPT: DEFAULT_PROMPT},
                            "title": DEFAULT_CONVERSATION_NAME,
                            "unique_id": None,
                        }
                    ],
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


class HermesSubentryFlowHandler(ConfigSubentryFlow):
    """Manage the conversation agents under one Hermes profile."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new conversation agent."""
        return await self._async_step_form(user_input, is_new=True)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing conversation agent."""
        return await self._async_step_form(user_input, is_new=False)

    async def _async_step_form(
        self, user_input: dict[str, Any] | None, *, is_new: bool
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        if user_input is not None:
            title = user_input.pop(CONF_NAME)
            if is_new:
                return self.async_create_entry(title=title, data=user_input)
            return self.async_update_reload_and_abort(
                entry,
                self._get_reconfigure_subentry(),
                title=title,
                data=user_input,
            )

        defaults: Mapping[str, Any] = {}
        if not is_new:
            subentry = self._get_reconfigure_subentry()
            defaults = {CONF_NAME: subentry.title, **subentry.data}
        return self.async_show_form(
            step_id="user" if is_new else "reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                await self._async_schema(entry, defaults.get(CONF_MODEL)), defaults
            ),
        )

    async def _async_schema(
        self, entry: ConfigEntry, current_model: str | None
    ) -> vol.Schema:
        """Build the form, offering whichever models this profile advertises.

        A profile advertises its own name as the model meaning "my default",
        plus any gateway aliases. A stored value that is no longer advertised
        stays selectable so editing an agent never silently changes it.
        """
        try:
            models = await entry.runtime_data.async_list_models()
        except HermesError:
            models = []
        if not models:
            models = [current_model or DEFAULT_MODEL]
        if current_model and current_model not in models:
            models = [current_model, *models]

        schema: VolDictType = {
            vol.Required(CONF_NAME, default=DEFAULT_CONVERSATION_NAME): str
        }

        schema.update(
            {
                vol.Required(CONF_MODEL, default=models[0]): SelectSelector(
                    SelectSelectorConfig(options=models, custom_value=False)
                ),
                vol.Optional(CONF_PROMPT, default=DEFAULT_PROMPT): TemplateSelector(),
                vol.Required(CONF_TIMEOUT, default=REQUEST_TIMEOUT): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_TIMEOUT, max=MAX_TIMEOUT, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SESSION_TIMEOUT, default=DEFAULT_SESSION_TIMEOUT
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SESSION_TIMEOUT,
                        max=MAX_SESSION_TIMEOUT,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
            }
        )
        return vol.Schema(schema)
