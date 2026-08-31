"""Shared fixtures for the Hermes Conversation test suite."""

from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)

from .const import API_KEY, BASE_URL, DEFAULT_MODELS, MODELS_URL, PROFILE

ENTRY_DATA = {CONF_BASE_URL: BASE_URL, CONF_PROFILE: PROFILE, CONF_API_KEY: API_KEY}

type EntryLoader = Callable[..., Coroutine[Any, Any, MockConfigEntry]]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ for every test."""
    return


@pytest.fixture(autouse=True)
async def setup_core_components(hass: HomeAssistant):
    """Set up what `conversation` needs before our integration can load."""
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def load_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> EntryLoader:
    """Return a factory that adds and sets up a config entry with one agent."""

    async def _load(
        agent_options: dict[str, Any] | None = None,
        agent_title: str = "Hermes",
    ) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=ENTRY_DATA,
            unique_id=f"{BASE_URL}#{PROFILE}",
            title=f"Hermes {PROFILE}",
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONVERSATION,
                    data=agent_options or {},
                    title=agent_title,
                    unique_id=None,
                )
            ],
        )
        entry.add_to_hass(hass)
        aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _load
