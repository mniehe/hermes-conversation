"""The user-group policy: the boundary Home Assistant enforces on every route."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.auth.const import GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntity,
    MockEntityPlatform,
    MockUser,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.hermes_conversation.const import (
    CONF_HERMES_USER,
    DOMAIN,
    ISSUE_POLICY_UNSUPPORTED,
    ISSUE_POLICY_USER_ADMIN,
    ISSUE_POLICY_USER_GROUPS,
    ISSUE_POLICY_USER_MISSING,
    NO_USER,
)
from custom_components.hermes_conversation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hermes_conversation.policy import (
    DATA_GROUP,
    GROUP_ID,
    forbidden_covers,
    restricted_policy,
)

from .conftest import EntryLoader


@pytest.fixture
def hermes_user(hass: HomeAssistant) -> MockUser:
    """A non-admin user in the Users group, as the README tells people to make."""
    users = hass.auth._store._groups[GROUP_ID_USER]
    return MockUser(name="Hermes", groups=[users]).add_to_hass(hass)


def _door(hass: HomeAssistant, object_id: str, device_class: str = "door") -> str:
    entity_id = f"cover.{object_id}"
    hass.states.async_set(entity_id, "closed", {"device_class": device_class})
    return entity_id


async def test_policy_grants_control_of_everything_but_locks_and_doors(
    hass: HomeAssistant,
) -> None:
    """No deny exists in HA policies, so control is granted explicitly."""
    hass.states.async_set("light.kitchen", "on")
    hass.states.async_set("lock.front_door", "locked")
    hass.states.async_set("alarm_control_panel.house", "armed_away")
    _door(hass, "front", "door")
    _door(hass, "blind", "blind")
    er.async_get(hass).async_get_or_create(
        "cover",
        "test",
        "garage",
        suggested_object_id="garage",
        original_device_class="garage",
    )

    policy = restricted_policy(hass)

    assert forbidden_covers(hass) == ["cover.front", "cover.garage"]
    assert policy == {
        "entities": {
            "domains": {"light": {POLICY_READ: True, POLICY_CONTROL: True}},
            "entity_ids": {"cover.blind": {POLICY_READ: True, POLICY_CONTROL: True}},
            "all": {POLICY_READ: True},
        }
    }


async def test_no_covers_means_no_entity_rules(hass: HomeAssistant) -> None:
    hass.states.async_set("light.kitchen", "on")

    assert "entity_ids" not in restricted_policy(hass)["entities"]


async def test_selected_user_is_moved_into_the_group(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    """The user ends up in our group only; the Users group would win a merge."""
    hass.states.async_set("light.kitchen", "on")
    _door(hass, "garage", "garage")
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})

    assert [group.id for group in hermes_user.groups] == [GROUP_ID]
    perms = hermes_user.permissions
    assert perms.check_entity("light.kitchen", POLICY_CONTROL)
    assert perms.check_entity("lock.front_door", POLICY_READ)
    assert not perms.check_entity("lock.front_door", POLICY_CONTROL)
    assert not perms.check_entity("cover.garage", POLICY_CONTROL)
    assert not perms.check_entity("alarm_control_panel.house", POLICY_CONTROL)


async def test_core_refuses_the_unlock_for_that_user(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    """The point of the group: Home Assistant itself blocks the service call."""
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})
    platform = MockEntityPlatform(hass, domain="lock", platform_name="lock")
    lock = MockEntity(name="front door", unique_id="front")
    await platform.async_add_entities([lock])
    platform.async_register_entity_service("unlock", {}, "async_update")

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "lock",
            "unlock",
            {"entity_id": lock.entity_id},
            blocking=True,
            context=Context(user_id=hermes_user.id),
        )


async def test_new_domain_gains_control(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    """A domain that appears after the group was built gains control."""
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})
    assert not hermes_user.permissions.check_entity("switch.fan", POLICY_CONTROL)

    hass.states.async_set("switch.fan", "off")
    await hass.async_block_till_done()

    assert hermes_user.permissions.check_entity("switch.fan", POLICY_CONTROL)


async def test_cover_reclassified_as_a_door_loses_control(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "cover",
        "test",
        "side",
        suggested_object_id="side",
        original_device_class="blind",
    )
    hass.states.async_set("cover.side", "closed")
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})
    assert hermes_user.permissions.check_entity("cover.side", POLICY_CONTROL)

    registry.async_update_entity("cover.side", device_class="door")
    await hass.async_block_till_done()

    assert not hermes_user.permissions.check_entity("cover.side", POLICY_CONTROL)


async def test_clearing_the_user_returns_it_to_the_users_group(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    entry = await load_entry(options={CONF_HERMES_USER: hermes_user.id})

    hass.config_entries.async_update_entry(entry, options={CONF_HERMES_USER: NO_USER})
    await hass.async_block_till_done()

    assert [group.id for group in hermes_user.groups] == [GROUP_ID_USER]


async def test_removing_the_entry_returns_the_user(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    entry = await load_entry(options={CONF_HERMES_USER: hermes_user.id})

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert [group.id for group in hermes_user.groups] == [GROUP_ID_USER]


async def test_admin_user_is_left_alone_with_a_repair(
    hass: HomeAssistant, load_entry: EntryLoader, hass_admin_user: MockUser
) -> None:
    """Demoting an administrator silently would be a nasty surprise."""
    await load_entry(options={CONF_HERMES_USER: hass_admin_user.id})

    assert hass_admin_user.is_admin
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_POLICY_USER_ADMIN}_{hass_admin_user.id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"user_name": hass_admin_user.name}


async def test_deleted_user_raises_a_repair(
    hass: HomeAssistant, load_entry: EntryLoader
) -> None:
    await load_entry(options={CONF_HERMES_USER: "gone"})

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_POLICY_USER_MISSING}_gone")
        is not None
    )


async def test_unmanaged_entry_creates_no_group(
    hass: HomeAssistant, load_entry: EntryLoader
) -> None:
    """Nobody asked for the group, so the auth store is not touched."""
    await load_entry()

    assert hass.data[DATA_GROUP].get() is None
    assert GROUP_ID not in hass.auth._store._groups


async def test_changed_store_internals_raise_a_repair(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser, monkeypatch
) -> None:
    """A future Home Assistant may rename what we rely on; say so, don't crash."""
    entry = await load_entry()
    monkeypatch.setattr(hass.auth, "_store", SimpleNamespace())

    hass.config_entries.async_update_entry(
        entry, options={CONF_HERMES_USER: hermes_user.id}
    )
    await hass.async_block_till_done()

    assert not hass.data[DATA_GROUP].supported
    assert hass.data[DATA_GROUP].get() is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_POLICY_UNSUPPORTED)


