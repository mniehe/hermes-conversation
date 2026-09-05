"""Conversation entity backed by a Hermes profile."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Literal, override

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_MODEL, CONF_PROMPT, CONF_TIMEOUT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import template
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HermesConfigEntry
from .client import HermesAuthError, HermesConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_HOUSE_STATE,
    CONF_PROFILE,
    CONF_SESSION_TIMEOUT,
    DEFAULT_HOUSE_STATE,
    DEFAULT_MODEL,
    DEFAULT_SESSION_TIMEOUT,
    DOMAIN,
    MANUFACTURER,
    REQUEST_TIMEOUT,
    SECONDS_PER_MINUTE,
    SUBENTRY_TYPE_CONVERSATION,
)
from .house import clock_line, house_block
from .satellites import area_name
from .session import SessionTracker

_LOGGER = logging.getLogger(__name__)


def _satellite_context(hass: HomeAssistant, satellite_id: str | None) -> dict[str, Any]:
    """Describe the satellite a request came from, for the prompt template."""
    if not satellite_id:
        return {"satellite_id": None, "satellite_name": None, "area_name": None}

    state = hass.states.get(satellite_id)
    return {
        "satellite_id": satellite_id,
        "satellite_name": state.name if state else None,
        "area_name": area_name(hass, satellite_id),
    }


def _append_system(messages: list[dict[str, str]], text: str, separator: str) -> None:
    """Add text to the system message, creating one if the prompt is empty."""
    if messages:
        messages[0]["content"] += f"{separator}{text}"
    else:
        messages.append({"role": "system", "content": text})


async def _transform_stream(
    fragments: AsyncIterator[str],
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Adapt Hermes text fragments to the chat log's delta protocol."""
    yield {"role": "assistant"}
    async for fragment in fragments:
        yield {"content": fragment}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HermesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one conversation entity per configured agent."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_CONVERSATION:
            continue

        async_add_entities(
            [HermesConversationEntity(entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class HermesConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """A Home Assistant conversation agent backed by Hermes."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = True

    def __init__(self, entry: HermesConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the Hermes conversation entity."""
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        idle_minutes = float(
            subentry.data.get(CONF_SESSION_TIMEOUT, DEFAULT_SESSION_TIMEOUT)
        )
        self._sessions = SessionTracker(idle_minutes * SECONDS_PER_MINUTE)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer=MANUFACTURER,
            model=subentry.data.get(CONF_MODEL, DEFAULT_MODEL),
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data[CONF_BASE_URL],
        )

    @property
    @override
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return the supported languages."""
        return MATCH_ALL

    @override
    async def async_added_to_hass(self) -> None:
        """Register the conversation agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Unregister the conversation agent."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @override
    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Send the transcript to Hermes and return its response."""
        messages = await self._async_system_messages(user_input)

        # Hermes keeps the transcript itself while a session is live, so the
        # request carries only the newest turn. Without continuity Hermes is
        # stateless and the chat log is replayed instead.
        session_id: str | None = None
        if self._sessions.enabled:
            origin = (
                user_input.satellite_id
                or user_input.device_id
                or chat_log.conversation_id
            )
            session_id = self._sessions.session_for(origin)
            _LOGGER.debug(
                "Hermes session %s for %s (satellite=%s device=%s)",
                session_id,
                origin,
                user_input.satellite_id,
                user_input.device_id,
            )
            messages.append({"role": "user", "content": user_input.text})
        else:
            messages += [
                {"role": content.role, "content": content.content}
                for content in chat_log.content
                if isinstance(
                    content, conversation.UserContent | conversation.AssistantContent
                )
                and content.content
            ]

        options = self.subentry.data
        stream = self.entry.runtime_data.async_stream_chat(
            options.get(CONF_MODEL, DEFAULT_MODEL),
            messages,
            timeout=int(options.get(CONF_TIMEOUT, REQUEST_TIMEOUT)),
            session_id=session_id,
        )

        try:
            async for _content in chat_log.async_add_delta_content_stream(
                user_input.agent_id, _transform_stream(stream)
            ):
                pass
        except HermesAuthError as err:
            _LOGGER.warning(
                "Hermes rejected the API key for profile %s; requesting reauth",
                self.entry.data[CONF_PROFILE],
            )
            # Raising ConfigEntryAuthFailed here would not reach HA's reauth
            # machinery: async_converse catches it as a plain HomeAssistantError
            # and turns it into an error response.
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except HermesConnectionError as err:
            _LOGGER.warning(
                "Hermes profile %s did not answer: %s",
                self.entry.data[CONF_PROFILE],
                err,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from err

        # A stream of whitespace still produces AssistantContent, so the chat
        # log helper would treat it as a valid — but silent — answer.
        last = chat_log.content[-1]
        if (
            isinstance(last, conversation.AssistantContent)
            and not (last.content or "").strip()
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="empty_response"
            )

        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _async_system_messages(
        self, user_input: conversation.ConversationInput
    ) -> list[dict[str, str]]:
        """Render the configured prompt and any extra prompt from the pipeline."""
        messages: list[dict[str, str]] = []
        if prompt := self.subentry.data.get(CONF_PROMPT):
            user_name: str | None = None
            if user_input.context.user_id and (
                user := await self.hass.auth.async_get_user(user_input.context.user_id)
            ):
                user_name = user.name
            messages.append(
                {
                    "role": "system",
                    "content": template.Template(prompt, self.hass).async_render(
                        {
                            "ha_name": self.hass.config.location_name,
                            "user_name": user_name,
                            "llm_context": user_input.as_llm_context(DOMAIN),
                            **_satellite_context(self.hass, user_input.satellite_id),
                        },
                        parse_result=False,
                    ),
                }
            )

        if user_input.extra_system_prompt:
            _append_system(messages, user_input.extra_system_prompt, "\n")

        # Last on purpose: Hermes puts its own prompt ahead of this message and
        # providers cache by prefix, so only what follows the clock is recomputed.
        tail = [clock_line()]
        if self.subentry.data.get(CONF_HOUSE_STATE, DEFAULT_HOUSE_STATE):
            house = house_block(self.hass)
            _LOGGER.debug("House state block is %d characters", len(house))
            tail.append(house)
        _append_system(messages, "\n".join(tail), "\n\n")

        return messages
