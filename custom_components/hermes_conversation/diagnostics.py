"""Diagnostics for Hermes Conversation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant

from . import HermesConfigEntry
from .client import HermesError
from .const import CONF_API_KEY
from .llm import MCP_SERVER_DOMAIN, RESTRICTED_API_ID

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HermesConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(entry.data, TO_REDACT),
        },
        "agents": [
            {
                "title": subentry.title,
                "type": subentry.subentry_type,
                "data": async_redact_data(subentry.data, TO_REDACT),
            }
            for subentry in entry.subentries.values()
        ],
        "hermes": await _async_gateway_info(entry),
        "mcp_server": _mcp_server_info(hass),
    }


async def _async_gateway_info(entry: HermesConfigEntry) -> dict[str, Any]:
    """Report what the gateway says about itself, or why it cannot be asked."""
    try:
        return {
            "reachable": True,
            "models": await entry.runtime_data.async_list_models(),
        }
    except HermesError as err:
        return {"reachable": False, "error": type(err).__name__, "detail": str(err)}


def _mcp_server_info(hass: HomeAssistant) -> dict[str, Any]:
    """Report whether the capability boundary is actually in force.

    Home Assistant's MCP server serves whichever API it was configured with. If
    that is not ours, nothing errors and the agent simply has unrestricted
    access — so this is the first thing worth knowing on a support thread.
    """
    entries = hass.config_entries.async_entries(MCP_SERVER_DOMAIN)
    if not entries:
        return {"configured": False, "restricted_api_selected": False}

    selected = entries[0].data.get(CONF_LLM_HASS_API) or []
    if isinstance(selected, str):
        selected = [selected]

    return {
        "configured": True,
        "selected_apis": list(selected),
        "restricted_api_selected": RESTRICTED_API_ID in selected,
    }
