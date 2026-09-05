"""Set up Hermes Conversation."""

from __future__ import annotations

import logging

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
    ConfigSubentry,
)
from homeassistant.const import CONF_PROMPT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType

from .client import HermesAuthError, HermesClient, HermesConnectionError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_HOUSE_STATE,
    CONF_PROFILE,
    CONFIG_MINOR_VERSION,
    DEFAULT_PROMPT,
    DOMAIN,
    ISSUE_PROFILE_IGNORED,
    LEGACY_PROMPT,
    SUBENTRY_TYPE_CONVERSATION,
)
from .llm import MCP_SERVER_DOMAIN, async_check_mcp_server, async_register_api
from .policy import (
    DATA_GROUP,
    RestrictedGroup,
    async_watch_entities,
    async_watch_users,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (Platform.CONVERSATION,)

type HermesConfigEntry = ConfigEntry[HermesClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide resources."""
    async_register_api(hass)
    group = hass.data[DATA_GROUP] = RestrictedGroup(hass)
    async_watch_entities(hass, group)
    async_watch_users(hass, group)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_PROFILE_IGNORED)

    @callback
    def _config_entry_changed(change: ConfigEntryChange, entry: ConfigEntry) -> None:
        if entry.domain == MCP_SERVER_DOMAIN:
            async_check_mcp_server(hass)
        elif entry.domain == DOMAIN and change is ConfigEntryChange.REMOVED:
            ir.async_delete_issue(
                hass, DOMAIN, f"{ISSUE_PROFILE_IGNORED}_{entry.entry_id}"
            )
            hass.async_create_task(group.async_sync_users())
        elif (
            entry.domain == DOMAIN
            and change is ConfigEntryChange.UPDATED
            and entry.state is ConfigEntryState.LOADED
        ):
            hass.async_create_task(group.async_sync_users())

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _config_entry_changed)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Set up Hermes Conversation from a config entry."""
    client = HermesClient(
        hass,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_PROFILE],
        entry.data[CONF_API_KEY],
    )

    try:
        models = await client.async_list_models()
    except HermesAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except HermesConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    _LOGGER.debug(
        "Connected to Hermes profile %s; %d model(s) advertised",
        entry.data[CONF_PROFILE],
        len(models),
    )

    entry.runtime_data = client
    async_check_mcp_server(hass)
    await _async_check_profile_routing(hass, client, entry)
    await hass.data[DATA_GROUP].async_sync_users()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Unload a Hermes Conversation config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an entry written by an older release up to date."""
    if entry.minor_version < CONFIG_MINOR_VERSION:
        for subentry in entry.subentries.values():
            _migrate_house_state(hass, entry, subentry)

    hass.config_entries.async_update_entry(entry, minor_version=CONFIG_MINOR_VERSION)
    return True


def _migrate_house_state(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> None:
    """Turn the house-state block on only where the prompt was never touched.

    An agent still on the shipped prompt gets the new default and the block.
    A customised prompt is left alone with the block off, so an upgrade never
    changes what a tuned agent is sent.
    """
    if subentry.subentry_type != SUBENTRY_TYPE_CONVERSATION:
        return
    if CONF_HOUSE_STATE in subentry.data:
        return

    data = dict(subentry.data)
    if _same_words(data.get(CONF_PROMPT, ""), LEGACY_PROMPT):
        data[CONF_PROMPT] = DEFAULT_PROMPT
        data[CONF_HOUSE_STATE] = True
    else:
        data[CONF_HOUSE_STATE] = False
    hass.config_entries.async_update_subentry(entry, subentry, data=data)


def _same_words(left: str, right: str) -> bool:
    """Compare prompts ignoring how they were wrapped when pasted."""
    return left.split() == right.split()


async def _async_check_profile_routing(
    hass: HomeAssistant, client: HermesClient, entry: HermesConfigEntry
) -> None:
    """Warn when Hermes is ignoring the profile prefix.

    With ``gateway.multiplex_profiles`` off, Hermes drops the ``/p/<profile>/``
    prefix and serves the default profile instead. Nothing fails, so without
    this the only symptom is an agent answering as the wrong persona.
    """
    issue_id = f"{ISSUE_PROFILE_IGNORED}_{entry.entry_id}"
    if await client.async_profile_prefix_honoured() is not False:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PROFILE_IGNORED,
        translation_placeholders={"profile": entry.data[CONF_PROFILE]},
    )
