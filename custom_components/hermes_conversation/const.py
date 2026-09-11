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

# Hermes takes no commands on a continued session and the session id lives
# here, so a satellite can only leave a session by saying one of these as the
# whole utterance. Compared after lowercasing and trimming punctuation.
RESET_PHRASES: Final = frozenset(
    {"new session", "start over", "reset", "slash new", "/new"}
)
RESET_TRIM: Final = " .,!?"
RESET_REPLY: Final = "Starting a new session."

# Pre-filled for new agents as a worked example of the template variables;
# spoken replies need different manners from typed ones. The editor previews
# the template with no variables, so each one is guarded with "is defined"
# or "default" to keep the preview free of undefined-variable warnings.
_PROMPT_LEAD: Final = (
    "You are the voice of the house at {{ ha_name | default('') }}. Your reply "
    "is spoken aloud, so answer in one or two short plain sentences with no "
    "lists, markdown, or emoji. Do not narrate what you are doing.",
    "{% if user_name is defined and user_name %}"
    "You are talking to {{ user_name }}.{% endif %}",
    "{% if satellite_id is defined and satellite_id %}The request came from "
    "{{ satellite_name or satellite_id }}"
    "{% if area_name %} in the {{ area_name }}{% endif %}. "
    'When a command names no room, assume the {{ area_name or "same" }} area. '
    "Send any announcements to {{ satellite_id }}.{% endif %}",
)
_ANSWER_FROM_SNAPSHOT: Final = (
    "Each message starts with the current time. The state of the house is "
    "listed at the end of these instructions, as of the time shown there. "
    "Answer from it directly unless the answer depends on something that "
    "changes quickly, then check with a tool. Call tools to act, and confirm "
    "briefly after acting."
)
DEFAULT_PROMPT: Final = "\n".join((*_PROMPT_LEAD, _ANSWER_FROM_SNAPSHOT))

# Earlier shipped defaults. An upgrade replaces a prompt still matching one
# of them and leaves anything else alone.
_UNGUARDED_LEAD: Final = (
    "You are the voice of the house at {{ ha_name }}. Your reply is spoken "
    "aloud, so answer in one or two short plain sentences with no lists, "
    "markdown, or emoji. Do not narrate what you are doing.",
    "{% if user_name %}You are talking to {{ user_name }}.{% endif %}",
    "{% if satellite_id %}The request came from "
    "{{ satellite_name or satellite_id }}"
    "{% if area_name %} in the {{ area_name }}{% endif %}. "
    'When a command names no room, assume the {{ area_name or "same" }} area. '
    "Send any announcements to {{ satellite_id }}.{% endif %}",
)
PROMPT_BEFORE_HOUSE_STATE: Final = "\n".join(
    (
        *_UNGUARDED_LEAD,
        "Use the Home Assistant tools to check state before answering questions "
        "about the house, and confirm briefly after acting.",
    )
)
_ANSWER_FROM_BLOCK: Final = (
    "The current time and the state of the house are listed at the end of "
    "these instructions. Answer from them directly. Call tools only to act "
    "or when what you need is not listed, and confirm briefly after acting."
)
PROMPT_BEFORE_GUARDS: Final = "\n".join((*_UNGUARDED_LEAD, _ANSWER_FROM_BLOCK))
PROMPT_BEFORE_SNAPSHOT: Final = "\n".join((*_PROMPT_LEAD, _ANSWER_FROM_BLOCK))
SUPERSEDED_PROMPTS: Final = (
    PROMPT_BEFORE_HOUSE_STATE,
    PROMPT_BEFORE_GUARDS,
    PROMPT_BEFORE_SNAPSHOT,
)

# Appended after the prompt: the state of every entity exposed to Assist, so
# simple questions need no tool call. Hermes sends the system message ahead of
# the tool schemas and the whole transcript, so any change to it re-bills all
# of them; a session reuses one snapshot, retaken once it is this old.
CONF_HOUSE_STATE: Final = "house_state"
DEFAULT_HOUSE_STATE: Final = True
HOUSE_SNAPSHOT_MAX_AGE: Final = 10 * SECONDS_PER_MINUTE

# Config entry minor versions: 2 introduced CONF_HOUSE_STATE on agents; 3 and
# 4 each refreshed the default prompt.
MINOR_VERSION_HOUSE_STATE: Final = 2
MINOR_VERSION_DEFAULT_PROMPT: Final = 4
CONFIG_MINOR_VERSION: Final = MINOR_VERSION_DEFAULT_PROMPT

# Whole domains the agent may never write to. Alarm panels are included even
# though today's Assist tools cannot reach them: the boundary should already
# hold if Home Assistant adds an arming or disarming intent later.
FORBIDDEN_DOMAINS: Final = ("lock", "alarm_control_panel")
FORBIDDEN_COVER_CLASSES: Final = ("door", "garage")

# The Home Assistant user Hermes signs in as, kept read-only on locks and
# doors through a user group; NO_USER leaves the user's groups alone.
CONF_HERMES_USER: Final = "hermes_user"
NO_USER: Final = "none"

ISSUE_POLICY_UNSUPPORTED: Final = "policy_unsupported"
ISSUE_POLICY_USER_ADMIN: Final = "policy_user_admin"
ISSUE_POLICY_USER_GROUPS: Final = "policy_user_groups"
ISSUE_POLICY_USER_MISSING: Final = "policy_user_missing"
