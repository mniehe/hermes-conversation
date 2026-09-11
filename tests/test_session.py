"""Turns from one satellite share a Hermes session until it goes idle."""

from http import HTTPStatus
from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.const import CONF_PROMPT
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import (
    CONF_HOUSE_STATE,
    CONF_SESSION_TIMEOUT,
    DOMAIN,
    RESET_REPLY,
    SESSION_ID_HEADER,
)
from custom_components.hermes_conversation.house import CLOCK_PREFIX
from custom_components.hermes_conversation.session import (
    SESSION_ID_PREFIX,
    SessionTracker,
)

from . import sse
from .conftest import EntryLoader, expose
from .const import COMPLETIONS_URL

KITCHEN = "assist_satellite.kitchen"
OFFICE = "assist_satellite.office"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_tracker_reuses_session_within_timeout() -> None:
    clock = FakeClock()
    tracker = SessionTracker(60, clock)

    first = tracker.session_for(KITCHEN)
    clock.now += 59
    assert tracker.session_for(KITCHEN) == first


def test_tracker_mints_session_after_idle() -> None:
    clock = FakeClock()
    tracker = SessionTracker(60, clock)

    first = tracker.session_for(KITCHEN)
    clock.now += 60
    assert tracker.session_for(KITCHEN) != first


def test_tracker_activity_extends_session() -> None:
    clock = FakeClock()
    tracker = SessionTracker(60, clock)

    first = tracker.session_for(KITCHEN)
    clock.now += 40
    tracker.touch(KITCHEN)
    clock.now += 40
    assert tracker.session_for(KITCHEN) == first


def test_tracker_lookup_alone_does_not_extend_session() -> None:
    """A turn that never completes must not keep the session alive."""
    clock = FakeClock()
    tracker = SessionTracker(60, clock)

    first = tracker.session_for(KITCHEN)
    clock.now += 40
    tracker.session_for(KITCHEN)
    clock.now += 40
    assert tracker.session_for(KITCHEN) != first


def test_tracker_reset_starts_fresh_session() -> None:
    tracker = SessionTracker(60, FakeClock())

    first = tracker.session_for(KITCHEN)
    tracker.reset(KITCHEN)

    assert tracker.session_for(KITCHEN) != first


def test_tracker_reset_leaves_other_origins() -> None:
    tracker = SessionTracker(60, FakeClock())

    office = tracker.session_for(OFFICE)
    tracker.session_for(KITCHEN)
    tracker.reset(KITCHEN)

    assert tracker.session_for(OFFICE) == office


class Snapshots:
    def __init__(self) -> None:
        self.taken = 0

    def __call__(self) -> str:
        self.taken += 1
        return f"snapshot {self.taken}"


def test_snapshot_is_kept_for_the_session() -> None:
    clock = FakeClock()
    tracker = SessionTracker(600, clock)
    take = Snapshots()

    tracker.session_for(KITCHEN)
    first = tracker.snapshot_for(KITCHEN, take, 60)
    clock.now += 59

    assert tracker.snapshot_for(KITCHEN, take, 60) == first
    assert take.taken == 1


def test_snapshot_is_retaken_once_old() -> None:
    clock = FakeClock()
    tracker = SessionTracker(600, clock)
    take = Snapshots()

    tracker.session_for(KITCHEN)
    first = tracker.snapshot_for(KITCHEN, take, 60)
    clock.now += 60

    assert tracker.snapshot_for(KITCHEN, take, 60) != first


def test_snapshot_without_a_session_is_always_fresh() -> None:
    tracker = SessionTracker(600, FakeClock())
    take = Snapshots()

    tracker.snapshot_for(KITCHEN, take, 60)
    tracker.snapshot_for(KITCHEN, take, 60)

    assert take.taken == 2


def test_new_session_takes_a_new_snapshot() -> None:
    tracker = SessionTracker(600, FakeClock())
    take = Snapshots()

    tracker.session_for(KITCHEN)
    first = tracker.snapshot_for(KITCHEN, take, 60)
    tracker.reset(KITCHEN)
    tracker.session_for(KITCHEN)

    assert tracker.snapshot_for(KITCHEN, take, 60) != first


