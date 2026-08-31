"""With multiplexing off, Hermes ignores the profile prefix and says nothing."""

from http import HTTPStatus
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation import _async_check_profile_routing
from custom_components.hermes_conversation.client import PROBE_PROFILE, HermesClient
from custom_components.hermes_conversation.const import (
    CONF_PROFILE,
    DOMAIN,
    ISSUE_PROFILE_IGNORED,
)

from .const import BASE_URL, DEFAULT_MODELS

PROBE_URL = f"{BASE_URL}/p/{PROBE_PROFILE}/v1/models"


def _issue(hass: HomeAssistant, entry_id: str):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_PROFILE_IGNORED}_{entry_id}"
    )


async def test_no_issue_when_prefix_is_honoured(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    """A rejected nonexistent profile proves multiplexing is on."""
    aioclient_mock.get(PROBE_URL, status=HTTPStatus.NOT_FOUND)
    entry = await load_entry()

    assert _issue(hass, entry.entry_id) is None


async def test_issue_when_prefix_is_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    """A nonexistent profile answering 200 means every request hits the default."""
    aioclient_mock.get(PROBE_URL, json=DEFAULT_MODELS)
    entry = await load_entry()

    issue = _issue(hass, entry.entry_id)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


@pytest.mark.parametrize("failure", [HTTPStatus.UNAUTHORIZED, "timeout"])
async def test_probe_failure_stays_quiet(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry, failure
) -> None:
    """An inconclusive probe must not nag; only a positive result warns."""
    if failure == "timeout":
        aioclient_mock.get(PROBE_URL, exc=TimeoutError)
    else:
        aioclient_mock.get(PROBE_URL, status=failure)
    entry = await load_entry()

    assert _issue(hass, entry.entry_id) is None


async def test_issue_clears_when_multiplexing_is_enabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    aioclient_mock.get(PROBE_URL, json=DEFAULT_MODELS)
    entry = await load_entry()
    assert _issue(hass, entry.entry_id) is not None

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/p/home-assist/v1/models", json=DEFAULT_MODELS)
    aioclient_mock.get(PROBE_URL, status=HTTPStatus.NOT_FOUND)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry.entry_id) is None


async def test_profile_issues_are_independent(hass: HomeAssistant) -> None:
    """A healthy profile must not clear another profile's routing warning."""
    ignored = MockConfigEntry(
        domain=DOMAIN, data={CONF_PROFILE: "ignored"}, title="Ignored"
    )
    healthy = MockConfigEntry(
        domain=DOMAIN, data={CONF_PROFILE: "healthy"}, title="Healthy"
    )
    ignored_client = AsyncMock(spec=HermesClient)
    ignored_client.async_profile_prefix_honoured.return_value = False
    healthy_client = AsyncMock(spec=HermesClient)
    healthy_client.async_profile_prefix_honoured.return_value = True

    await _async_check_profile_routing(hass, ignored_client, ignored)
    await _async_check_profile_routing(hass, healthy_client, healthy)

    assert _issue(hass, ignored.entry_id) is not None
    assert _issue(hass, healthy.entry_id) is None


async def test_profile_issue_is_removed_with_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    aioclient_mock.get(PROBE_URL, json=DEFAULT_MODELS)
    entry = await load_entry()
    assert _issue(hass, entry.entry_id) is not None

    await hass.config_entries.async_remove(entry.entry_id)

    assert _issue(hass, entry.entry_id) is None


async def test_legacy_global_issue_is_removed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_PROFILE_IGNORED,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PROFILE_IGNORED,
        translation_placeholders={"profile": "old"},
    )
    aioclient_mock.get(PROBE_URL, status=HTTPStatus.NOT_FOUND)

    await load_entry()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_PROFILE_IGNORED) is None
