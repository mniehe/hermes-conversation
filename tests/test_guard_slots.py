"""Every argument an Assist tool accepts must be classified by the guard.

The guard refuses any call carrying an argument it has not classified as a
target selector or a plain value, so an unclassified slot silently breaks that
tool for the agent. This enumerates what Home Assistant actually registers.
"""

import voluptuous as vol
from homeassistant.components.todo.llm import TodoGetItemsTool
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component

from custom_components.hermes_conversation.llm import NON_TARGET_SLOTS, TARGET_SLOTS

# Components whose intents Assist exposes as tools. assist_satellite cannot be
# imported here (its tts dependency needs mutagen), so its one intent is
# transcribed below from homeassistant.components.assist_satellite.intent.
INTENT_COMPONENTS = (
    "conversation",
    "intent",
    "todo",
    "shopping_list",
    "light",
    "climate",
    "cover",
    "fan",
    "media_player",
    "weather",
    "humidifier",
    "vacuum",
    "valve",
    "lock",
    "switch",
    "script",
    "timer",
)

# Filled in by Home Assistant from the calling device, never sent by the model
# (see IntentTool.extra_slots in homeassistant.helpers.llm).
HA_INJECTED_SLOTS = {"preferred_area_id", "preferred_floor_id"}


async def test_every_assist_argument_is_classified(hass: HomeAssistant) -> None:
    for component in INTENT_COMPONENTS:
        assert await async_setup_component(hass, component, {})
    await hass.async_block_till_done()

    schemas = {
        handler.intent_type: handler.slot_schema
        for handler in intent.async_get(hass)
        if handler.slot_schema
    }
    schemas["HassBroadcast"] = {vol.Required("message"): str}
    schemas["todo_get_items"] = TodoGetItemsTool(["Shopping"]).parameters.schema

    known = set(TARGET_SLOTS) | set(NON_TARGET_SLOTS) | HA_INJECTED_SLOTS
    unknown = {
        (tool, slot)
        for tool, schema in schemas.items()
        for key in schema
        for slot in _slot_names(key)
        if slot not in known
    }
    assert not unknown, sorted(unknown)


def _slot_names(key: object) -> list[str]:
    """Flatten a voluptuous key into the argument names a model can send."""
    if isinstance(key, str):
        return [key]
    if isinstance(key, vol.Marker):
        return _slot_names(key.schema)
    if isinstance(key, vol.Any):
        return [name for choice in key.validators for name in _slot_names(choice)]
    return [str(key)]