def test_tracker_keeps_origins_apart() -> None:
    tracker = SessionTracker(60, FakeClock())

    assert tracker.session_for(KITCHEN) != tracker.session_for(OFFICE)


def test_tracker_ids_are_path_safe() -> None:
    session_id = SessionTracker(60, FakeClock()).session_for(KITCHEN)

    assert session_id.startswith(SESSION_ID_PREFIX)
    assert session_id.removeprefix(SESSION_ID_PREFIX).isalnum()


def test_tracker_disabled_at_zero() -> None:
    tracker = SessionTracker(0, FakeClock())

    assert tracker.enabled is False
    assert tracker.session_for(KITCHEN) != tracker.session_for(KITCHEN)


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, next(iter(entry.subentries))
    )
    assert entity is not None
    return entity


async def _converse(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    text: str,
    *,
    satellite_id: str | None = None,
    conversation_id: str | None = None,
):
    return await conversation.async_converse(
        hass,
        text,
        conversation_id,
        Context(),
        agent_id=_entity_id(hass, entry),
        satellite_id=satellite_id,
    )


def _session_header(aioclient_mock: AiohttpClientMocker) -> str | None:
    headers = aioclient_mock.mock_calls[-1][3]
    return headers.get(SESSION_ID_HEADER) if headers else None


def _completions(aioclient_mock: AiohttpClientMocker) -> int:
    return sum(str(call[1]) == COMPLETIONS_URL for call in aioclient_mock.mock_calls)


async def test_session_header_is_sent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)

    assert _session_header(aioclient_mock) is not None


async def test_same_satellite_continues_session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    first = _session_header(aioclient_mock)
    await _converse(hass, entry, "and then", satellite_id=KITCHEN)

    assert _session_header(aioclient_mock) == first


async def test_other_satellite_gets_own_session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    first = _session_header(aioclient_mock)
    await _converse(hass, entry, "hello", satellite_id=OFFICE)

    assert _session_header(aioclient_mock) != first


async def test_text_chat_falls_back_to_conversation_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """Without a satellite or device, Home Assistant's conversation is the key."""
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    result = await _converse(hass, entry, "hello")
    first = _session_header(aioclient_mock)
    await _converse(hass, entry, "more", conversation_id=result.conversation_id)

    assert first is not None
    assert _session_header(aioclient_mock) == first


async def test_live_session_sends_only_newest_turn(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """Hermes ignores body history for a continued session; do not send it."""
    entry = await load_entry({CONF_HOUSE_STATE: False, CONF_PROMPT: "Be terse."})
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    result = await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    await _converse(
        hass,
        entry,
        "and then",
        satellite_id=KITCHEN,
        conversation_id=result.conversation_id,
    )

    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == "Be terse."
    assert messages[1]["content"].startswith(CLOCK_PREFIX)
    assert messages[1]["content"].endswith("\nand then")


async def test_disabled_continuity_replays_chat_log(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_HOUSE_STATE: False, CONF_SESSION_TIMEOUT: 0})
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    result = await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    await _converse(
        hass,
        entry,
        "and then",
        satellite_id=KITCHEN,
        conversation_id=result.conversation_id,
    )

    assert _session_header(aioclient_mock) is None
    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert [m["content"] for m in messages[:2]] == ["hello", "ok"]
    assert messages[2]["content"].startswith(CLOCK_PREFIX)
    assert messages[2]["content"].endswith("\nand then")


def _system(aioclient_mock: AiohttpClientMocker) -> str:
    messages = aioclient_mock.mock_calls[-1][2]["messages"]
    assert messages[0]["role"] == "system"
    return messages[0]["content"]


async def test_house_snapshot_holds_for_the_session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """A byte-identical system message keeps the transcript cached."""
    expose(hass, "light.kitchen", "Kitchen Lights", "on")
    entry = await load_entry({CONF_PROMPT: "Be terse."})
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    first = _system(aioclient_mock)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen Lights"})
    await _converse(hass, entry, "and then", satellite_id=KITCHEN)

    assert _system(aioclient_mock) == first
    assert first.endswith("Kitchen Lights: on")


