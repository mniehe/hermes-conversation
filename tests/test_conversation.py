"""The conversation entity must surface failures, not paper over them."""

import logging
from http import HTTPStatus
from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.const import CONF_MODEL, CONF_PROMPT, CONF_TIMEOUT
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.client import HermesClient
from custom_components.hermes_conversation.const import (
    CONF_HOUSE_STATE,
    CONF_SESSION_TIMEOUT,
    DOMAIN,
)
from custom_components.hermes_conversation.house import CLOCK_PREFIX, HEADER

from . import sse
from .conftest import EntryLoader, expose
from .const import COMPLETIONS_URL, PROFILE

# The house-state block is on by default; tests that pin the exact system
# message switch it off. The clock is always appended, so they read the
# prompt part through _prompt().
NO_HOUSE = {CONF_HOUSE_STATE: False}
CLOCK_MARKER = f"\n\n{CLOCK_PREFIX}"


def _prompt(messages: list[dict[str, str]]) -> str:
    """Return the system message without the trailing clock line."""
    assert messages[0]["role"] == "system"
    prompt, marker, _ = messages[0]["content"].rpartition(CLOCK_MARKER)
    assert marker
    return prompt


def _reply(text: str) -> bytes:
    return sse.reply(text)


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    subentry_id = next(iter(entry.subentries))
    entity = er.async_get(hass).async_get_entity_id("conversation", DOMAIN, subentry_id)
    assert entity is not None
    return entity


async def _converse(
    hass: HomeAssistant,
    text: str,
    agent_id: str,
    *,
    context: Context | None = None,
    extra_system_prompt: str | None = None,
):
    return await conversation.async_converse(
        hass,
        text,
        None,
        context or Context(),
        agent_id=agent_id,
        extra_system_prompt=extra_system_prompt,
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
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("The kitchen light is on."))

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
    aioclient_mock.post(COMPLETIONS_URL, content=_reply(answer))

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
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("   "))

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
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    body = aioclient_mock.mock_calls[-1][2]
    assert body["model"] == "home-assist"


async def test_configured_prompt_leads_the_transcript(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({**NO_HOUSE, CONF_PROMPT: "You are terse."})
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert _prompt(messages) == "You are terse."
    assert messages[-1]["role"] == "user"


async def test_configured_prompt_is_rendered(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({**NO_HOUSE, CONF_PROMPT: "You are in {{ ha_name }}."})
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert _prompt(messages) == f"You are in {hass.config.location_name}."


async def test_configured_prompt_receives_user_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    user = await hass.auth.async_create_user("Mark")
    entry = await load_entry(
        {**NO_HOUSE, CONF_PROMPT: "You are helping {{ user_name }}."}
    )
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(
        hass,
        "hello",
        _entity_id(hass, entry),
        context=Context(user_id=user.id),
    )

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert _prompt(messages) == "You are helping Mark."


async def test_extra_system_prompt_is_sent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(NO_HOUSE)
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(
        hass,
        "hello",
        _entity_id(hass, entry),
        extra_system_prompt="This request came from the kitchen satellite.",
    )

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert _prompt(messages) == "This request came from the kitchen satellite."


async def test_no_prompt_still_sends_the_clock(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(NO_HOUSE)
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith(CLOCK_PREFIX)
    assert HEADER not in messages[0]["content"]


async def test_configured_timeout_is_applied(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """A short timeout must actually bound the request."""
    entry = await load_entry({CONF_TIMEOUT: 10})
    captured: list[int] = []
    original = HermesClient.async_stream_chat

    def _spy(self, model, messages, timeout=None, session_id=None):
        captured.append(timeout)
        return original(self, model, messages, timeout=timeout, session_id=session_id)

    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))
    with patch.object(HermesClient, "async_stream_chat", _spy):
        await _converse(hass, "hello", _entity_id(hass, entry))

    assert captured == [10]


async def test_failures_name_the_profile_in_logs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry, caplog
) -> None:
    """With several profiles configured, a bare failure message is useless."""
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, exc=TimeoutError)

    await _converse(hass, "hello", _entity_id(hass, entry))

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(PROFILE in r.getMessage() for r in warnings)


