"""Set up Hermes Conversation."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .client import HermesAuthError, HermesClient, HermesConnectionError
from .const import CONF_API_KEY, CONF_BASE_URL, CONF_PROFILE
from .llm import async_check_mcp_server, async_register_api

PLATFORMS = (Platform.CONVERSATION,)

type HermesConfigEntry = ConfigEntry[HermesClient]


async def async_setup_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Set up Hermes Conversation from a config entry."""
    client = HermesClient(
        hass,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_PROFILE],
        entry.data[CONF_API_KEY],
    )

    try:
        await client.async_list_models()
    except HermesAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except HermesConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = client
    entry.async_on_unload(async_register_api(hass))
    async_check_mcp_server(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Unload a Hermes Conversation config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
