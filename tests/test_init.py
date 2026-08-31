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

from .conftest import PROBE_URL
from .const import API_KEY, BASE_URL, DEFAULT_MODELS, MODELS_URL, PROFILE

ENTRY_DATA = {CONF_BASE_URL: BASE_URL, CONF_PROFILE: PROFILE, CONF_API_KEY: API_KEY}


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"{BASE_URL}#{PROFILE}"
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker | None = None,
) -> None:
    if aioclient_mock is not None:
        aioclient_mock.get(PROBE_URL, status=HTTPStatus.NOT_FOUND)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_stores_client_in_runtime_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = _entry(hass)
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    await _setup(hass, entry, aioclient_mock)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, HermesClient)


async def test_multiple_profile_entries_load(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The integration-wide LLM API must not belong to one profile entry."""
    first = _entry(hass)
    second_profile = "wife"
    second = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_PROFILE: second_profile},
        unique_id=f"{BASE_URL}#{second_profile}",
    )
    second.add_to_hass(hass)
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    aioclient_mock.get(f"{BASE_URL}/p/{second_profile}/v1/models", json=DEFAULT_MODELS)

    # Loading the integration sets up all of its pending entries.
    await _setup(hass, first, aioclient_mock)

    assert first.state is ConfigEntryState.LOADED
    assert second.state is ConfigEntryState.LOADED


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
    await _setup(hass, entry, aioclient_mock)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
