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

MIN_TIMEOUT: Final = 10
MAX_TIMEOUT: Final = 300
