"""Constants for Hermes Conversation."""

from typing import Final

DOMAIN: Final = "hermes_conversation"

CONF_API_KEY: Final = "api_key"
CONF_BASE_URL: Final = "base_url"
CONF_PROFILE: Final = "profile"

DEFAULT_BASE_URL: Final = "http://homeassistant.local:8642"
DEFAULT_PROFILE: Final = "home-assist"

# Long enough for an agent turn that calls tools; the gateway itself has no
# ceiling, so an unbounded request would hang a voice pipeline indefinitely.
REQUEST_TIMEOUT: Final = 120
VALIDATE_TIMEOUT: Final = 10

MANUFACTURER: Final = "Nous Research"
MODEL_NAME: Final = "Hermes Agent"

# /v1/models advertises a stable virtual model that means "the profile's own
# default"; anything else overrides the model for that turn.
DEFAULT_MODEL: Final = "hermes-agent"

SUBENTRY_TYPE_CONVERSATION: Final = "conversation"
DEFAULT_CONVERSATION_NAME: Final = "Hermes"

ISSUE_PROFILE_IGNORED: Final = "profile_ignored"

MIN_TIMEOUT: Final = 10
MAX_TIMEOUT: Final = 300

SESSION_ID_HEADER: Final = "X-Hermes-Session-Id"

# Minutes a satellite may stay quiet before its next request starts a fresh
# Hermes session. 0 disables continuity: every turn is a new session and the
# Home Assistant chat log is replayed in the request instead.
CONF_SESSION_TIMEOUT: Final = "session_timeout"
DEFAULT_SESSION_TIMEOUT: Final = 5
MIN_SESSION_TIMEOUT: Final = 0
MAX_SESSION_TIMEOUT: Final = 1440
SECONDS_PER_MINUTE: Final = 60

# Pre-filled for new agents as a worked example of the template variables;
# spoken replies need different manners from typed ones.
DEFAULT_PROMPT: Final = "\n".join(
    (
        "You are the voice of the house at {{ ha_name }}. Your reply is spoken "
        "aloud, so answer in one or two short plain sentences with no lists, "
        "markdown, or emoji. Do not narrate what you are doing.",
        "{% if user_name %}You are talking to {{ user_name }}.{% endif %}",
        "{% if satellite_id %}The request came from "
        "{{ satellite_name or satellite_id }}"
        "{% if area_name %} in the {{ area_name }}{% endif %}. "
        'When a command names no room, assume the {{ area_name or "same" }} area. '
        "Send any announcements to {{ satellite_id }}.{% endif %}",
        "Use the Home Assistant tools to check state before answering questions "
        "about the house, and confirm briefly after acting.",
    )
)
