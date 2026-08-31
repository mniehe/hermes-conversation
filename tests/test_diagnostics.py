"""Diagnostics get pasted into public issue threads. No secrets, ever."""

import logging

from homeassistant.components import conversation
from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_LLM_HASS_API, CONF_MODEL, CONF_PROMPT
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    DOMAIN,
)
from custom_components.hermes_conversation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hermes_conversation.llm import RESTRICTED_API_ID

from .const import API_KEY, BASE_URL, COMPLETIONS_URL, PROFILE


async def test_api_key_is_redacted(hass: HomeAssistant, load_entry) -> None:
    entry = await load_entry()
    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"][CONF_API_KEY] == REDACTED
    assert API_KEY not in str(result)


async def test_useful_context_is_present(hass: HomeAssistant, load_entry) -> None:
    """Redaction must not strip what makes the report worth reading."""
    entry = await load_entry({CONF_MODEL: "home-assist", CONF_PROMPT: "Be terse."})
    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"][CONF_BASE_URL] == BASE_URL
    assert result["entry"]["data"][CONF_PROFILE] == PROFILE
    assert result["agents"][0]["data"][CONF_MODEL] == "home-assist"


async def test_reports_whether_the_boundary_is_active(
    hass: HomeAssistant, load_entry
) -> None:
    """The first question on any support thread."""
    MockConfigEntry(
        domain="mcp_server", data={CONF_LLM_HASS_API: [RESTRICTED_API_ID]}
    ).add_to_hass(hass)
    entry = await load_entry()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["mcp_server"]["restricted_api_selected"] is True


async def test_reports_any_unrestricted_mcp_entry(
    hass: HomeAssistant, load_entry
) -> None:
    """A safe first entry must not hide a second unrestricted endpoint."""
    MockConfigEntry(
        domain="mcp_server", data={CONF_LLM_HASS_API: [RESTRICTED_API_ID]}
    ).add_to_hass(hass)
    MockConfigEntry(
        domain="mcp_server", data={CONF_LLM_HASS_API: ["assist"]}
    ).add_to_hass(hass)
    entry = await load_entry()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["mcp_server"]["restricted_api_selected"] is False
    assert result["mcp_server"]["entries"] == [
        {"selected_apis": [RESTRICTED_API_ID], "restricted": True},
        {"selected_apis": ["assist"], "restricted": False},
    ]


async def test_api_key_never_reaches_the_logs(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    load_entry,
    caplog,
) -> None:
    """Failures are the moment a naive implementation dumps the request."""
    caplog.set_level(logging.DEBUG)
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, exc=TimeoutError)

    entity_id = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, next(iter(entry.subentries))
    )
    await conversation.async_converse(
        hass, "hello", None, Context(), agent_id=entity_id
    )

    assert API_KEY not in caplog.text
    assert "Bearer" not in caplog.text