async def test_options_flow_lists_only_restrictable_users(
    hass: HomeAssistant,
    load_entry: EntryLoader,
    hermes_user: MockUser,
    hass_admin_user: MockUser,
) -> None:
    entry = await load_entry()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    selector = result["data_schema"].schema[CONF_HERMES_USER]
    offered = {option["value"] for option in selector.config["options"]}

    assert offered == {NO_USER, hermes_user.id}


async def test_options_flow_stores_the_user(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    entry = await load_entry()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HERMES_USER: hermes_user.id}
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.options == {CONF_HERMES_USER: hermes_user.id}
    assert [group.id for group in hermes_user.groups] == [GROUP_ID]


async def test_second_entry_keeps_the_user_restricted(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    """One profile releasing its user must not release another profile's."""
    first = await load_entry(options={CONF_HERMES_USER: hermes_user.id})
    second = MockConfigEntry(
        domain=DOMAIN,
        data={**first.data, "profile": "wife"},
        unique_id="other",
        options={CONF_HERMES_USER: hermes_user.id},
    )
    second.add_to_hass(hass)

    hass.config_entries.async_update_entry(first, options={CONF_HERMES_USER: NO_USER})
    await hass.async_block_till_done()

    assert [group.id for group in hermes_user.groups] == [GROUP_ID]


async def test_diagnostics_report_the_policy_state(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    _door(hass, "garage", "garage")
    entry = await load_entry(options={CONF_HERMES_USER: hermes_user.id})

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["policy"] == {
        "supported": True,
        "group_present": True,
        "user_managed": True,
        "user_exists": True,
        "user_is_admin": False,
        "user_in_group": True,
        "forbidden_covers": ["cover.garage"],
    }


async def test_automations_stay_out_of_reach(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    """An automation's actions run with no user, so triggering one is a bypass."""
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "alias": "let me in",
                    "trigger": {"platform": "event", "event_type": "never"},
                    "action": {
                        "action": "lock.unlock",
                        "target": {"entity_id": "lock.front_door"},
                    },
                }
            ]
        },
    )
    unlocks = async_mock_service(hass, "lock", "unlock")
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "automation",
            "trigger",
            {"entity_id": "automation.let_me_in"},
            blocking=True,
            context=Context(user_id=hermes_user.id),
        )
    assert unlocks == []
    assert hermes_user.permissions.check_entity("automation.let_me_in", POLICY_READ)


