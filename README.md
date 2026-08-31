# Hermes Conversation

A [Home Assistant][ha] conversation agent backed by a [Hermes][hermes] profile.

Talk to Hermes from Home Assistant — voice pipeline, Assist dialog, or an
automation — and let Hermes act on your house from any of its channels.

> **Status: early.** v0.1.0 is chat only. See [Roadmap](#roadmap).

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

## Requirements

- Home Assistant **2026.8.2** or later
- A reachable Hermes gateway with `gateway.api_server.enabled: true`
- For multiple profiles: `gateway.multiplex_profiles: true`, so `/p/<profile>/`
  resolves. **With it off the prefix is silently ignored and every request lands
  on the default profile** — which is a miserable thing to debug, so the config
  flow warns when it cannot tell the two apart.

## Installation

Add this repository to [HACS][hacs] as a custom repository of category
**Integration**, install it, restart Home Assistant, then add **Hermes
Conversation** from *Settings → Devices & Services*.

You will need the gateway root URL (for example `http://10.0.0.3:8642` — *not*
the `/p/<profile>/v1` path), the profile name, and that profile's
`API_SERVER_KEY`.

Each Hermes profile is its own config entry, because each profile authenticates
with its own key. Profiles can be changed later without deleting the entry.

## Roadmap

| | |
|---|---|
| ✅ | Chat, streaming, per-profile config entries |
| ⬜ | Conversation subentries: prompt, model, timeout |
| ⬜ | Restricted LLM API for MCP, with locks and doors withheld |
| ⬜ | Diagnostics and repair checks |

Hermes-side setup (the `mcp_servers` block and the dedicated Home Assistant user)
is documented when the restricted LLM API ships, since it is not useful before
then.

## Development

The repo ships a Nix devshell with Python, uv, ruff and mypy:

```sh
nix develop          # or `direnv allow`
uv sync              # install dev dependencies
uv run pytest        # tests
uv run ruff check .  # lint
uv run mypy custom_components
```

Home Assistant pins its dependency closure tightly and releases monthly, so uv
resolves the Python packages from `pyproject.toml` while Nix supplies the
toolchain. `uv.lock` is committed to keep CI reproducible.

## Licence

MIT — see [LICENSE](LICENSE).

[ha]: https://www.home-assistant.io/
[hacs]: https://hacs.xyz/
[hermes]: https://github.com/NousResearch/hermes-agent
