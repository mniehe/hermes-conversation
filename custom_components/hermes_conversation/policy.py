"""A Home Assistant user group that cannot control locks or doors.

The tool guard in llm.py only sees calls that arrive through the restricted
API. The bearer token Hermes holds can also reach Home Assistant's REST API,
the unrestricted Assist API, scripts and scenes. Home Assistant checks a user's
group policy on every entity service call, so a group that grants control of
everything except locks, alarm panels and door covers closes every route at
once. Home Assistant has no UI or public API for custom groups, so this module
maintains one.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.models import Group, User
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.automation import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.const import ATTR_DEVICE_CLASS, EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_HERMES_USER,
    DOMAIN,
    FORBIDDEN_COVER_CLASSES,
    FORBIDDEN_DOMAINS,
    ISSUE_POLICY_UNSUPPORTED,
    ISSUE_POLICY_USER_ADMIN,
    ISSUE_POLICY_USER_MISSING,
    NO_USER,
)

_LOGGER = logging.getLogger(__name__)

GROUP_ID = f"{DOMAIN}_restricted"
GROUP_NAME = "Hermes (locks and doors withheld)"

READ_ONLY = {POLICY_READ: True}
READ_AND_CONTROL = {POLICY_READ: True, POLICY_CONTROL: True}

# Automations run their actions with no user attached, so Home Assistant never
# permission-checks them; a triggerable automation would be a way around the
# policy. Covers are granted per entity instead, to leave doors out.
READ_ONLY_DOMAINS = (*FORBIDDEN_DOMAINS, AUTOMATION_DOMAIN, COVER_DOMAIN)


def restricted_policy(hass: HomeAssistant) -> dict[str, Any]:
    """Grant control of everything except locks, alarm panels and door covers.

    Policies are allow-lists with no way to say "no": a rule that omits
    "control" has no opinion, and the lookup falls through to the next level
    (entity id, then domain, then "all"). So control is granted explicitly,
    per domain and per permitted cover, and "all" grants only reading. A
    domain that appears later is read-only until the policy is rebuilt.
    """
    domains = {
        domain: READ_AND_CONTROL
        for domain in known_domains(hass)
        if domain not in READ_ONLY_DOMAINS
    }
    covers = {
        entity_id: READ_AND_CONTROL
        for entity_id in _entity_ids(hass, COVER_DOMAIN)
        if entity_id not in forbidden_covers(hass)
    }

    entities: dict[str, Any] = {"domains": domains, "all": READ_ONLY}
    if covers:
        entities["entity_ids"] = covers
    return {"entities": entities}


def known_domains(hass: HomeAssistant) -> set[str]:
    """Every domain with a state or a registry entry."""
    domains = {state.domain for state in hass.states.async_all()}
    domains.update(entry.domain for entry in er.async_get(hass).entities.values())
    return domains


def _entity_ids(hass: HomeAssistant, domain: str) -> set[str]:
    ids = {state.entity_id for state in hass.states.async_all(domain)}
    ids.update(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.domain == domain
    )
    return ids


def forbidden_covers(hass: HomeAssistant) -> list[str]:
    """Door and garage covers, from live state and from the registry."""
    found: set[str] = set()

    for state in hass.states.async_all(COVER_DOMAIN):
        if state.attributes.get(ATTR_DEVICE_CLASS) in FORBIDDEN_COVER_CLASSES:
            found.add(state.entity_id)

    for entry in er.async_get(hass).entities.values():
        if entry.domain != COVER_DOMAIN:
            continue
        device_class = entry.device_class or entry.original_device_class
        if device_class in FORBIDDEN_COVER_CLASSES:
            found.add(entry.entity_id)

    return sorted(found)


class RestrictedGroup:
    """Create, refresh and populate the restricted group."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind to the auth store without touching it yet."""
        self._hass = hass

    @property
    def supported(self) -> bool:
        """Whether this Home Assistant still has the store internals we rely on."""
        store = self._store()
        return isinstance(getattr(store, "_groups", None), dict) and callable(
            getattr(store, "_async_schedule_save", None)
        )

    def _store(self) -> Any:
        return getattr(self._hass.auth, "_store", None)

    @callback
    def get(self) -> Group | None:
        """Return the group if it exists."""
        if not self.supported:
            return None
        group: Group | None = self._store()._groups.get(GROUP_ID)
        return group

    @callback
    def ensure(self) -> Group | None:
        """Create the group, or bring its policy up to date."""
        if not self.supported:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                ISSUE_POLICY_UNSUPPORTED,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_POLICY_UNSUPPORTED,
                translation_placeholders={"group_name": GROUP_NAME},
            )
            return None
        ir.async_delete_issue(self._hass, DOMAIN, ISSUE_POLICY_UNSUPPORTED)

        store = self._store()
        policy = restricted_policy(self._hass)
        group: Group | None = store._groups.get(GROUP_ID)
        if group is None:
            group = Group(name=GROUP_NAME, policy=policy, id=GROUP_ID)
            store._groups[GROUP_ID] = group
            _LOGGER.info("Created the %s user group", GROUP_NAME)
        elif group.policy != policy:
            group.policy = policy
            for user in store._users.values():
                if group in user.groups:
                    user.invalidate_cache()
            _LOGGER.debug("Refreshed the %s policy", GROUP_NAME)
        else:
            return group

        store._async_schedule_save()
        return group

    async def async_sync_users(self) -> None:
        """Put every configured Hermes user in the group, and nobody else."""
        wanted = {
            user_id
            for entry in self._hass.config_entries.async_entries(DOMAIN)
            if (user_id := entry.options.get(CONF_HERMES_USER, NO_USER)) != NO_USER
        }
        group = self.ensure() if wanted else self.get()
        if group is None:
            return

        for user in await self._hass.auth.async_get_users():
            if user.id in wanted:
                await self._async_restrict(user, group)
            elif group in user.groups:
                await self._async_release(user)

        for user_id in wanted:
            if await self._hass.auth.async_get_user(user_id) is None:
                self._issue(ISSUE_POLICY_USER_MISSING, user_id)

    async def _async_restrict(self, user: User, group: Group) -> None:
        if user.is_admin:
            self._issue(ISSUE_POLICY_USER_ADMIN, user.id, user_name=user.name or "")
            return
        ir.async_delete_issue(
            self._hass, DOMAIN, f"{ISSUE_POLICY_USER_ADMIN}_{user.id}"
        )
        ir.async_delete_issue(
            self._hass, DOMAIN, f"{ISSUE_POLICY_USER_MISSING}_{user.id}"
        )
        if user.groups != [group]:
            await self._hass.auth.async_update_user(user, group_ids=[group.id])
            _LOGGER.info("Moved user %s into %s", user.name, GROUP_NAME)

    async def _async_release(self, user: User) -> None:
        await self._hass.auth.async_update_user(user, group_ids=[GROUP_ID_USER])
        _LOGGER.info("Moved user %s back to the Users group", user.name)

    def _issue(self, key: str, user_id: str, **placeholders: str) -> None:
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            f"{key}_{user_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=key,
            translation_placeholders=placeholders,
        )


@callback
def async_watch_entities(hass: HomeAssistant, group: RestrictedGroup) -> None:
    """Rebuild the policy when the set of entities changes.

    A new domain must be granted control and a new door cover must be
    withheld; both only show up when an entity appears or is re-registered.
    """

    @callback
    def _refresh(_event: Event[Any]) -> None:
        if group.get() is not None:
            group.ensure()

    @callback
    def _state_changed(event: Event[EventStateChangedData]) -> None:
        if event.data["old_state"] is None:
            _refresh(event)

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _refresh)
    hass.bus.async_listen(EVENT_STATE_CHANGED, _state_changed)
