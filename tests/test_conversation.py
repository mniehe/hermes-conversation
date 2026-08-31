"""The conversation entity must surface failures, not paper over them."""

from http import HTTPStatus
from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.const import CONF_MODEL, CONF_PROMPT, CONF_TIMEOUT
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.client import HermesClient
from custom_components.hermes_conversation.const import DOMAIN

from .conftest import EntryLoader
from .const import COMPLETIONS_URL


def _reply(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    subentry_id = next(iter(entry.subentries))
    entity = er.async_get(hass).async_get_entity_id("conversation", DOMAIN, subentry_id)
    assert entity is not None
    return entity


async def _converse(hass: HomeAssistant, text: str, agent_id: str):
    return await conversation.async_converse(
        hass, text, None, Context(), agent_id=agent_id
    )


async def test_unique_id_is_per_agent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """Agents must not collide on a domain-wide unique id."""
    entry = await load_entry()
    entity = er.async_get(hass).async_get(_entity_id(hass, entry))

    assert entity is not None
    assert entity.unique_id == next(iter(entry.subentries))


async def test_entity_has_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(agent_title="Bedtime")
    subentry_id = next(iter(entry.subentries))
    device = dr.async_get(hass).async_get_device({(DOMAIN, subentry_id)})

    assert device is not None
    assert device.name == "Bedtime"


async def test_successful_reply(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
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
    load_entry: EntryLoader,
    answer: str,
    expected: bool,
) -> None:
    """Follow-up detection is core's, via the chat log, not our own rule."""
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, json=_reply(answer))

    result = await _converse(hass, "hello", _entity_id(hass, entry))

    assert result.continue_conversation is expected


async def test_connection_failure_is_an_error_response(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """A failure must reach HA's error surface, not become a cheerful string."""
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, exc=TimeoutError)

    result = await _converse(hass, "hello", _entity_id(hass, entry))

    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert result.response.error_code is intent.IntentResponseErrorCode.UNKNOWN


async def test_empty_reply_is_an_error_response(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, json=_reply("   "))

    result = await _converse(hass, "hello", _entity_id(hass, entry))

    assert result.response.response_type is intent.IntentResponseType.ERROR


async def test_runtime_401_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, status=HTTPStatus.UNAUTHORIZED)

    result = await _converse(hass, "hello", _entity_id(hass, entry))
    await hass.async_block_till_done()

    assert result.response.response_type is intent.IntentResponseType.ERROR

    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_configured_model_is_sent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_MODEL: "home-assist"})
    aioclient_mock.post(COMPLETIONS_URL, json=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    body = aioclient_mock.mock_calls[-1][2]
    assert body["model"] == "home-assist"


async def test_configured_prompt_leads_the_transcript(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: "You are terse."})
    aioclient_mock.post(COMPLETIONS_URL, json=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert messages[0] == {"role": "system", "content": "You are terse."}
    assert messages[-1]["role"] == "user"


async def test_no_prompt_sends_no_system_message(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, json=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert not [msg for msg in messages if msg["role"] == "system"]


async def test_configured_timeout_is_applied(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """A short timeout must actually bound the request."""
    entry = await load_entry({CONF_TIMEOUT: 10})
    captured: list[int] = []
    original = HermesClient._request

    async def _spy(self, method, path, *, timeout, json=None):
        captured.append(timeout)
        return await original(self, method, path, timeout=timeout, json=json)

    aioclient_mock.post(COMPLETIONS_URL, json=_reply("ok"))
    with patch.object(HermesClient, "_request", _spy):
        await _converse(hass, "hello", _entity_id(hass, entry))

    assert captured == [10]
