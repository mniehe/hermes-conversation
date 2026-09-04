# Hermes Conversation

Talk to a [Hermes][hermes] agent from [Home Assistant][ha], and let Hermes run
your house from any channel it has, with the front door kept out of reach.

## What it does

Two things, in opposite directions:

```mermaid
flowchart LR
    V[Voice satellite / Assist chat] -->|text| E[Conversation agent]
    E -->|streamed reply| V
    E -->|chat request| H[Hermes profile]
    H -->|MCP tools| M[HA MCP server]
    M --> G[Guard: no locks, no doors]
    G --> HA[Lights, lists, covers...]
    T[Telegram, cron, anything Hermes has] --> H
```

1. **Home Assistant talks to Hermes.** The integration adds a conversation agent
   you can put in a voice pipeline or the Assist dialog. It streams the reply
   back as Hermes writes it.
2. **Hermes talks to Home Assistant.** Hermes reaches the house through Home
   Assistant's built-in MCP server, so it can act from Telegram or a cron job
   as easily as from a voice request. Locks and doors are withheld.

Speech-to-text, text-to-speech, wake words and satellites come from your
existing Home Assistant setup; this only supplies the agent.

## Install

### 1. Connect to Hermes

Add this repository to [HACS][hacs] as a custom **Integration**, install it,
restart Home Assistant, then add **Hermes Conversation** from *Settings →
Devices & Services*. You need three things:

| | |
|---|---|
| Gateway URL | The root, e.g. `http://10.0.0.3:8642` (not the `/p/<profile>/v1` path) |
| Profile | e.g. `home-assist` |
| API key | That profile's `API_SERVER_KEY` |

The Hermes side needs `gateway.api_server.enabled: true`, and
`gateway.multiplex_profiles: true` if you use more than one profile. If the
gateway ignores the profile name, the integration raises a repair to tell you.

One entry per profile. Each entry can hold several agents, edited any time.

### 2. Let Hermes act on the house

Optional. Do it when you want Hermes to switch things, not just talk.

1. **Make a user for Hermes.** *Settings → People → Add person*, turn on
   *Allow login*, leave *Administrator* off. The logbook will show what Hermes
   did under that name, and the next step can restrict it.
2. **Create its token.** Sign in as that user in a private window, then
   *Profile → Security → Long-lived access tokens*. Copy it once, store it
   with your Hermes secrets.
3. **Add the MCP server.** *Settings → Devices & Services → Add Integration →
   Model Context Protocol Server*. When asked which API to expose, choose
   **Assist (locks and doors withheld)**. Choosing plain Assist gives Hermes
   everything; the integration raises a repair if you do.
4. **Point Hermes at it**, in the profile's config:

   ```yaml
   mcp_servers:
     home_assistant:
       url: "http://<home-assistant>:8123/api/mcp"
       headers:
         Authorization: "Bearer ${HA_TOKEN}"
   ```

5. **Expose what Hermes may see.** *Settings → Voice assistants → Expose*.
   The tools only see exposed entities. The token itself can still read any
   entity's state through the REST API; exposure limits what the tools act on,
   not what the account can read.
