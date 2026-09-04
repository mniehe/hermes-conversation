"""Targeted announcements: one satellite, named by the model."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.hermes_conversation.llm import (
    ANNOUNCE_TOOL,
    NO_SATELLITES,
    RESTRICTED_API_ID,
)
from custom_components.hermes_conversation.satellites import (
    ANNOUNCE_FEATURE,
    SATELLITE_DOMAIN,
    SERVICE_ANNOUNCE,
    announce_capable,
)

ASSISTANT = "conversation"


@pytest.fixture(autouse=True)
async def setup_intents(hass: HomeAssistant):
    """The restricted API wraps Assist, which needs the intent component."""
    assert await async_setup_component(hass, "intent", {})


@pytest.fixture
def announcements(hass: HomeAssistant) -> list:
    return async_mock_service(hass, SATELLITE_DOMAIN, SERVICE_ANNOUNCE)


def _satellite(
    hass: HomeAssistant,
    object_id: str,
    name: str,
    *,
    features: int = ANNOUNCE_FEATURE,
    config_entry: MockConfigEntry | None = None,
) -> er.RegistryEntry:
    entry = er.async_get(hass).async_get_or_create(
        SATELLITE_DOMAIN,
        "test",
        object_id,
        suggested_object_id=object_id,
        supported_features=features,
        config_entry=config_entry,
    )
    hass.states.async_set(entry.entity_id, "idle", {"friendly_name": name})
    return entry


async def _tool(hass: HomeAssistant) -> llm.Tool:
    context = llm.LLMContext(
        platform="hermes_conversation",
        context=None,
        language="en",
        assistant=ASSISTANT,
        device_id=None,
    )
    api = await llm.async_get_api(hass, RESTRICTED_API_ID, context)
    return next(tool for tool in api.tools if tool.name == ANNOUNCE_TOOL)


async def _call(hass: HomeAssistant, **args) -> dict:
    context = llm.LLMContext(
        platform="hermes_conversation",
        context=None,
        language="en",
        assistant=ASSISTANT,
        device_id=None,
    )
    api = await llm.async_get_api(hass, RESTRICTED_API_ID, context)
    return await api.async_call_tool(
        llm.ToolInput(tool_name=ANNOUNCE_TOOL, tool_args=args)
    )


async def test_lists_satellites_with_their_rooms(
    hass: HomeAssistant, load_entry
) -> None:
    """The model can only pick a satellite it has been told about."""
    await load_entry()
    kitchen = ar.async_get(hass).async_create("Kitchen")
    box = MockConfigEntry(domain="test")
    box.add_to_hass(hass)
    office_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=box.entry_id,
        connections=set(),
        identifiers={("test", "office-box")},
    )
    office = ar.async_get(hass).async_create("Office")
    dr.async_get(hass).async_update_device(office_device.id, area_id=office.id)
    registry = er.async_get(hass)

    sat = _satellite(hass, "kitchen", "Kitchen Voice")
    registry.async_update_entity(sat.entity_id, area_id=kitchen.id)
    sat = _satellite(hass, "office", "Office Voice")
    registry.async_update_entity(sat.entity_id, device_id=office_device.id)
    _satellite(hass, "hallway", "Hallway Voice")

    tool = await _tool(hass)

    assert "assist_satellite.kitchen: Kitchen Voice in the Kitchen" in tool.description
    assert "assist_satellite.office: Office Voice in the Office" in tool.description
    assert "assist_satellite.hallway: Hallway Voice;" in tool.description


async def test_only_satellites_that_can_announce_are_listed(
    hass: HomeAssistant, load_entry
) -> None:
    """A satellite the announce service would reject must not be offered."""
    await load_entry()
    voip = MockConfigEntry(domain="voip")
    voip.add_to_hass(hass)

    _satellite(hass, "kitchen", "Kitchen Voice")
    _satellite(hass, "listen_only", "Listen Only", features=0)
    _satellite(hass, "phone", "Phone", config_entry=voip)
    hass.states.async_set("assist_satellite.unregistered", "idle")

    assert [s.entity_id for s in announce_capable(hass)] == ["assist_satellite.kitchen"]


async def test_no_satellites_is_said_rather_than_hidden(
    hass: HomeAssistant, load_entry
) -> None:
    await load_entry()

    tool = await _tool(hass)

    assert tool.description.endswith(f"{NO_SATELLITES}.")


async def test_announces_on_the_named_satellite_only(
    hass: HomeAssistant, load_entry, announcements: list
) -> None:
    await load_entry()
    _satellite(hass, "kitchen", "Kitchen Voice")
    _satellite(hass, "office", "Office Voice")

    result = await _call(
        hass, satellite_id="assist_satellite.office", message="Laundry is done"
    )

    assert result == {"success": True, "result": "Announced on Office Voice"}
    assert len(announcements) == 1
    assert announcements[0].data == {
        "entity_id": "assist_satellite.office",
        "message": "Laundry is done",
    }


async def test_unknown_satellite_is_refused_with_the_choices(
    hass: HomeAssistant, load_entry, announcements: list
) -> None:
    """A typo must not fall through to the service and its own error path."""
    await load_entry()
    _satellite(hass, "kitchen", "Kitchen Voice")

    result = await _call(hass, satellite_id="assist_satellite.kichen", message="hi")

    assert result == {
        "error": "Unknown satellite assist_satellite.kichen. "
        "Choose one of: assist_satellite.kitchen."
    }
    assert announcements == []


async def test_unknown_satellite_with_none_available(
    hass: HomeAssistant, load_entry, announcements: list
) -> None:
    await load_entry()

    result = await _call(hass, satellite_id="assist_satellite.kitchen", message="hi")

    assert result["error"].endswith(f"Choose one of: {NO_SATELLITES}.")
    assert announcements == []


@pytest.mark.parametrize(
    "args",
    [
        {"satellite_id": "assist_satellite.kitchen"},
        {"satellite_id": "assist_satellite.kitchen", "message": ""},
        {"message": "hi"},
        {"satellite_id": "assist_satellite.kitchen", "message": "hi", "loud": True},
    ],
)
async def test_bad_arguments_are_reported_not_raised(
    hass: HomeAssistant, load_entry, announcements: list, args: dict
) -> None:
    """The model gets a correctable error instead of a failed tool call."""
    await load_entry()
    _satellite(hass, "kitchen", "Kitchen Voice")

    result = await _call(hass, **args)

    assert result["error"].startswith("Invalid arguments: ")
    assert announcements == []
