"""Assist satellites that can speak, and the rooms they are in."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

# Mirrors homeassistant.components.assist_satellite, which cannot be imported
# without the tts stack it drags in.
SATELLITE_DOMAIN = "assist_satellite"
SERVICE_ANNOUNCE = "announce"
ANNOUNCE_FEATURE = 1

# Home Assistant's own broadcast intent skips these: a phone answers a call,
# it does not play announcements unprompted.
EXCLUDED_ENTRY_DOMAINS = ("voip",)


@dataclass(frozen=True, slots=True)
class Satellite:
    """One satellite that accepts announcements."""

    entity_id: str
    name: str
    area_name: str | None

    def describe(self) -> str:
        """Return the satellite as one prompt line."""
        if self.area_name:
            return f"{self.entity_id}: {self.name} in the {self.area_name}"
        return f"{self.entity_id}: {self.name}"


def announce_capable(hass: HomeAssistant) -> list[Satellite]:
    """Return every satellite the announce service will accept."""
    registry = er.async_get(hass)
    satellites: list[Satellite] = []

    for entity_id in sorted(hass.states.async_entity_ids(SATELLITE_DOMAIN)):
        entry = registry.async_get(entity_id)
        if entry is None or not entry.supported_features & ANNOUNCE_FEATURE:
            continue
        if _entry_domain(hass, entry) in EXCLUDED_ENTRY_DOMAINS:
            continue

        state = hass.states.get(entity_id)
        name = state.name if state else entity_id
        satellites.append(Satellite(entity_id, name, area_name(hass, entity_id)))

    return satellites


def area_name(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the area of an entity, falling back to its device's area."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        return None

    area_id = entity.area_id
    if area_id is None and entity.device_id:
        device = dr.async_get(hass).async_get(entity.device_id)
        area_id = device.area_id if device else None
    if area_id is None:
        return None

    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area else None


def _entry_domain(hass: HomeAssistant, entity: er.RegistryEntry) -> str | None:
    if not entity.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    return entry.domain if entry else None
