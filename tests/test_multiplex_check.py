"""With multiplexing off, Hermes ignores the profile prefix and says nothing."""

from http import HTTPStatus

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_conversation.client import PROBE_PROFILE
from custom_components.hermes_conversation.const import DOMAIN, ISSUE_PROFILE_IGNORED

from .const import BASE_URL, DEFAULT_MODELS

PROBE_URL = f"{BASE_URL}/p/{PROBE_PROFILE}/v1/models"


def _issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_PROFILE_IGNORED)


async def test_no_issue_when_prefix_is_honoured(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    """A rejected nonexistent profile proves multiplexing is on."""
    aioclient_mock.get(PROBE_URL, status=HTTPStatus.NOT_FOUND)
    await load_entry()

    assert _issue(hass) is None


async def test_issue_when_prefix_is_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    """A nonexistent profile answering 200 means every request hits the default."""
    aioclient_mock.get(PROBE_URL, json=DEFAULT_MODELS)
    await load_entry()

    issue = _issue(hass)
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
    await load_entry()

    assert _issue(hass) is None


async def test_issue_clears_when_multiplexing_is_enabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, load_entry
) -> None:
    aioclient_mock.get(PROBE_URL, json=DEFAULT_MODELS)
    entry = await load_entry()
    assert _issue(hass) is not None

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/p/home-assist/v1/models", json=DEFAULT_MODELS)
    aioclient_mock.get(PROBE_URL, status=HTTPStatus.NOT_FOUND)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass) is None
