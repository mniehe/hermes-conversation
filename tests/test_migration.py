"""Upgrading must not change what a tuned agent is sent."""

from homeassistant.const import CONF_PROMPT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import (
    CONF_HOUSE_STATE,
    CONFIG_MINOR_VERSION,
    DEFAULT_PROMPT,
    LEGACY_PROMPT,
)

from .conftest import EntryLoader

# The old default as a user pasted it from the README, wrapping kept.
WRAPPED_LEGACY_PROMPT = LEGACY_PROMPT.replace(" Do not", "\n  Do not")


def _agent(entry) -> dict:
    return dict(next(iter(entry.subentries.values())).data)


async def test_untouched_prompt_gets_the_block_and_new_wording(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: WRAPPED_LEGACY_PROMPT}, minor_version=1)

    assert entry.minor_version == CONFIG_MINOR_VERSION
    assert _agent(entry) == {CONF_PROMPT: DEFAULT_PROMPT, CONF_HOUSE_STATE: True}


async def test_customised_prompt_is_left_alone_with_the_block_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: "You are terse."}, minor_version=1)

    assert _agent(entry) == {CONF_PROMPT: "You are terse.", CONF_HOUSE_STATE: False}


async def test_agent_that_already_chose_is_not_overridden(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(
        {CONF_PROMPT: LEGACY_PROMPT, CONF_HOUSE_STATE: False}, minor_version=1
    )

    assert _agent(entry) == {CONF_PROMPT: LEGACY_PROMPT, CONF_HOUSE_STATE: False}


async def test_current_entries_are_not_migrated(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: LEGACY_PROMPT})

    assert _agent(entry) == {CONF_PROMPT: LEGACY_PROMPT}
