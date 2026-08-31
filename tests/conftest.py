"""Shared fixtures for the Hermes Conversation test suite."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ for every test."""
    return


@pytest.fixture(autouse=True)
async def setup_core_components(hass: HomeAssistant):
    """Set up what `conversation` needs before our integration can load."""
    assert await async_setup_component(hass, "homeassistant", {})