async def test_prompt_receives_satellite(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """The agent must know which satellite spoke so it can answer there."""
    area = ar.async_get(hass).async_create("Kitchen")
    registry = er.async_get(hass)
    satellite = registry.async_get_or_create(
        "assist_satellite", "test", "kitchen-sat", suggested_object_id="kitchen"
    )
    registry.async_update_entity(satellite.entity_id, area_id=area.id)
    hass.states.async_set(
        satellite.entity_id, "idle", {"friendly_name": "Kitchen Satellite"}
    )
    entry = await load_entry(
        {
            **NO_HOUSE,
            CONF_PROMPT: "{{ satellite_id }} / {{ satellite_name }} / {{ area_name }}",
        }
    )
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await conversation.async_converse(
        hass,
        "hello",
        None,
        Context(),
        agent_id=_entity_id(hass, entry),
        satellite_id=satellite.entity_id,
    )

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert _prompt(messages) == "assist_satellite.kitchen / Kitchen Satellite / Kitchen"


async def test_prompt_satellite_area_comes_from_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    area = ar.async_get(hass).async_create("Office")
    satellite_entry = MockConfigEntry(domain="test")
    satellite_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=satellite_entry.entry_id,
        identifiers={("test", "office-sat")},
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    satellite = er.async_get(hass).async_get_or_create(
        "assist_satellite",
        "test",
        "office-sat",
        suggested_object_id="office",
        device_id=device.id,
    )
    entry = await load_entry({**NO_HOUSE, CONF_PROMPT: "{{ area_name }}"})
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await conversation.async_converse(
        hass,
        "hello",
        None,
        Context(),
        agent_id=_entity_id(hass, entry),
        satellite_id=satellite.entity_id,
    )

    assert _prompt(aioclient_mock.mock_calls[-1][2]["messages"]) == "Office"


async def test_prompt_without_satellite_renders_blank(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(
        {
            **NO_HOUSE,
            CONF_PROMPT: "[{{ satellite_id }}][{{ satellite_name }}][{{ area_name }}]",
        }
    )
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    assert _prompt(aioclient_mock.mock_calls[-1][2]["messages"]) == "[None][None][None]"


async def test_house_state_trails_the_prompt(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    expose(hass, "light.kitchen", "Kitchen Lights", "on")
    entry = await load_entry({CONF_PROMPT: "You are terse."})
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    content = aioclient_mock.mock_calls[-1][2]["messages"][0]["content"]
    assert content.startswith(f"You are terse.\n\n{CLOCK_PREFIX}")
    assert content.endswith(f"\n{HEADER}\nNo area:\n  Kitchen Lights: on")


async def test_house_state_without_prompt_stands_alone(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith(CLOCK_PREFIX)
    assert HEADER in messages[0]["content"]


async def test_house_state_changes_only_the_tail(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    load_entry: EntryLoader,
    freezer,
) -> None:
    """The block must stay behind the prompt: whatever precedes it is cacheable."""
    expose(hass, "light.kitchen", "Kitchen Lights", "on")
    entry = await load_entry({CONF_PROMPT: "You are the house at {{ ha_name }}."})
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))
    first = aioclient_mock.mock_calls[-1][2]["messages"][0]["content"]

    freezer.tick(90)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen Lights"})
    await _converse(hass, "hello again", _entity_id(hass, entry))
    second = aioclient_mock.mock_calls[-1][2]["messages"][0]["content"]

    marker = "\n\nCurrent time: "
    assert first.split(marker)[0] == second.split(marker)[0]
    assert first != second
    assert second.endswith("Kitchen Lights: off")


async def test_house_state_follows_the_pipeline_prompt(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: "You are terse."})
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(
        hass, "hello", _entity_id(hass, entry), extra_system_prompt="From the kitchen."
    )

    content = aioclient_mock.mock_calls[-1][2]["messages"][0]["content"]
    assert content.startswith(f"You are terse.\nFrom the kitchen.\n\n{CLOCK_PREFIX}")


async def test_house_state_is_sent_on_the_replay_path(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: "You are terse.", CONF_SESSION_TIMEOUT: 0})
    aioclient_mock.post(COMPLETIONS_URL, content=_reply("ok"))

    await _converse(hass, "hello", _entity_id(hass, entry))

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert messages[0]["role"] == "system"
    assert "\n\nCurrent time: " in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "hello"}
