"""Upgrading must not change what a tuned agent is sent."""

from homeassistant.const import CONF_PROMPT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.const import (
    CONF_HOUSE_STATE,
    CONFIG_MINOR_VERSION,
    DEFAULT_PROMPT,
    PROMPT_BEFORE_GUARDS,
    PROMPT_BEFORE_HOUSE_STATE,
)

from .conftest import EntryLoader

# The 0.4 default as a user pasted it from the README, wrapping kept.
WRAPPED_OLD_PROMPT = PROMPT_BEFORE_HOUSE_STATE.replace(" Do not", "\n  Do not")


def _agent(entry) -> dict:
    return dict(next(iter(entry.subentries.values())).data)


async def test_untouched_prompt_gets_the_block_and_new_wording(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: WRAPPED_OLD_PROMPT}, minor_version=1)

    assert entry.minor_version == CONFIG_MINOR_VERSION
    assert _agent(entry) == {CONF_PROMPT: DEFAULT_PROMPT, CONF_HOUSE_STATE: True}


async def test_customised_prompt_is_left_alone_with_the_block_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: "You are terse."}, minor_version=1)

    assert _agent(entry) == {CONF_PROMPT: "You are terse.", CONF_HOUSE_STATE: False}


async def test_agent_that_already_chose_keeps_its_choice(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(
        {CONF_PROMPT: PROMPT_BEFORE_HOUSE_STATE, CONF_HOUSE_STATE: False},
        minor_version=1,
    )

    assert _agent(entry) == {CONF_PROMPT: DEFAULT_PROMPT, CONF_HOUSE_STATE: False}


async def test_unguarded_default_is_refreshed_and_toggle_kept(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(
        {CONF_PROMPT: PROMPT_BEFORE_GUARDS, CONF_HOUSE_STATE: False}, minor_version=2
    )

    assert _agent(entry) == {CONF_PROMPT: DEFAULT_PROMPT, CONF_HOUSE_STATE: False}


async def test_customised_prompt_survives_the_guard_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry(
        {CONF_PROMPT: "You are terse.", CONF_HOUSE_STATE: True}, minor_version=2
    )

    assert _agent(entry) == {CONF_PROMPT: "You are terse.", CONF_HOUSE_STATE: True}


async def test_current_entries_are_not_migrated(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry({CONF_PROMPT: PROMPT_BEFORE_HOUSE_STATE})

    assert _agent(entry) == {CONF_PROMPT: PROMPT_BEFORE_HOUSE_STATE}
