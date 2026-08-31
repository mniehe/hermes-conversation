"""Chat-only Hermes conversation entity."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_API_KEY, CONF_BASE_URL, DOMAIN, MODEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Hermes conversation entity."""
    async_add_entities([HermesConversationEntity(hass, entry)])


class HermesConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """A Home Assistant conversation agent backed by Hermes."""

    _attr_has_entity_name = True
    _attr_name = "Hermes home-assist"
    _attr_unique_id = DOMAIN

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the Hermes conversation entity."""
        self.hass = hass
        self.entry = entry

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return the supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register the conversation agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the conversation agent."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Send the transcript to Hermes and return its final text response."""
        messages: list[dict[str, str]] = []
        messages.extend(
            {"role": content.role, "content": content.content}
            for content in chat_log.content
            if content.role in {"user", "assistant"}
            and isinstance(content.content, str)
            and content.content
        )
        headers = {
            "Authorization": f"Bearer {self.entry.data[CONF_API_KEY]}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
        }
        base_url = self.entry.data[CONF_BASE_URL].rstrip("/")

        try:
            async with asyncio.timeout(120):
                async with async_get_clientsession(self.hass).post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
            answer = data["choices"][0]["message"]["content"].strip()
            if not answer:
                raise ValueError("Hermes returned an empty response")
        except (TimeoutError, aiohttp.ClientError, KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Hermes conversation request failed: %s", type(err).__name__)
            answer = "Sorry, Hermes is unavailable right now."

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id,
                content=answer,
            )
        )
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(answer)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=user_input.conversation_id,
            continue_conversation=answer.rstrip().endswith("?"),
        )
