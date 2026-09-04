"""The config flow is the only place credentials are validated."""

from http import HTTPStatus

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.client import HermesClient
from custom_components.hermes_conversation.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_PROFILE,
    DEFAULT_PROMPT,
    DOMAIN,
)

from .const import API_KEY, BASE_URL, DEFAULT_MODELS, MODELS_URL, PROFILE

USER_INPUT = {CONF_BASE_URL: BASE_URL, CONF_PROFILE: PROFILE, CONF_API_KEY: API_KEY}


async def _submit(hass: HomeAssistant, user_input: dict | None = None) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input or USER_INPUT
    )


async def test_user_flow_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    result = await _submit(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT
    assert PROFILE in result["title"]

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    agent = next(iter(entry.subentries.values()))
    assert agent.data[CONF_PROMPT] == DEFAULT_PROMPT


async def test_trailing_slash_is_stripped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A pasted URL with a trailing slash must not double up the path."""
    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    result = await _submit(hass, {**USER_INPUT, CONF_BASE_URL: f"{BASE_URL}/"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BASE_URL] == BASE_URL


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HTTPStatus.UNAUTHORIZED, "invalid_auth"),
        (HTTPStatus.INTERNAL_SERVER_ERROR, "cannot_connect"),
    ],
)
async def test_error_responses(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: HTTPStatus,
    expected: str,
) -> None:
    aioclient_mock.get(MODELS_URL, status=status)
    result = await _submit(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(MODELS_URL, exc=TimeoutError)
    result = await _submit(hass)

    assert result["errors"] == {"base": "cannot_connect"}


async def test_malformed_model_payload_is_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 response with the wrong JSON shape must stay inside the flow."""
    aioclient_mock.get(MODELS_URL, json=[])

    result = await _submit(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_non_string_model_ids_are_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(MODELS_URL, json={"data": [{"id": 42}, {"id": "hermes-agent"}]})
    client = HermesClient(hass, BASE_URL, PROFILE, API_KEY)

    assert await client.async_list_models() == ["hermes-agent"]


async def test_duplicate_profile_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    MockConfigEntry(
        domain=DOMAIN, data=USER_INPUT, unique_id=f"{BASE_URL}#{PROFILE}"
    ).add_to_hass(hass)

    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    result = await _submit(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_second_profile_on_same_gateway_allowed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Profiles are separate entries; one gateway hosts several."""
    MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, CONF_PROFILE: "wife"},
        unique_id=f"{BASE_URL}#wife",
    ).add_to_hass(hass)

    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    result = await _submit(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth_updates_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=USER_INPUT, unique_id=f"{BASE_URL}#{PROFILE}"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.get(MODELS_URL, json=DEFAULT_MODELS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "sk-rotated"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "sk-rotated"


async def test_reconfigure_changes_profile_in_place(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Switching profile must not require deleting the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=USER_INPUT, unique_id=f"{BASE_URL}#{PROFILE}"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["step_id"] == "reconfigure"

    aioclient_mock.get(f"{BASE_URL}/p/wife/v1/models", json=DEFAULT_MODELS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_PROFILE: "wife"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PROFILE] == "wife"


async def test_reconfigure_onto_existing_profile_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reconfiguring onto a profile another entry already owns must not merge."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=USER_INPUT, unique_id=f"{BASE_URL}#{PROFILE}"
    )
    entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, CONF_PROFILE: "wife"},
        unique_id=f"{BASE_URL}#wife",
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    aioclient_mock.get(f"{BASE_URL}/p/wife/v1/models", json=DEFAULT_MODELS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_PROFILE: "wife"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_PROFILE] == PROFILE
