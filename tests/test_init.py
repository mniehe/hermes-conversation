"""Setup must fail loudly and retry, not pretend to succeed."""

from http import HTTPStatus

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.client import HermesClient
from custom_components.hermes_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    DOMAIN,
)

from .const import API_KEY, BASE_URL, DEFAULT_MODELS, MODELS_URL, PROFILE

ENTRY_DATA = {CONF_BASE_URL: BASE_URL, CONF_PROFILE: PROFILE, CONF_API_KEY: API_KEY}


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"{BASE_URL}#{PROFILE}"
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_stores_client_in_runtime_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = _entry(hass)
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, HermesClient)


async def test_unreachable_gateway_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A dead gateway must raise ConfigEntryNotReady, not silently load."""
    entry = _entry(hass)
    aioclient_mock.get(MODELS_URL, exc=TimeoutError)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_key_triggers_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = _entry(hass)
    aioclient_mock.get(MODELS_URL, status=HTTPStatus.UNAUTHORIZED)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_unload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = _entry(hass)
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    await _setup(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
