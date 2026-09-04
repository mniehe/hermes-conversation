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

import voluptuous as vol
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.components.llm import LLMTools
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import intent, llm
from homeassistant.helpers import issue_registry as ir
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN, FORBIDDEN_COVER_CLASSES, FORBIDDEN_DOMAINS
from .satellites import SATELLITE_DOMAIN, SERVICE_ANNOUNCE, announce_capable

_LOGGER = logging.getLogger(__name__)

RESTRICTED_API_ID = f"{DOMAIN}_restricted"
RESTRICTED_API_NAME = "Assist (locks and doors withheld)"

# The name Home Assistant treats as "no name constraint" when matching.
ALL_ENTITIES = "all"

GUARD_PROMPT = (
    "\n\nYou cannot lock, unlock, open or close doors, garage doors or locks, "
    "and you cannot arm or disarm alarm panels. "
    "You can still report their state. Do not claim to have changed them."
)

REFUSAL = (
    "Refused: this would act on a lock or a door. Locks, doors and garage "
    "doors are never controllable; report their state instead."
)
UNKNOWN_ARGUMENT_REFUSAL = (
    "Refused: the guard does not recognise the argument(s) {names}. Retry without them."
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
    "volume_step",
    "is_volume_muted",
    "mode",
    "humidity",
    "state",
    "media_class",
    "search_query",
    "item",
    "todo_list",
    "status",
    "message",
    "response",
    "hours",
    "minutes",
    "seconds",
    "start_hours",
    "start_minutes",
    "start_seconds",
    "conversation_command",
    "calendar",
    "range",
)
READ_ONLY_TOOLS = (
    "GetLiveContext",
    "GetDateTime",
    "todo_get_items",
    "calendar_get_events",
)

ANNOUNCE_TOOL = "announce"
ANNOUNCE_DESCRIPTION = (
    "Speak a message aloud on one Assist satellite. Use it to notify a room "
    "unprompted; a reply to the person you are talking to is spoken there "
    "already. Satellites that can announce: "
)
NO_SATELLITES = "none right now"

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
        """Wrap every Assist tool in a target guard, and add targeted announcements."""
        assist = await llm.async_get_api(self.hass, llm.LLM_API_ASSIST, llm_context)
        tools: list[llm.Tool] = [GuardedTool(tool) for tool in assist.tools]
        tools.append(AnnounceTool(self.hass))

        return llm.APIInstance(
            api=self,
            api_prompt=assist.api_prompt + GUARD_PROMPT,
            llm_context=llm_context,
            tools=tools,
            custom_serializer=assist.custom_serializer,
        )


