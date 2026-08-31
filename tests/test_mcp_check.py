"""Selecting the wrong API in mcp_server removes the boundary silently."""

import pytest
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hermes_conversation.const import DOMAIN
from custom_components.hermes_conversation.llm import (
    ISSUE_UNRESTRICTED_MCP,
    RESTRICTED_API_ID,
)

MCP_SERVER_DOMAIN = "mcp_server"


def _add_mcp_server(hass: HomeAssistant, apis: list[str] | str | None) -> None:
    MockConfigEntry(
        domain=MCP_SERVER_DOMAIN,
        data={} if apis is None else {CONF_LLM_HASS_API: apis},
        title="MCP Server",
    ).add_to_hass(hass)


def _issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_UNRESTRICTED_MCP)


async def test_no_issue_when_mcp_server_absent(hass: HomeAssistant, load_entry) -> None:
    """Not everyone uses MCP; silence is correct until they do."""
    await load_entry()
    assert _issue(hass) is None


async def test_no_issue_when_restricted_api_selected(
    hass: HomeAssistant, load_entry
) -> None:
    _add_mcp_server(hass, [RESTRICTED_API_ID])
    await load_entry()

    assert _issue(hass) is None


@pytest.mark.parametrize(
    "apis", [["assist"], "assist", None, ["assist", "something_else"]]
)
async def test_issue_when_boundary_is_bypassed(
    hass: HomeAssistant, load_entry, apis
) -> None:
    """Plain Assist over MCP means Hermes can unlock doors, with no error."""
    _add_mcp_server(hass, apis)
    await load_entry()

    issue = _issue(hass)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


async def test_issue_clears_when_corrected(hass: HomeAssistant, load_entry) -> None:
    _add_mcp_server(hass, ["assist"])
    entry = await load_entry()
    assert _issue(hass) is not None

    hass.config_entries.async_update_entry(
        hass.config_entries.async_entries(MCP_SERVER_DOMAIN)[0],
        data={CONF_LLM_HASS_API: [RESTRICTED_API_ID]},
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass) is None
