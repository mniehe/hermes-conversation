"""Turns from one satellite share a Hermes session until it goes idle."""

from http import HTTPStatus

from homeassistant.components import conversation
from homeassistant.const import CONF_PROMPT
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import (
    CONF_SESSION_TIMEOUT,
    DOMAIN,
    SESSION_ID_HEADER,
)
from custom_components.hermes_conversation.session import (
    SESSION_ID_PREFIX,
    SessionTracker,
)

from . import sse
from .conftest import EntryLoader
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
    tracker.session_for(KITCHEN)
    clock.now += 40
    assert tracker.session_for(KITCHEN) == first


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
    entry = await load_entry({CONF_PROMPT: "Be terse."})
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
    assert messages == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "and then"},
    ]


async def test_disabled_continuity_replays_chat_log(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_SESSION_TIMEOUT: 0})
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
    assert [m["content"] for m in messages] == ["hello", "ok", "and then"]


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
