"""Diagnostics for Hermes Conversation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant

from . import HermesConfigEntry
from .client import HermesError
from .const import CONF_API_KEY, CONF_HERMES_USER, NO_USER
from .llm import MCP_SERVER_DOMAIN, mcp_entry_is_restricted
from .policy import DATA_GROUP, GROUP_ID, forbidden_covers

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
        "policy": await _async_policy_info(hass, entry),
    }


async def _async_policy_info(
    hass: HomeAssistant, entry: HermesConfigEntry
) -> dict[str, Any]:
    """Report whether the user-group policy is actually in force for this entry."""
    group = hass.data[DATA_GROUP]
    user_id = entry.options.get(CONF_HERMES_USER, NO_USER)
    user = await hass.auth.async_get_user(user_id) if user_id != NO_USER else None
    return {
        "supported": group.supported,
        "group_present": group.get() is not None,
        "user_managed": user_id != NO_USER,
        "user_exists": user is not None,
        "user_is_admin": user is not None and user.is_admin,
        "user_in_group": user is not None and [g.id for g in user.groups] == [GROUP_ID],
        "forbidden_covers": forbidden_covers(hass),
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

    entry_info: list[dict[str, Any]] = []
    for entry in entries:
        selected = entry.data.get(CONF_LLM_HASS_API) or []
        if isinstance(selected, str):
            selected = [selected]
        entry_info.append(
            {
                "selected_apis": list(selected),
                "restricted": mcp_entry_is_restricted(entry),
            }
        )

    return {
        "configured": True,
        "selected_apis": [api for info in entry_info for api in info["selected_apis"]],
        "restricted_api_selected": all(info["restricted"] for info in entry_info),
        "entries": entry_info,
    }
