"""The conversation entity must surface failures, not paper over them."""

from http import HTTPStatus

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    DOMAIN,
)

from .const import (
    API_KEY,
    BASE_URL,
    COMPLETIONS_URL,
    DEFAULT_MODELS,
    MODELS_URL,
    PROFILE,
)

ENTRY_DATA = {CONF_BASE_URL: BASE_URL, CONF_PROFILE: PROFILE, CONF_API_KEY: API_KEY}


def _reply(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


async def _load(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=f"{BASE_URL}#{PROFILE}",
        title=f"Hermes {PROFILE}",
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, entry.entry_id
    )
    assert entity is not None
    return entity


async def _converse(hass: HomeAssistant, text: str, agent_id: str):
    return await conversation.async_converse(
        hass, text, None, Context(), agent_id=agent_id
    )


async def test_unique_id_is_per_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two entries must not collide on a domain-wide unique id."""
    entry = await _load(hass, aioclient_mock)
    entity = er.async_get(hass).async_get(_entity_id(hass, entry))

    assert entity is not None
    assert entity.unique_id == entry.entry_id


async def test_entity_has_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _load(hass, aioclient_mock)
    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})

    assert device is not None
    assert device.name == f"Hermes {PROFILE}"


async def test_successful_reply(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _load(hass, aioclient_mock)
    aioclient_mock.post(COMPLETIONS_URL, json=_reply("The kitchen light is on."))

    result = await _converse(hass, "is the light on", _entity_id(hass, entry))

    assert result.response.speech["plain"]["speech"] == "The kitchen light is on."


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Which room did you mean?", True),
        ("The light is on.", False),
        # Core also recognises the Chinese question mark; the hand-rolled
        # endswith("?") check this replaced did not.
        ("\u4f60\u597d\uff1f", True),
    ],
)
async def test_continue_conversation_follows_core(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    answer: str,
    expected: bool,
) -> None:
    """Follow-up detection is core's, via the chat log, not our own rule."""
    entry = await _load(hass, aioclient_mock)
    aioclient_mock.post(COMPLETIONS_URL, json=_reply(answer))

    result = await _converse(hass, "hello", _entity_id(hass, entry))

    assert result.continue_conversation is expected


async def test_connection_failure_is_an_error_response(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A failure must reach HA's error surface, not become a cheerful string."""
    entry = await _load(hass, aioclient_mock)
    aioclient_mock.post(COMPLETIONS_URL, exc=TimeoutError)

    result = await _converse(hass, "hello", _entity_id(hass, entry))

    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert result.response.error_code is intent.IntentResponseErrorCode.UNKNOWN


async def test_empty_reply_is_an_error_response(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _load(hass, aioclient_mock)
    aioclient_mock.post(COMPLETIONS_URL, json=_reply("   "))

    result = await _converse(hass, "hello", _entity_id(hass, entry))

    assert result.response.response_type is intent.IntentResponseType.ERROR


async def test_runtime_401_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _load(hass, aioclient_mock)
    aioclient_mock.post(COMPLETIONS_URL, status=HTTPStatus.UNAUTHORIZED)

    result = await _converse(hass, "hello", _entity_id(hass, entry))
    await hass.async_block_till_done()

    assert result.response.response_type is intent.IntentResponseType.ERROR

    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
