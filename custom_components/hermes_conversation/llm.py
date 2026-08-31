"""A restricted view of the Assist API, exposed to Hermes over MCP.

Home Assistant reuses one tool for opposite intentions: its own prompt says
"Use HassTurnOn to lock and HassTurnOff to unlock a lock". Unlocking a door is
therefore the same tool call as switching off a lamp, and withholding tools by
name cannot separate them. The guard here inspects the *targets* a call would
resolve to, using Home Assistant's own matcher, and refuses the call when any of
them is something the agent must never write to.
"""

from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.components.llm import LLMTools
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent, llm
from homeassistant.helpers import issue_registry as ir
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

RESTRICTED_API_ID = f"{DOMAIN}_restricted"
RESTRICTED_API_NAME = "Assist (locks and doors withheld)"

# Whole domains the agent may never write to. Alarm panels are included even
# though today's Assist tools cannot reach them: the boundary should already
# hold if Home Assistant adds an arming or disarming intent later.
FORBIDDEN_DOMAINS = ("lock", "alarm_control_panel")
FORBIDDEN_COVER_CLASSES = ("door", "garage")

GUARD_PROMPT = (
    "\n\nYou cannot lock, unlock, open or close doors, garage doors or locks, "
    "and you cannot arm or disarm alarm panels. "
    "You can still report their state. Do not claim to have changed them."
)

REFUSAL = (
    "Refused: this would act on a lock or a door, which is not permitted. "
    "Target something else, or name a specific entity."
)

# Slots that steer which entities an intent resolves to.
TARGET_SLOTS = ("name", "area", "floor", "domain", "device_class")
# Current Assist tool arguments that affect an action but never select targets.
# A new argument is refused until it is deliberately classified here.
NON_TARGET_SLOTS = (
    "position",
    "color",
    "temperature",
    "brightness",
    "percentage",
    "volume_level",
    "is_volume_muted",
)
READ_ONLY_TOOLS = ("GetLiveContext", "GetDateTime")

MCP_SERVER_DOMAIN = "mcp_server"
ISSUE_UNRESTRICTED_MCP = "mcp_server_unrestricted"


@callback
def async_check_mcp_server(hass: HomeAssistant) -> None:
    """Warn when Home Assistant's MCP server bypasses this boundary.

    Nothing errors if the MCP server is set to plain Assist — Hermes simply gets
    unrestricted tools and can unlock doors. A security boundary that can be
    switched off by a dropdown must say so rather than fail quietly.
    """
    entries = hass.config_entries.async_entries(MCP_SERVER_DOMAIN)
    if not entries:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_UNRESTRICTED_MCP)
        return

    if all(mcp_entry_is_restricted(entry) for entry in entries):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_UNRESTRICTED_MCP)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_UNRESTRICTED_MCP,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UNRESTRICTED_MCP,
        translation_placeholders={"api_name": RESTRICTED_API_NAME},
    )


def mcp_entry_is_restricted(entry: ConfigEntry) -> bool:
    """Return whether an MCP entry exposes only the restricted API."""
    selected = entry.data.get(CONF_LLM_HASS_API) or []
    if isinstance(selected, str):
        selected = [selected]
    return selected == [RESTRICTED_API_ID]


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: llm.LLMContext, api_id: str
) -> LLMTools | None:
    """Contribute no tools of our own.

    Naming this module ``llm.py`` enrols it as an LLM tools platform, so Home
    Assistant calls this for every API it assembles. We restrict Assist rather
    than adding to it, so there is nothing to contribute — but the hook has to
    exist, or every assembly logs an AttributeError.
    """
    return None


@callback
def async_register_api(hass: HomeAssistant) -> Any:
    """Register the restricted API and return its unregister callback."""
    return llm.async_register_api(hass, HermesRestrictedAPI(hass))