6. **Pick the Hermes user.** Open the integration's *Configure* and choose the
   user from step 1. This puts it in a user group that Home Assistant itself
   keeps read-only on locks and doors. See [How the door stays shut](#how-the-door-stays-shut).

### 3. Talk to it

Set the new agent as the conversation agent of a voice pipeline, or pick it in
the Assist dialog.

## Agent options

| Option | Default | Meaning |
|---|---|---|
| Name | `Hermes` | Entity name |
| Model | the profile | Which model alias the profile advertises, see below |
| System prompt | voice example below | Prepended to every conversation; a template |
| Timeout | 120 s | How long to wait for a reply |
| Session idle timeout | 5 min | Quiet time after which a satellite starts a fresh Hermes session; `0` means a fresh session every turn |

### Prompt

The prompt is a template with these variables:

| Variable | Value |
|---|---|
| `ha_name` | Your Home Assistant's location name |
| `user_name` | Who spoke, if known |
| `satellite_id` | The `assist_satellite` entity the request came from |
| `satellite_name` | Its friendly name |
| `area_name` | Its area, or its device's area |
| `llm_context` | Home Assistant's own request context object; rarely useful in a prompt |

The Assist dialog has no satellite, so the satellite variables render empty there. New
agents start with this:

```jinja
You are the voice of the house at {{ ha_name }}. Your reply is spoken aloud, so answer in one or two short plain sentences with no lists, markdown, or emoji. Do not narrate what you are doing.
{% if user_name %}You are talking to {{ user_name }}.{% endif %}
{% if satellite_id %}The request came from {{ satellite_name or satellite_id }}{% if area_name %} in the {{ area_name }}{% endif %}. When a command names no room, assume the {{ area_name or "same" }} area. Send any announcements to {{ satellite_id }}.{% endif %}
Use the Home Assistant tools to check state before answering questions about the house, and confirm briefly after acting.
```

### Models

The list is what the profile advertises. By default that is one entry named
after the profile, meaning "its own default model". To offer a choice, define
aliases on the gateway; they show up in the list:

```yaml
gateway:
  api_server:
    extra:
      model_routes:
        fast:
          provider: zai
          model: glm-5.3-flash
        smart:
          provider: openai-codex
          model: gpt-5.6-terra
```

A routed turn still falls back through the profile's `fallback_providers`.

### Sessions

Each satellite gets its own Hermes session and keeps it across turns, so
Hermes remembers the conversation and its prompt cache stays warm. A satellite
that goes quiet longer than the idle timeout starts over. The Assist dialog
keys on its own conversation instead.

## Announcing on one satellite

Home Assistant's own broadcast tool speaks on every satellite. This integration
adds an `announce` tool that takes a `satellite_id` and a `message`, so Hermes
can say "the laundry is done" in the kitchen only. The tool's description lists
the satellites that can announce, with their rooms, so Hermes knows the
choices. Together with the prompt variables, Hermes knows where a request came
from and can answer there later.

## How the door stays shut

Locks, alarm panels, and door or garage covers can be read but never
controlled. Two layers do this, and it matters which one you are relying on.

```mermaid
flowchart TB
    subgraph routes["Ways the Hermes token can reach the house"]
        A[MCP, restricted API] --> G[Layer 1: tool guard]
        B[MCP, plain Assist] --> P
        C[REST service call] --> P
        D[Scripts and scenes] --> P
        G --> P[Layer 2: user group policy]
    end
    P --> HA[Home Assistant acts]
```

**Layer 1: the tool guard.** Home Assistant uses one tool for opposite jobs:
`HassTurnOff` switches off a lamp and unlocks a lock. So the guard doesn't look
at tool names. It works out which entities the call would land on, the same
way Home Assistant does, and refuses if any is off limits:

```
on tool call(args):
    if args contain a name the guard has never seen: refuse, naming it
    targets = match(args) exactly as HA's handler would (name "all" = no name)
    if any target is a lock, alarm panel, or door/garage cover: refuse
    else: run the real tool
```

This only sees calls that arrive through the restricted API. The same token
can also call the REST API, the plain Assist API at `/api/mcp/assist`, and run
exposed scripts, none of which pass through it.

**Layer 2: the user group.** Home Assistant checks the calling user's group
policy on every entity action, on every route. Policies can only allow, never
deny, so the integration builds one that allows control of everything except
the forbidden entities and keeps "everything else" read-only:

```
policy:
    domains:    every domain in the house except lock, alarm_control_panel,
                cover and automation                              -> read + control
    entity_ids: every cover that is not a door or garage          -> read + control
    all:                                                          -> read only
```

Automations are withheld because their actions run with no user attached, so
Home Assistant never permission-checks them; letting Hermes trigger one would
be a way around the policy. Scripts and scenes carry Hermes's user and are
checked action by action, so those stay controllable.

The policy is rebuilt when a cover or a new domain appears, the user you
picked is moved into the group and nobody else, and the group is removed
again when no entry manages a user. If an administrator edits that user in
Settings, the integration notices and puts it back. Home Assistant has no UI
for custom groups, so this relies on a few internal names in the auth store;
if a future Home Assistant renames them, a repair tells you and layer 1 keeps
working.

What is still on you:

- Pick the user in the integration's options. Without that, only layer 1
  applies. Only a plain member of the Users group can be picked; admins and
  Viewers are refused, since the move would change what they may do.
- Remove the config entry before uninstalling the integration. Otherwise the
  user stays in a group nobody maintains.
- Services that don't target entities are never permission-checked by Home
  Assistant: `shell_command`, `rest_command`, legacy `notify`, persistent
  notifications. Don't define one that unlocks a door.
- Only device class tells a cover from a door. A gate, a cover with no device
  class wrapping a garage door, a door strike wired to a `switch`, an `update`
  install or a reboot `button` are all controllable. Home Assistant's model
  cannot express those; keep such entities unexposed or off the account.

## Checking it works

Hermes never stores the prompt a client sends, so verify from the Home
Assistant side. Turn on debug logging:

```yaml
logger:
  logs:
    custom_components.hermes_conversation: debug
```

Each turn then logs its session and origin:

```
Hermes session ha-8e32e955… for assist_satellite.kitchen (satellite=assist_satellite.kitchen device=abc123)
```

On the Hermes side, the profile's `logs/agent.log` shows the same session id
and a `history=N` that grows across turns while the session continues.

Diagnostics for the entry report whether the MCP server serves the restricted
API and whether the user-group policy is in force.

## Requirements

- Home Assistant 2026.8.2 or later
- A reachable Hermes gateway with the API server enabled

## Development

[uv][uv] handles everything; Python 3.14.2 or later is the only prerequisite.
A `flake.nix` gives Nix users the same toolchain with `nix develop`.

```sh
uv sync
uv run pytest --cov=custom_components --cov-report=json:coverage.json
uv run python scripts/check_guard_coverage.py
uv run mypy custom_components
uv run ruff check .
```

The guard and the policy module must stay at 100% coverage; a missing branch
there is a hole, not a style problem. The dev dependencies pin the Home
Assistant release this targets, and `uv.lock` is committed.

The original design notes, with citations into Home Assistant's source, are in
[DESIGN.md](DESIGN.md).

## Licence

MIT — see [LICENSE](LICENSE).

[ha]: https://www.home-assistant.io/
[hacs]: https://hacs.xyz/
[hermes]: https://github.com/NousResearch/hermes-agent
[uv]: https://docs.astral.sh/uv/
