"""Agent options live in subentries so editing never re-adds the entry."""

from homeassistant.const import CONF_MODEL, CONF_NAME, CONF_PROMPT, CONF_TIMEOUT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.config_flow import (
    HermesConversationConfigFlow,
)
from custom_components.hermes_conversation.const import (
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)

from .conftest import EntryLoader
from .const import DEFAULT_MODELS, MODELS_URL


async def test_conversation_subentry_type_is_supported(hass: HomeAssistant) -> None:
    supported = HermesConversationConfigFlow.async_get_supported_subentry_types(
        MockConfigEntry(domain=DOMAIN)
    )
    assert SUBENTRY_TYPE_CONVERSATION in supported


async def test_add_second_agent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """A second agent on the same profile is a second entity."""
    entry = await load_entry()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONVERSATION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Bedtime",
            CONF_MODEL: "home-assist",
            CONF_PROMPT: "Be brief.",
            CONF_TIMEOUT: 30,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 2
    assert len(hass.states.async_entity_ids("conversation")) == 2


async def test_model_choices_come_from_hermes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    entry = await load_entry()
    aioclient_mock.clear_requests()
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONVERSATION), context={"source": "user"}
    )

    schema = result["data_schema"].schema
    model_field = next(key for key in schema if str(key) == CONF_MODEL)
    options = schema[model_field].config["options"]
    assert "hermes-agent" in options


async def test_reconfigure_subentry_in_place(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry: EntryLoader
) -> None:
    """Changing the prompt must not require deleting anything."""
    entry = await load_entry()
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONVERSATION),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Evening",
            CONF_MODEL: "home-assist",
            CONF_PROMPT: "You are terse.",
            CONF_TIMEOUT: 45,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert len(entry.subentries) == 1
    assert entry.subentries[subentry_id].title == "Evening"
    assert entry.subentries[subentry_id].data[CONF_PROMPT] == "You are terse."
    assert entry.subentries[subentry_id].data[CONF_TIMEOUT] == 45
    device = dr.async_get(hass).async_get_device({(DOMAIN, subentry_id)})
    assert device is not None
    assert device.name == "Evening"
