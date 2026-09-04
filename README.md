# Hermes Conversation

A [Home Assistant][ha] conversation agent backed by a [Hermes][hermes] profile.

Talk to Hermes from Home Assistant — voice pipeline, Assist dialog, or an
automation — and let Hermes act on your house from any of its channels.

> **Status:** Streaming chat plus restricted house control over MCP.

This integration supplies the conversation stage of an existing Home Assistant
Assist pipeline. Speech-to-text, text-to-speech, wake-word detection and voice
satellite hardware are provided by separate Home Assistant integrations.

## How it works

Home Assistant and Hermes each drive one direction, over a different protocol:

```
                 ┌──────────────── Home Assistant ─────────────────┐
 voice / Assist ─┼─▶ conversation entity ─── text+SSE ──▶ Hermes   │
                 │                                                 │
 Telegram ───────┼──▶ Hermes ─── MCP ──▶ mcp_server ──▶ restricted │
                 │                       (built-in)      LLM API   │
                 └─────────────────────────────────────────────────┘
```

Hermes' OpenAI-compatible endpoint runs its own agent loop and ignores tools sent
by a client, so Home Assistant cannot hand it tools the way the core LLM
integrations do. Instead Hermes reaches Home Assistant through HA's built-in
**Model Context Protocol Server**, which means house control works from *every*
Hermes channel — Telegram included — not just a voice pipeline.

The full reasoning, with citations, is in [DESIGN.md](DESIGN.md).

## Setup

There are two halves, and they are independent: the conversation agent works on
its own, and so does house control. Do the first, add the second when you want
Hermes to be able to act; targeted announcements come with the second.

### 1. Talking to Hermes from Home Assistant

Add this repository to [HACS][hacs] as a custom repository of category
**Integration**, install it, restart Home Assistant, then add **Hermes
Conversation** from *Settings → Devices & Services*.

You will need:

| | |
|---|---|
| Gateway URL | The root, e.g. `http://10.0.0.3:8642` — **not** the `/p/<profile>/v1` path |
| Profile | e.g. `home-assist` |
| API key | That profile's `API_SERVER_KEY` |

Each profile is its own config entry, because each authenticates with its own
key. Under an entry you can add several agents, and edit any of them later
without re-adding anything.

#### Agent options

| Option | Default | What it does |
|---|---|---|
| Name | `Hermes` | The entity name in Home Assistant |
| Model | *(profile name)* | Which of the models the profile advertises this agent uses; the entry named after the profile means its own default, see below |
| System prompt | *(voice example below)* | Prepended to every conversation; a template, see below |
| Timeout | 120 s | How long to wait for a reply before giving up |
| Session idle timeout | 5 min | How long a satellite may stay quiet before its next request starts a fresh Hermes session; `0` starts a new session on every turn |

**Model choices.** The Model list is whatever the profile advertises on
`/v1/models`, and by default that is only the profile's own name, meaning "the
profile's default model". Hermes deliberately ignores
any other model name a client sends, so the list is not free text. To offer a
choice per agent, define aliases on the Hermes gateway; they appear in the list
and each pins a provider and model:

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

Under `gateway.multiplex_profiles` the default profile owns the API server, so
the aliases live in its config and are shared by every profile. A routed turn
still uses the profile's `fallback_providers` if the pinned provider fails.

