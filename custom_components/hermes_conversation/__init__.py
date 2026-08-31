"""Set up Hermes Conversation."""

from __future__ import annotations

import logging

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType

from .client import HermesAuthError, HermesClient, HermesConnectionError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    DOMAIN,
    ISSUE_PROFILE_IGNORED,
)
from .llm import MCP_SERVER_DOMAIN, async_check_mcp_server, async_register_api

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (Platform.CONVERSATION,)

type HermesConfigEntry = ConfigEntry[HermesClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide resources."""
    async_register_api(hass)
    # Versions before per-entry routing issues used one global issue id.
    ir.async_delete_issue(hass, DOMAIN, ISSUE_PROFILE_IGNORED)

    @callback
    def _config_entry_changed(change: ConfigEntryChange, entry: ConfigEntry) -> None:
        if entry.domain == MCP_SERVER_DOMAIN:
            async_check_mcp_server(hass)
        elif entry.domain == DOMAIN and change is ConfigEntryChange.REMOVED:
            ir.async_delete_issue(
                hass, DOMAIN, f"{ISSUE_PROFILE_IGNORED}_{entry.entry_id}"
            )

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _config_entry_changed)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Set up Hermes Conversation from a config entry."""
    client = HermesClient(
        hass,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_PROFILE],
        entry.data[CONF_API_KEY],
    )

    try:
        models = await client.async_list_models()
    except HermesAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except HermesConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    _LOGGER.debug(
        "Connected to Hermes profile %s; %d model(s) advertised",
        entry.data[CONF_PROFILE],
        len(models),
    )

    entry.runtime_data = client
    async_check_mcp_server(hass)
    await _async_check_profile_routing(hass, client, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Unload a Hermes Conversation config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_check_profile_routing(
    hass: HomeAssistant, client: HermesClient, entry: HermesConfigEntry
) -> None:
    """Warn when Hermes is ignoring the profile prefix.

    With ``gateway.multiplex_profiles`` off, Hermes drops the ``/p/<profile>/``
    prefix and serves the default profile instead. Nothing fails, so without
    this the only symptom is an agent answering as the wrong persona.
    """
    issue_id = f"{ISSUE_PROFILE_IGNORED}_{entry.entry_id}"
    if await client.async_profile_prefix_honoured() is not False:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PROFILE_IGNORED,
        translation_placeholders={"profile": entry.data[CONF_PROFILE]},
    )