async def test_viewer_is_neither_offered_nor_escalated(
    hass: HomeAssistant, load_entry: EntryLoader
) -> None:
    """Moving a read-only user into our group would hand it control of the house."""
    viewer_group = hass.auth._store._groups[GROUP_ID_READ_ONLY]
    viewer = MockUser(name="Viewer", groups=[viewer_group]).add_to_hass(hass)
    entry = await load_entry(options={CONF_HERMES_USER: viewer.id})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    offered = {
        option["value"]
        for option in result["data_schema"].schema[CONF_HERMES_USER].config["options"]
    }

    assert viewer.id not in offered
    assert [group.id for group in viewer.groups] == [GROUP_ID_READ_ONLY]
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_POLICY_USER_GROUPS}_{viewer.id}"
    )


async def test_user_edited_elsewhere_is_put_back(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    """Saving the user dialog in Settings rewrites groups; we must notice."""
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})

    await hass.auth.async_update_user(hermes_user, group_ids=[GROUP_ID_USER])
    assert [group.id for group in hermes_user.groups] == [GROUP_ID_USER]
    await _settle(hass)

    assert [group.id for group in hermes_user.groups] == [GROUP_ID]


async def test_user_deleted_elsewhere_raises_a_repair(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})

    await hass.auth.async_remove_user(hermes_user)
    await _settle(hass)

    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_POLICY_USER_MISSING}_{hermes_user.id}"
    )


async def test_stale_user_repair_goes_when_unmanaged(
    hass: HomeAssistant, load_entry: EntryLoader
) -> None:
    entry = await load_entry(options={CONF_HERMES_USER: "gone"})
    issue_id = f"{ISSUE_POLICY_USER_MISSING}_gone"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    ir.async_create_issue(
        hass,
        "other",
        "policy_user_missing_gone",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="x",
    )

    hass.config_entries.async_update_entry(entry, options={CONF_HERMES_USER: NO_USER})
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_options_flow_does_not_suggest_a_vanished_user(
    hass: HomeAssistant, load_entry: EntryLoader
) -> None:
    entry = await load_entry(options={CONF_HERMES_USER: "gone"})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    key = next(k for k in result["data_schema"].schema if k == CONF_HERMES_USER)

    assert (key.description or {}).get("suggested_value") in (None, NO_USER)


async def _settle(hass: HomeAssistant) -> None:
    """Let the user-sync debouncer fire, then let its cooldown expire."""
    for _ in range(2):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()


async def test_group_goes_when_nobody_is_managed(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    """Uninstalling after removing the entry must leave nothing in the auth store."""
    entry = await load_entry(options={CONF_HERMES_USER: hermes_user.id})
    assert GROUP_ID in hass.auth._store._groups

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert GROUP_ID not in hass.auth._store._groups
    assert [group.id for group in hermes_user.groups] == [GROUP_ID_USER]


async def test_only_policy_changing_entities_trigger_a_rebuild(
    hass: HomeAssistant, load_entry: EntryLoader, hermes_user: MockUser
) -> None:
    hass.states.async_set("light.kitchen", "on")
    await load_entry(options={CONF_HERMES_USER: hermes_user.id})
    group = hass.data[DATA_GROUP]

    assert not group.needs_rebuild("light.lamp")
    assert not group.needs_rebuild("lock.back_door")
    assert group.needs_rebuild("switch.fan")
    assert group.needs_rebuild("cover.side")

    policy_before = group.get().policy
    hass.states.async_set("light.lamp", "on")
    er.async_get(hass).async_get_or_create("light", "test", "lamp2")
    await hass.async_block_till_done()

    assert group.get().policy is policy_before
