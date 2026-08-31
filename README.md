# Hermes Conversation

A [Home Assistant][ha] conversation agent backed by a [Hermes][hermes] profile.

Talk to Hermes from Home Assistant — voice pipeline, Assist dialog, or an
automation — and let Hermes act on your house from any of its channels.

> **Status:** v0.1.0. Streaming chat plus restricted house control over MCP.

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

**a. Make a dedicated Home Assistant user.** *Settings → People → Add person*,
with "Allow person to login" on and **administrator off**. Log in as them once
and create a long-lived access token from their profile page.

A separate user is worth the two minutes: the logbook then attributes agent
actions to it, and disabling the user cuts the agent off without touching your
own session.

**b. Add the MCP server.** *Settings → Devices & Services → Add Integration →
Model Context Protocol Server*. When it asks which API to expose, choose
**"Assist (locks and doors withheld)"** — the one this integration adds.

> Choosing plain **Assist** gives the agent unrestricted control, including
> unlocking doors. Nothing errors if you do; this integration raises a repair
> warning instead of failing quietly.

**c. Point Hermes at it.** In the profile's config:

```yaml
mcp_servers:
  home_assistant:
    url: "http://<home-assistant>:8123/api/mcp"
    headers:
      Authorization: "Bearer ${HA_TOKEN}"
```

Use the bare `/api/mcp`. The `/api/mcp/<api_id>` form requires an *administrator*
token for anything other than Assist, which would undo the dedicated user.

**d. Expose what it may see.** *Settings → Voice assistants → Expose*. The agent
can only see and act on exposed entities.

## What the agent cannot do

Writes to **locks**, **door and garage covers**, and **alarm disarm** are refused.
This is not configurable — changing it is a code change and a release, which is
the right amount of friction for a front door.

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
  resolves. **With it off the prefix is silently ignored and every request lands
  on the default profile**, so the config flow warns when it cannot tell the two
  apart.

## Roadmap

| | |
|---|---|
| ✅ | Streaming chat, per-profile entries, reauth and reconfigure |
| ✅ | Agents configurable in the UI: model, system prompt, timeout |
| ✅ | Restricted LLM API for MCP, with locks and doors withheld |
| ✅ | Diagnostics and a repair check for the boundary |
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