class HermesRestrictedAPI(llm.API):
    """Assist's tools, with writes to locks and doors withheld."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the API."""
        super().__init__(hass=hass, id=RESTRICTED_API_ID, name=RESTRICTED_API_NAME)

    @override
    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Wrap every Assist tool in a target guard."""
        assist = await llm.async_get_api(self.hass, llm.LLM_API_ASSIST, llm_context)

        return llm.APIInstance(
            api=self,
            api_prompt=assist.api_prompt + GUARD_PROMPT,
            llm_context=llm_context,
            tools=[GuardedTool(tool) for tool in assist.tools],
            custom_serializer=assist.custom_serializer,
        )


class GuardedTool(llm.Tool):
    """Delegates to an Assist tool once its targets are known to be permitted."""

    def __init__(self, tool: llm.Tool) -> None:
        """Mirror the wrapped tool's advertised interface."""
        self._tool = tool
        self.name = tool.name
        self.description = tool.description
        self.parameters = tool.parameters

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Refuse calls that would reach a lock or a door."""
        if self.name not in READ_ONLY_TOOLS and _targets_forbidden(
            hass, tool_input.tool_args, llm_context.assistant
        ):
            _LOGGER.warning(
                "Refused %s from %s: the target resolves to a lock or door (%s)",
                tool_input.tool_name,
                llm_context.platform,
                tool_input.tool_args,
            )
            return {"error": REFUSAL}

        return await self._tool.async_call(hass, tool_input, llm_context)


def _targets_forbidden(
    hass: HomeAssistant, tool_args: dict[str, Any], assistant: str | None
) -> bool:
    """Return whether this call could resolve to something off limits.

    The question asked is "could these arguments reach a lock or a door?", not
    "did the model name one". Constraining the match to the forbidden domains
    means an unrelated call finds nothing and passes untouched, while a call
    broad enough to sweep a lock in is refused even when the model never
    mentioned it.
    """
    if set(tool_args) - set(TARGET_SLOTS) - set(NON_TARGET_SLOTS):
        # Unknown arguments may be target selectors added by a future Assist
        # intent. They cannot become an accidental boundary bypass.
        return True
    if not any(slot in tool_args for slot in TARGET_SLOTS):
        return False

    for constraints in _forbidden_constraints(tool_args, assistant):
        try:
            if intent.async_match_targets(hass, constraints).is_match:
                return True
        except Exception:
            # An unresolvable match must not become an accidental allow.
            _LOGGER.exception("Target guard failed; refusing the call")
            return True

    return False


def _forbidden_constraints(
    tool_args: dict[str, Any], assistant: str | None
) -> list[intent.MatchTargetsConstraints]:
    """Build the "would this touch a lock, or a door?" queries."""
    name = tool_args.get("name")
    area = tool_args.get("area")
    floor = tool_args.get("floor")
    requested_domains = _string_values(tool_args.get("domain"))
    requested_classes = _string_values(tool_args.get("device_class"))
    constraints: list[intent.MatchTargetsConstraints] = []

    forbidden_domains = tuple(
        domain
        for domain in FORBIDDEN_DOMAINS
        if requested_domains is None or domain in requested_domains
    )
    if forbidden_domains:
        constraints.append(
            intent.MatchTargetsConstraints(
                name=name,
                area_name=area,
                floor_name=floor,
                domains=forbidden_domains,
                device_classes=(
                    tuple(requested_classes) if requested_classes else None
                ),
                assistant=assistant,
                allow_duplicate_names=True,
            )
        )

    forbidden_cover_classes = tuple(
        device_class
        for device_class in FORBIDDEN_COVER_CLASSES
        if requested_classes is None or device_class in requested_classes
    )
    if (
        requested_domains is None or "cover" in requested_domains
    ) and forbidden_cover_classes:
        constraints.append(
            intent.MatchTargetsConstraints(
                name=name,
                area_name=area,
                floor_name=floor,
                domains=("cover",),
                device_classes=forbidden_cover_classes,
                assistant=assistant,
                allow_duplicate_names=True,
            )
        )

    return constraints


def _string_values(value: Any) -> set[str] | None:
    """Normalize a target selector without treating malformed data as narrowing."""
    if value is None:
        return None
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return None
