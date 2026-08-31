"""Streaming is what lets TTS start before the whole answer arrives."""

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import DOMAIN

from .conftest import EntryLoader
from .const import COMPLETIONS_URL
from .sse import DONE, ROLE_CHUNK, content, frames


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, next(iter(entry.subentries))
    )
    assert entity is not None
    return entity


async def _converse(hass: HomeAssistant, agent_id: str):
    return await conversation.async_converse(
        hass, "hello", None, Context(), agent_id=agent_id
    )


async def test_stream_is_requested(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(
        COMPLETIONS_URL, content=frames(ROLE_CHUNK, content("Hi."), DONE)
    )

    await _converse(hass, _entity_id(hass, entry))

    assert aioclient_mock.mock_calls[-1][2]["stream"] is True


async def test_deltas_are_assembled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.post(
        COMPLETIONS_URL,
        content=frames(
            ROLE_CHUNK,
            content("The kitchen "),
            content("light is on."),
            DONE,
        ),
    )

    result = await _converse(hass, _entity_id(hass, entry))

    assert result.response.speech["plain"]["speech"] == "The kitchen light is on."


async def test_malformed_frame_is_skipped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """One bad frame must not lose the whole reply."""
    entry = await load_entry()
    aioclient_mock.post(
        COMPLETIONS_URL,
        content=frames(ROLE_CHUNK, "{not json", content("Still here."), DONE),
    )

    result = await _converse(hass, _entity_id(hass, entry))

    assert result.response.speech["plain"]["speech"] == "Still here."


async def test_empty_stream_is_an_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """A stream that never produces content must not look like success."""
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=frames(DONE))

    result = await _converse(hass, _entity_id(hass, entry))

    assert result.response.response_type is intent.IntentResponseType.ERROR


async def test_stream_without_done_sentinel(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """A gateway that just closes the connection is still a complete answer."""
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, content=frames(ROLE_CHUNK, content("Done.")))

    result = await _converse(hass, _entity_id(hass, entry))

    assert result.response.speech["plain"]["speech"] == "Done."


@pytest.mark.parametrize("status", [401, 500])
async def test_stream_http_error_is_an_error_response(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    load_entry: EntryLoader,
    status: int,
) -> None:
    entry = await load_entry()
    aioclient_mock.post(COMPLETIONS_URL, status=status)

    result = await _converse(hass, _entity_id(hass, entry))

    assert result.response.response_type is intent.IntentResponseType.ERROR
