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
Hermes to be able to act.

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
key. Under an entry you can add several agents, each with its own model, system
prompt and timeout, and edit any of it later without re-adding anything.

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
| ✅ | Agents configurable in the UI: model, system prompt, timeout |
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
