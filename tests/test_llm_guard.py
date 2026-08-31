"""The capability boundary. A missing branch here is a vulnerability."""

import logging
from unittest.mock import patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent, llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hermes_conversation.llm import RESTRICTED_API_ID

ASSISTANT = "conversation"


@pytest.fixture(autouse=True)
async def setup_intents(hass: HomeAssistant):
    """Intent handlers are what the guarded tools wrap."""
    assert await async_setup_component(hass, "intent", {})


def _expose(hass: HomeAssistant, entity_id: str, name: str, **attrs) -> None:
    hass.states.async_set(entity_id, "on", {ATTR_FRIENDLY_NAME: name, **attrs})
    async_expose_entity(hass, ASSISTANT, entity_id, True)


@pytest.fixture
def calls(hass: HomeAssistant) -> dict[str, list]:
    """Record service calls so refusal can be checked against real effect."""
    return {
        f"{domain}.{service}": async_mock_service(hass, domain, service)
        for domain, service in (
            ("light", "turn_on"),
            ("light", "turn_off"),
            ("cover", "open_cover"),
            ("cover", "close_cover"),
            ("lock", "lock"),
            ("lock", "unlock"),
        )
    }


@pytest.fixture
async def house(hass: HomeAssistant, load_entry, calls) -> None:
    """A house with things that must stay reachable and things that must not."""
    await load_entry()
    _expose(hass, "light.kitchen", "kitchen light")
    _expose(
        hass,
        "cover.living_room_blind",
        "living room blind",
        **{ATTR_DEVICE_CLASS: "blind"},
    )
    _expose(hass, "cover.garage", "garage", **{ATTR_DEVICE_CLASS: "garage"})
    _expose(hass, "cover.side_entry", "side entry", **{ATTR_DEVICE_CLASS: "door"})
    hass.states.async_set(
        "lock.front_door", "locked", {ATTR_FRIENDLY_NAME: "front door"}
    )
    async_expose_entity(hass, ASSISTANT, "lock.front_door", True)


async def _api(hass: HomeAssistant) -> llm.APIInstance:
    context = llm.LLMContext(
        platform="hermes_conversation",
        context=None,
        language="en",
        assistant=ASSISTANT,
        device_id=None,
    )
    return await llm.async_get_api(hass, RESTRICTED_API_ID, context)


async def _call(hass: HomeAssistant, tool: str, **args) -> dict:
    api = await _api(hass)
    return await api.async_call_tool(
        llm.ToolInput(tool_name=tool, tool_args=args, id="test")
    )


def _refused(result: dict) -> bool:
    return "error" in result


async def test_api_is_registered(hass: HomeAssistant, load_entry) -> None:
    await load_entry()
    assert RESTRICTED_API_ID in {api.id for api in llm.async_get_apis(hass)}


async def test_api_is_unregistered_on_unload(hass: HomeAssistant, load_entry) -> None:
    entry = await load_entry()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert RESTRICTED_API_ID not in {api.id for api in llm.async_get_apis(hass)}


async def test_offers_the_same_tools_as_assist(hass: HomeAssistant, house) -> None:
    """Restricting must not mean losing capability."""
    context = llm.LLMContext(
        platform="hermes_conversation",
        context=None,
        language="en",
        assistant=ASSISTANT,
        device_id=None,
    )
    assist = await llm.async_get_api(hass, llm.LLM_API_ASSIST, context)
    guarded = await _api(hass)

    assert {tool.name for tool in guarded.tools} == {tool.name for tool in assist.tools}


@pytest.mark.parametrize("tool", ["HassTurnOff", "HassTurnOn"])
async def test_lock_by_name_is_refused(hass: HomeAssistant, house, tool: str) -> None:
    """HassTurnOff unlocks a lock; the guard must catch it by target."""
    result = await _call(hass, tool, name="front door")

    assert _refused(result)
    assert hass.states.get("lock.front_door").state == "locked"


async def test_lock_by_domain_is_refused(hass: HomeAssistant, house) -> None:
    result = await _call(hass, "HassTurnOff", name="front door", domain=["lock"])

    assert _refused(result)


async def test_door_cover_is_refused(hass: HomeAssistant, house, calls) -> None:
    result = await _call(hass, "HassTurnOn", name="side entry")

    assert _refused(result)
    assert not calls["cover.open_cover"]