class AnnounceTool(llm.Tool):
    """Speak on one satellite, unlike HassBroadcast which speaks on all.

    The MCP server only ever hands Hermes the tool list, never the API prompt,
    so the satellites the model may pick from travel in the description.
    """

    name = ANNOUNCE_TOOL
    parameters = vol.Schema(
        {
            vol.Required("satellite_id"): str,
            vol.Required("message"): vol.All(str, vol.Length(min=1)),
        }
    )

    def __init__(self, hass: HomeAssistant) -> None:
        """List the satellites available at the time the tools are assembled."""
        listed = "; ".join(s.describe() for s in announce_capable(hass))
        self.description = ANNOUNCE_DESCRIPTION + (listed or NO_SATELLITES) + "."

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Announce on the named satellite, refusing one that cannot play it."""
        try:
            args = self.parameters(tool_input.tool_args)
        except vol.Invalid as err:
            return {"error": f"Invalid arguments: {err}"}

        satellites = {s.entity_id: s for s in announce_capable(hass)}
        satellite = satellites.get(args["satellite_id"])
        if satellite is None:
            return {
                "error": f"Unknown satellite {args['satellite_id']}. "
                f"Choose one of: {', '.join(satellites) or NO_SATELLITES}."
            }

        await hass.services.async_call(
            SATELLITE_DOMAIN,
            SERVICE_ANNOUNCE,
            {"message": args["message"]},
            target={"entity_id": satellite.entity_id},
            blocking=True,
            context=llm_context.context,
        )
        return {"success": True, "result": f"Announced on {satellite.name}"}


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
        if self._selects_no_targets():
            return await self._tool.async_call(hass, tool_input, llm_context)

        if unknown := _unknown_arguments(tool_input.tool_args):
            _LOGGER.warning(
                "Refused %s from %s: unclassified argument(s) %s",
                tool_input.tool_name,
                llm_context.platform,
                sorted(unknown),
            )
            return {
                "error": UNKNOWN_ARGUMENT_REFUSAL.format(
                    names=", ".join(sorted(unknown))
                )
            }

        if _targets_forbidden(
            hass, self.name, tool_input.tool_args, llm_context.assistant
        ):
            _LOGGER.warning(
                "Refused %s from %s: the target resolves to a lock or door (%s)",
                tool_input.tool_name,
                llm_context.platform,
                tool_input.tool_args,
            )
            return {"error": REFUSAL}

        return await self._tool.async_call(hass, tool_input, llm_context)

    def _selects_no_targets(self) -> bool:
        """Read-only tools and script tools never choose which entity to act on.

        A script tool runs one fixed script with whatever fields that script
        declares; its arguments are values, never targets. What the script
        does is outside this guard's reach either way.
        """
        return self.name in READ_ONLY_TOOLS or isinstance(self._tool, llm.ActionTool)


def _unknown_arguments(tool_args: dict[str, Any]) -> set[str]:
    """Arguments the guard has not classified as a target or a value.

    They may be target selectors added by a future Assist intent, and cannot
    be allowed to become an accidental boundary bypass.
    """
    return set(tool_args) - set(TARGET_SLOTS) - set(NON_TARGET_SLOTS)


def _targets_forbidden(
    hass: HomeAssistant,
    tool_name: str,
    tool_args: dict[str, Any],
    assistant: str | None,
) -> bool:
    """Return whether this call could resolve to something off limits.

    The question asked is "could these arguments reach a lock or a door?", not
    "did the model name one". The match is built the way Home Assistant builds
    it for the real handler, so the guard sees the same entities the intent
    would act on, including the ones a broad sweep pulls in.
    """
    if _unknown_arguments(tool_args):
        return True
    if not any(slot in tool_args for slot in TARGET_SLOTS):
        return False

    try:
        matched = intent.async_match_targets(
            hass, _handler_constraints(hass, tool_name, tool_args, assistant)
        )
    except Exception:
        # An unresolvable match must not become an accidental allow.
        _LOGGER.exception("Target guard failed; refusing the call")
        return True

    return any(_is_forbidden(state) for state in matched.states)


def _handler_constraints(
    hass: HomeAssistant,
    tool_name: str,
    tool_args: dict[str, Any],
    assistant: str | None,
) -> intent.MatchTargetsConstraints:
    """Build the match the intent handler itself would run.

    Mirrors DynamicServiceIntentHandler: a name of "all" means no name
    constraint, and an intent that only ever acts on its own domain (media
    players, fans, vacuums) only matches that domain, so "pause the living
    room" never trips over a lock in the same room.
    """
    name = tool_args.get("name")
    if name == ALL_ENTITIES:
        name = None

    handler = _service_handler(hass, tool_name)
    domains = _string_values(tool_args.get("domain"))
    if "domain" not in tool_args and handler is not None:
        domains = handler.required_domains

    return intent.MatchTargetsConstraints(
        name=name,
        area_name=tool_args.get("area"),
        floor_name=tool_args.get("floor"),
        domains=domains,
        device_classes=_string_values(tool_args.get("device_class")),
        features=handler.required_features if handler else None,
        states=handler.required_states if handler else None,
        assistant=assistant,
        allow_duplicate_names=True,
    )


def _service_handler(
    hass: HomeAssistant, tool_name: str
) -> intent.DynamicServiceIntentHandler | None:
    """Return the service intent handler behind an Assist tool, if any."""
    for handler in intent.async_get(hass):
        if handler.intent_type == tool_name and isinstance(
            handler, intent.DynamicServiceIntentHandler
        ):
            return handler
    return None


def _is_forbidden(state: State) -> bool:
    if state.domain in FORBIDDEN_DOMAINS:
        return True
    return (
        state.domain == COVER_DOMAIN
        and state.attributes.get(ATTR_DEVICE_CLASS) in FORBIDDEN_COVER_CLASSES
    )


def _string_values(value: Any) -> set[str] | None:
    """Normalize a target selector without treating malformed data as narrowing."""
    if value is None:
        return None
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return None