async def test_new_session_sees_the_current_house(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    expose(hass, "light.kitchen", "Kitchen Lights", "on")
    entry = await load_entry({CONF_PROMPT: "Be terse."})
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen Lights"})
    await _converse(hass, entry, "new session", satellite_id=KITCHEN)
    await _converse(hass, entry, "hello", satellite_id=KITCHEN)

    assert _system(aioclient_mock).endswith("Kitchen Lights: off")


async def test_prompt_is_rendered_fresh_within_a_session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """Only the house snapshot is frozen; who is speaking can change mid-session."""
    entry = await load_entry(
        {CONF_HOUSE_STATE: False, CONF_PROMPT: "Talking to {{ user_name }}."}
    )
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))
    mark = await hass.auth.async_create_user("Mark")
    jenna = await hass.auth.async_create_user("Jenna")

    for user in (mark, jenna):
        await conversation.async_converse(
            hass,
            "hello",
            None,
            Context(user_id=user.id),
            agent_id=_entity_id(hass, entry),
            satellite_id=KITCHEN,
        )

    assert _system(aioclient_mock) == "Talking to Jenna."


async def test_reload_starts_fresh_sessions(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    first = _session_header(aioclient_mock)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _converse(hass, entry, "hello", satellite_id=KITCHEN)

    assert _session_header(aioclient_mock) != first


@pytest.mark.parametrize(
    "phrase", [" New session. ", "START OVER!", "Reset.", "Slash new.", "/new"]
)
async def test_reset_phrase_starts_fresh_session(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    load_entry: EntryLoader,
    phrase: str,
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    first = _session_header(aioclient_mock)
    result = await _converse(hass, entry, phrase, satellite_id=KITCHEN)

    assert _completions(aioclient_mock) == 1
    assert result.response.speech["plain"]["speech"] == RESET_REPLY

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    assert _session_header(aioclient_mock) != first


@pytest.mark.parametrize(
    "text", ["new session please", "let's start over the story", "reset the timer"]
)
async def test_reset_phrase_must_be_the_whole_utterance(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    load_entry: EntryLoader,
    text: str,
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=KITCHEN)
    first = _session_header(aioclient_mock)
    await _converse(hass, entry, text, satellite_id=KITCHEN)

    assert _completions(aioclient_mock) == 2
    assert _session_header(aioclient_mock) == first


async def test_reset_phrase_leaves_other_satellites(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    await _converse(hass, entry, "hello", satellite_id=OFFICE)
    office = _session_header(aioclient_mock)
    await _converse(hass, entry, "new session", satellite_id=KITCHEN)
    await _converse(hass, entry, "hello", satellite_id=OFFICE)

    assert _session_header(aioclient_mock) == office


async def test_reset_phrase_goes_to_hermes_without_continuity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """With every turn already a fresh session there is nothing to reset."""
    entry = await load_entry({CONF_SESSION_TIMEOUT: 0})
    aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))

    result = await _converse(hass, entry, "new session", satellite_id=KITCHEN)

    assert _completions(aioclient_mock) == 1
    assert result.response.speech["plain"]["speech"] == "ok"


async def test_only_completed_turns_extend_the_session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, exc=TimeoutError)

    with patch.object(SessionTracker, "touch") as touch:
        await _converse(hass, entry, "hello", satellite_id=KITCHEN)
        touch.assert_not_called()

        aioclient_mock.clear_requests()
        aioclient_mock.post(COMPLETIONS_URL, content=sse.reply("ok"))
        await _converse(hass, entry, "hello", satellite_id=KITCHEN)
        touch.assert_called_once_with(KITCHEN)


async def test_refused_continuation_is_an_error_response(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    load_entry: EntryLoader,
    caplog,
) -> None:
    """Hermes returns 403 when the profile has no API key for sessions."""
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, status=HTTPStatus.FORBIDDEN)

    result = await _converse(hass, entry, "hello", satellite_id=KITCHEN)

    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert "API_SERVER_KEY" in caplog.text