async def test_garage_cover_is_refused(hass: HomeAssistant, house, calls) -> None:
    result = await _call(hass, "HassTurnOn", name="garage")

    assert _refused(result)
    assert not calls["cover.open_cover"]


async def test_light_is_allowed(hass: HomeAssistant, house, calls) -> None:
    """The guard must not block ordinary control."""
    result = await _call(hass, "HassTurnOff", name="kitchen light")

    assert not _refused(result)
    assert len(calls["light.turn_off"]) == 1


async def test_ordinary_cover_is_allowed(hass: HomeAssistant, house, calls) -> None:
    result = await _call(hass, "HassTurnOff", name="living room blind")

    assert not _refused(result)
    assert len(calls["cover.close_cover"]) == 1


async def test_reading_lock_state_still_works(hass: HomeAssistant, house) -> None:
    """Denial is on writes only; asking whether the door is locked is fine."""
    result = await _call(hass, "GetLiveContext")

    assert not _refused(result)
    assert "front door" in str(result)


async def _in_area(hass: HomeAssistant, entity_id: str, name: str, area: str) -> None:
    """Register an entity properly so area targeting can reach it."""
    area_entry = ar.async_get(hass).async_get_or_create(area)
    domain, object_id = entity_id.split(".")
    entry = er.async_get(hass).async_get_or_create(
        domain, "test", object_id, suggested_object_id=object_id
    )
    er.async_get(hass).async_update_entity(
        entry.entity_id, area_id=area_entry.id, name=name
    )
    hass.states.async_set(entry.entity_id, "on", {ATTR_FRIENDLY_NAME: name})
    async_expose_entity(hass, ASSISTANT, entry.entity_id, True)


async def test_area_sweep_containing_a_lock_is_refused(
    hass: HomeAssistant, load_entry, calls
) -> None:
    """A broad command must not quietly catch a door the model never named."""
    await load_entry()
    await _in_area(hass, "light.hall", "hall light", "Hallway")
    await _in_area(hass, "lock.hall_door", "hall door", "Hallway")

    result = await _call(hass, "HassTurnOff", area="Hallway")

    assert _refused(result)
    assert not calls["lock.unlock"]
    assert not calls["light.turn_off"]


async def test_area_without_a_lock_is_allowed(
    hass: HomeAssistant, load_entry, calls
) -> None:
    """The fail-safe must not spread to areas that hold nothing sensitive."""
    await load_entry()
    await _in_area(hass, "light.study", "study light", "Study")

    result = await _call(hass, "HassTurnOff", area="Study")

    assert not _refused(result)
    assert len(calls["light.turn_off"]) == 1


async def test_unexposed_lock_is_refused_by_home_assistant(
    hass: HomeAssistant, load_entry, calls
) -> None:
    """The two layers must agree: unexposed means unreachable either way.

    The guard matches with the assistant constraint, so an unexposed lock is
    invisible to it and the call passes through — and Home Assistant refuses the
    target itself. Belt and braces, not one covering for the other.
    """
    await load_entry()
    hass.states.async_set("lock.cellar", "locked", {ATTR_FRIENDLY_NAME: "cellar door"})
    async_expose_entity(hass, ASSISTANT, "lock.cellar", False)

    with pytest.raises(intent.MatchFailedError):
        await _call(hass, "HassTurnOff", name="cellar door")

    assert not calls["lock.unlock"]


async def test_guard_failure_refuses(hass: HomeAssistant, house, calls) -> None:
    """If the matcher cannot answer, the call must not be let through."""
    with patch(
        "custom_components.hermes_conversation.llm.intent.async_match_targets",
        side_effect=RuntimeError("registry exploded"),
    ):
        result = await _call(hass, "HassTurnOff", name="kitchen light")

    assert _refused(result)
    assert not calls["light.turn_off"]


async def test_refusal_is_logged_at_warning(hass: HomeAssistant, house, caplog) -> None:
    """A blocked attempt on a door is worth seeing without enabling debug."""
    caplog.set_level(logging.INFO)

    await _call(hass, "HassTurnOff", name="front door")

    refusals = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and "Refused" in record.message
    ]
    assert refusals


async def test_alarm_panel_is_refused(hass: HomeAssistant, load_entry, calls) -> None:
    """Assist cannot reach alarm panels today; the boundary must not depend on that."""
    await load_entry()
    _expose(hass, "alarm_control_panel.house", "house alarm")

    result = await _call(hass, "HassTurnOff", name="house alarm")

    assert _refused(result)