**Sessions.** Every satellite (falling back to the device, then to Home
Assistant's own conversation) gets its own Hermes session, and consecutive
turns continue it. Hermes then keeps the transcript itself, so each request
carries only the new turn and the model's prompt cache stays warm. When a
satellite has been idle longer than the session timeout, its next request
starts over. Reloading the integration or editing the agent also starts over.
With the timeout at `0`, Hermes is stateless and the Home Assistant chat log is
sent with every request instead.

The Assist dialog in the Home Assistant UI has no satellite or device, so it
keys on the dialog's own conversation: the dialog continues one session until
it is closed or goes idle.

**Prompt template variables.**

| Variable | Value |
|---|---|
| `ha_name` | The Home Assistant location name |
| `user_name` | The name of the user who spoke, if known |
| `satellite_id` | The `assist_satellite` entity the request came from, e.g. `assist_satellite.kitchen` |
| `satellite_name` | That satellite's friendly name |
| `area_name` | The satellite's area, or its device's area |

Voice requests carry the satellite; text chat from the Home Assistant UI does
not, and the satellite variables render as `None`. New agents start with this
prompt, which keeps spoken replies short and tells Hermes which satellite and
room the request came from:

```jinja
You are the voice of the house at {{ ha_name }}. Your reply is spoken aloud, so answer in one or two short plain sentences with no lists, markdown, or emoji. Do not narrate what you are doing.
{% if user_name %}You are talking to {{ user_name }}.{% endif %}
{% if satellite_id %}The request came from {{ satellite_name or satellite_id }}{% if area_name %} in the {{ area_name }}{% endif %}. When a command names no room, assume the {{ area_name or "same" }} area. Send any announcements to {{ satellite_id }}.{% endif %}
Use the Home Assistant tools to check state before answering questions about the house, and confirm briefly after acting.
```

### 2. Letting Hermes control the house

Hermes' OpenAI-compatible endpoint runs its own agent loop and ignores tools
supplied by a client, so control does not travel over the conversation. It goes
the other way, over MCP, which means it works from **every** Hermes channel —
Telegram included — not just a Home Assistant voice pipeline.

**a. Make a dedicated Home Assistant user.**

Give the agent its own account rather than reusing yours. It costs two minutes
and buys three things: the logbook and history attribute every action to
*Hermes*, so you can tell the agent's changes from your own; Home Assistant
refuses admin-only surfaces to a non-admin account before this integration's own
policy runs at all; and disabling that one user cuts the agent off instantly
without touching your session.

*Settings → People → Add person*

1. **Name** it something you will recognise in the logbook — `Hermes`.
2. Turn on **Allow login**. An *Add user* dialog opens.
3. Set a **username** and a long random **password**. You will use it once, so
   generate it rather than choose it; a password manager entry is enough.
4. Turn on **Local access only** if Hermes runs on your LAN. It does in the
   reference setup, and this stops the account being usable from outside your
   network at all.
5. Leave **Administrator** **off**. This is the point of the exercise — an admin
   token would let the agent reconfigure Home Assistant itself.
6. Select **Create**, then **Add**.

**b. Create its long-lived access token.**

Tokens belong to whoever is signed in, so this has to be done *as that user*.
Open a private browser window and sign in as `Hermes`, then:

*User profile* (your name, bottom-left) *→ Security → Long-lived access tokens →
Create token*

Name it after what will hold it — `hermes-agent` — and copy the token
immediately. Home Assistant shows it once and never again.

Long-lived tokens do not expire, so treat it like a password: put it straight
into your secret store and don't paste it into a shell that keeps history. If it
leaks, delete it from this same screen and the agent loses access at once.

> A non-admin account is defence in depth and the audit trail — it is **not** the
> lock boundary. Home Assistant's per-user permissions are too coarse to express
> "everything except unlocking". What actually stops the agent opening your front
> door is the restricted API in step **c** below.

**c. Add the MCP server.** *Settings → Devices & Services → Add Integration →
Model Context Protocol Server*. When it asks which API to expose, choose
**"Assist (locks and doors withheld)"** — the one this integration adds.

> Choosing plain **Assist** gives the agent unrestricted control, including
> unlocking doors. Nothing errors if you do; this integration raises a repair
> warning instead of failing quietly.

**d. Point Hermes at it.** In the profile's config:

```yaml
mcp_servers:
  home_assistant:
    url: "http://<home-assistant>:8123/api/mcp"
    headers:
      Authorization: "Bearer ${HA_TOKEN}"
```

`HA_TOKEN` is the token from step **b**; set it in whatever holds your Hermes
secrets rather than inlining it here.

Use the bare `/api/mcp`. The `/api/mcp/<api_id>` form requires an *administrator*
token for anything other than Assist, which would undo the dedicated user.

**e. Expose what it may see.** *Settings → Voice assistants → Expose*. The agent
can only see and act on exposed entities.

### 3. Speaking on one satellite

Home Assistant's own `HassBroadcast` tool speaks on every satellite at once and
takes no target. This integration adds an `announce` tool alongside it that
takes a `satellite_id` and a `message`, so Hermes can say "the laundry is done"
in the kitchen and nowhere else, whether the trigger was a voice request, a
Telegram message, or a cron job.

The tool's description carries the list of satellites that accept
announcements, each with its name and area, refreshed every time Hermes
connects to the MCP server. Hermes cannot fetch MCP prompts, so the list lives
on the tool rather than in the API prompt. Satellites do not need to be
exposed to Assist for this; they are output devices, and the tool refuses any
id that is not on its list. VoIP phones are left out, matching Home
Assistant's broadcast behaviour.

Together with the `satellite_id` prompt variable this closes the loop: the
default prompt tells Hermes which satellite asked, and the tool lets it reply
there later.

## What the agent cannot do

Writes to **locks**, **door and garage covers**, and **alarm panels** are
refused. This is not configurable — changing it is a code change and a release,
which is the right amount of friction for a front door.

Home Assistant uses one tool for opposite intentions: its own prompt says *"Use
HassTurnOn to lock and HassTurnOff to unlock a lock"*. Unlocking a door is
therefore the same call as switching off a lamp, so withholding tools by name
cannot separate them. Instead every call is resolved to its **targets** using
Home Assistant's own matcher, and refused if any is off limits — even when the
model never mentioned a lock. A command broad enough to sweep one in ("turn off
everything in the hallway") is refused rather than partly executed.

Reading is unaffected. "Is the front door locked?" still answers.

The guard fails closed. Every argument an Assist tool accepts is classified as
either a target (area, floor, device, entity, domain, device class) or a value
(brightness, colour, list item, and so on). A call carrying an argument the
guard has never seen is refused rather than guessed at, so a Home Assistant
upgrade that adds a new slot shows up as a refused call until this integration
learns it. The test suite enumerates every registered intent to catch that
before release.

## Checking it works

Hermes never stores or logs the prompt a client sends — it is layered onto the
profile's own system prompt for the duration of each turn — so the satellite
and session details are only visible from the Home Assistant side. Turn on
debug logging for the integration:

```yaml
logger:
  logs:
    custom_components.hermes_conversation: debug
```

Each turn then logs which session it joined and where it came from:

```
Hermes session ha-8e32e955… for assist_satellite.kitchen (satellite=assist_satellite.kitchen device=abc123)
Streaming from profile home-assist with model home-assist (2 messages)
```

A satellite of `None` means the request did not come through a voice pipeline
(the Assist dialog, an automation, a developer-tools call), and the prompt's
satellite variables render empty for that turn.

On the Hermes side, the profile's `logs/agent.log` shows the same session id
and how much transcript Hermes loaded for it. A `history` that grows across
turns means the session is being continued:

```
agent.turn_context: conversation turn: session=ha-8e32e955… platform=api_server history=0 msg='What is on my shopping list?'
agent.turn_context: conversation turn: session=ha-8e32e955… platform=api_server history=4 msg='Lets mark milk complete'
```

If instead every turn is `history=0` with a new id, the satellite is idling past
the session timeout, or the integration was reloaded between turns.

## Requirements

- Home Assistant **2026.8.2** or later
- A reachable Hermes gateway with `gateway.api_server.enabled: true`
- For named profiles: `gateway.multiplex_profiles: true`, so `/p/<profile>/`
  resolves. **With it off, Hermes silently ignores the prefix and every request
  lands on the default profile.** Nothing fails, so this integration probes for
  it on setup and raises a repair warning rather than letting an agent quietly
  answer as the wrong persona.

## Roadmap

| | |
|---|---|
| ✅ | Streaming chat, per-profile entries, reauth and reconfigure |
| ✅ | Agents configurable in the UI: model, system prompt, timeout, session timeout |
| ✅ | Per-satellite session continuity, with the satellite and its area in the prompt |
| ✅ | Targeted announcements: an `announce` tool that speaks on one named satellite |
| ✅ | Restricted LLM API for MCP, with locks and doors withheld |
| ✅ | Diagnostics, and repair checks for the boundary and profile routing |
| ⬜ | `ai_task` support |

## Development

[uv][uv] handles everything; Python 3.14.2 or later is the only prerequisite.

```sh
uv sync              # install dev dependencies
uv run pytest        # tests
uv run ruff check .  # lint
uv run ruff format . # format
uv run mypy custom_components
```

Home Assistant pins its dependency closure exactly and releases monthly, so the
dev dependencies pin `pytest-homeassistant-custom-component` to the release that
matches the Home Assistant version this targets. `uv.lock` is committed to keep
CI reproducible.

A `flake.nix` is included for Nix users — `nix develop` gives you the same
toolchain — but it is entirely optional and nothing depends on it.

## Licence

MIT — see [LICENSE](LICENSE).

[ha]: https://www.home-assistant.io/
[hacs]: https://hacs.xyz/
[hermes]: https://github.com/NousResearch/hermes-agent
[uv]: https://docs.astral.sh/uv/
