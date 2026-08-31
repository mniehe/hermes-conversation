"""Conversation entity backed by a Hermes profile."""

from __future__ import annotations

import logging
from typing import Literal, override

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HermesConfigEntry
from .client import HermesAuthError, HermesConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_PROFILE,
    DEFAULT_MODEL,
    DOMAIN,
    MANUFACTURER,
    MODEL_NAME,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HermesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Hermes conversation entity."""
    async_add_entities([HermesConversationEntity(entry)])


class HermesConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """A Home Assistant conversation agent backed by Hermes."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: HermesConfigEntry) -> None:
        """Initialize the Hermes conversation entity."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL_NAME,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data[CONF_BASE_URL],
            serial_number=entry.data[CONF_PROFILE],
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
        messages = [
            {"role": content.role, "content": content.content}
            for content in chat_log.content
            if isinstance(
                content, conversation.UserContent | conversation.AssistantContent
            )
            and content.content
        ]

        try:
            answer = await self.entry.runtime_data.async_chat(DEFAULT_MODEL, messages)
        except HermesAuthError as err:
            # Raising ConfigEntryAuthFailed here would not reach HA's reauth
            # machinery: async_converse catches it as a plain HomeAssistantError
            # and turns it into an error response.
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except HermesConnectionError as err:
            _LOGGER.debug("Hermes request failed: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from err

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=answer)
        )
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
