# hermes_conversation — Design

Status: **historical.** This is the pre-implementation design, kept for the
reasoning and citations. Where the shipped code differs, the README is right
and this file is not; the known differences are listed in
[§0](#0-where-the-implementation-diverged) so the rest can be read with them
in mind.

## 0. Where the implementation diverged

- **The guard is not the lock boundary for the token.** A non-admin user
  controls every entity through the REST API, and HA serves the unrestricted
  Assist API at `/api/mcp/assist` to any user, so layers 1 and 2 below only
  cover calls made through the restricted API. Scripts and scenes run
  unguarded on their own. `policy.py` now keeps the Hermes user in a
  user group that Home Assistant enforces on every route; the README
  explains both layers.
- **No exposure repair issue.** The "lock is exposed" repair in §3 was never
  built. The repairs that exist are `mcp_server_unrestricted`,
  `profile_ignored`, and the `policy_*` family for the user group.
- **Alarm panels are refused as a whole domain**, not just `alarm_disarm`.
- **Targets are matched once, the way HA's own handler matches them**, with a
  name of "all" meaning no name, then checked against the forbidden set.
- **Runtime 401 does not raise `ConfigEntryAuthFailed`.** `async_converse`
  would swallow it; the entity starts reauth itself and raises a translated
  `HomeAssistantError`.
- **Model is a fixed list** (`custom_value=False`) of what the profile
  advertises, defaulting to the first entry; the prompt default is the
  integration's own voice prompt, and there is a `session_timeout` option.
- **`async_provide_llm_data` is not used.** The prompt is rendered directly.
- **Extra modules:** `session.py` (per-origin Hermes sessions) and
  `satellites.py` (announce-capable satellites and areas); there is no
  `entity.py`. Workflows are `validate.yml`, `lint.yml`, `test.yml`.
- **Toolchain:** Python 3.14, tests use `aioclient_mock` rather than
  `aioresponses`.
- **§9 step 5 is optional**, not required; leaving locks exposed keeps state
  reads working and relies on layer 2 for the MCP path.

Verified against:

| Thing | Version | Source |
|---|---|---|
| Home Assistant core | **2026.8.2** (your deployment, `hosts/cougar/services/home-assistant.nix:40`) | PyPI wheel + `raw.githubusercontent.com` at tag `2026.8.2` |
| Hermes agent | **0.20.5** (matches pinned commit `fcbd107`) | `/nix/store/…-hermes-agent-env/lib/python3.12/site-packages` |

Cross-checked against the published docs: `home-assistant.io/integrations/mcp_server`
and `developers.home-assistant.io/docs/core/llm/`. Where the docs and the source
agree they are cited together; where only the docs state something (auth
behaviour, admin gating) they are cited as the authority.

Every claim cites the file it came from. This document is the **second** design;
the first proposed a Hermes-side plugin, which turned out to be unnecessary. The
reasoning for the change is in §2 rather than hidden, because the discarded
version informs what the surviving one must not do.

---

## 1. Two findings that determine the shape

### 1a. Hermes ignores tools sent by a client

`gateway/platforms/api_server.py`, `_handle_chat_completions` (4173–4533). The
complete set of request-body fields it reads:

```python
messages = body.get("messages")
stream = _coerce_request_bool(body.get("stream"), default=False)
model_name = body.get("model", self._model_name)
```

`tools` and `tool_choice` appear once, in the idempotency fingerprint (4433) — so
they alter a cache key and nothing else. The handler calls `_run_agent(...)`:
Hermes runs its own loop with its own tools and returns prose. No `tool_calls` in
the response, none in the stream. `/v1/responses` (5335) and `/v1/runs` (6682)
read no `tools` either.

**So the core-integration pattern — HA sends tool definitions, the model asks for
a call, HA executes it — has no server-side code path.** Not harder. Absent.

### 1b. Both ends already speak MCP

- Home Assistant 2026.8.2 ships **`mcp_server`**, which serves a selected HA "LLM
  API" as MCP tools over `/api/mcp`, authenticated with an ordinary HA bearer
  token. Its `http.py` docstring: *"This serves the configured LLM APIs and does
  not require admin access."* The docs agree: *"The `/api/mcp` endpoint serves the
  LLM API you select when you set up the integration."*
- Hermes already consumes MCP servers with arbitrary auth headers — you run two
  (`hosts/cougar/hermes-vm.nix:230,240`).

The Hermes side of this bridge is therefore three lines of existing
configuration, not a plugin:

```nix
mcp_servers.home_assistant = {
  url = "http://<ha-host>:8123/api/mcp";          # bare — see the footgun below
  headers."Authorization" = "Bearer ${HA_TOKEN}";
};
```

**Footgun, worth getting right the first time.** There are two URL shapes and they
have different auth rules. The docs are explicit: *"Connecting to any API other
than Assist requires the authenticated user to be an administrator."* That gate
applies to the id-addressed form:

| URL | Serves | Admin required? |
|---|---|---|
| `/api/mcp` | whatever API you picked in the config flow | **no** |
| `/api/mcp/assist` | Assist | no |
| `/api/mcp/hermes_restricted` | our API, addressed by id | **yes** |

So point Hermes at the **bare `/api/mcp`** and select the restricted API in the
`mcp_server` config flow. Addressing our API by id would force the token onto an
admin account and undo the whole point of the dedicated non-admin user.

Auth: the docs prefer OAuth (IndieAuth) and treat long-lived tokens as the
fallback for *"MCP clients [that] may not support OAuth"*. Hermes sends a static
header, so the long-lived token is the correct choice here, not a compromise.

---

## 2. Architecture — one HA integration, no Hermes plugin

```
                 ┌──────────────── Home Assistant ─────────────────┐
 voice / Assist ─┼─▶ conversation entity ─── text+SSE ──▶ Hermes   │
                 │                                                 │
 Telegram ───────┼──▶ Hermes ─── MCP ──▶ mcp_server ──▶ restricted │
                 │                        (built-in)     LLM API   │
                 └─────────────────────────────────────────────────┘
```

This repo ships **one** integration doing two independent jobs:

1. **Conversation entity** — HA talks to Hermes. Streaming, so TTS starts before
   the full answer arrives. This is what makes your Voice PE / Whisper / Piper
   pipeline reach Hermes.
2. **A restricted LLM API** (`llm.py`) — Hermes talks to HA, through HA's own
   `mcp_server`. This is the capability boundary.

The two directions are deliberately decoupled: job 2 works for Telegram whether
or not anyone ever speaks to job 1.

### Why the first design's Hermes plugin is gone

It would have re-implemented, in code I maintain, a transport both ends already
have: an HA REST client, tool registration, a policy-fetch endpoint, and a
distribution path onto the VM. All of it replaced by `mcp_servers.home_assistant`.
Deleting it removes an entire deployment story and an entire security surface.

**What is genuinely lost:** nothing functional. The old plugin design's one
advantage was arbitrary service calls (`ha_call_service` on any domain). MCP via
Assist gives intent-level control instead — turn on/off, set position, timers, and
live state. If a future need genuinely requires a service call Assist cannot
express, the honest fix is an `intent` + `llm.py` platform in a small HA
integration, not a Hermes plugin.

---

## 3. The capability boundary

This is the requirement with teeth — *no writes to locks or doors* — and the
naive implementation does not work.

### The trap

`homeassistant/components/intent/llm.py:41-43` states its own prompt:

> "When controlling Home Assistant always call the intent tools.
> **Use HassTurnOn to lock and HassTurnOff to unlock a lock.**"

**Unlocking a door is the same tool call that turns off a lamp.** There is no
`HassUnlock` to withhold. So filtering the tool list by name — which is what my
first design and the obvious reading of "restricted tool set" both imply —
achieves nothing: dropping `HassTurnOff` would remove the ability to turn off
lights and still not be a lock boundary, because `HassTurnOn` locks and some
other phrasing may reach the same service.

Two further facts constrain the fix:

- **Core platforms only contribute to the Assist API.** Every `llm.py` platform
  begins `if api_id != LLM_API_ASSIST: return None` (`intent/llm.py:51`). A
  freshly-registered custom API id therefore receives **no tools at all** from
  core. A custom API must *wrap* Assist, not expect to be populated.
- **Intent targeting is bounded by exposure.** Intent handlers resolve names
  against `async_should_expose(hass, llm_context.assistant, entity_id)`
  (`intent/llm.py:65-67`), so an unexposed entity cannot be named.

### The design

Two layers, in this order, because the first is the one that actually holds.

**Layer 1 — exposure. The real boundary.**
Lock and door/garage-cover entities are not exposed to the assistant. Intent
resolution cannot see them, so no phrasing reaches them. This is HA's built-in
mechanism, enforced in core rather than in my code, and the docs state it plainly:
*"Clients can only control or provide information about entities that are exposed
to it."*

**Which exposure list?** `mcp_server` builds its context with
`assistant=conversation.DOMAIN` (`mcp_server/http.py:148`), so the list that
governs Hermes is the ordinary Settings → Voice assistants → Expose list — the
same one HA's own Assist uses.

**That sharing is a real tradeoff, not a detail.** Unexposing your locks to keep
them away from Hermes also takes them away from HA's built-in voice assistant, so
"Hey Assist, lock the front door" stops working too. If you want to keep that,
leave the locks exposed and rely on layer 2 — which is precisely why layer 2 is
not optional decoration.

The integration does not silently depend on this. On setup and on a repair check
it inspects exposure and raises an HA **repair issue** if a `lock` entity or a
`door`/`garage` cover is exposed to the assistant, naming the entity. A boundary
that depends on a checkbox must say so out loud when the checkbox is wrong.

**Layer 2 — a guarded LLM API. Defence in depth.**
`custom_components/hermes_conversation/llm.py` registers
`HermesRestrictedAPI` via `llm.async_register_api`. It does not build a tool list
from scratch; it borrows Assist's and wraps each tool:

```python
async def async_get_api_instance(self, llm_context):
    assist = await llm.async_get_api(self.hass, llm.LLM_API_ASSIST, llm_context)
    return llm.APIInstance(
        api=self,
        api_prompt=assist.api_prompt + FORBIDDEN_NOTE,
        llm_context=llm_context,
        tools=[GuardedTool(tool, self.hass) for tool in assist.tools],
        custom_serializer=assist.custom_serializer,
    )
```

`GuardedTool.async_call` resolves the call's target before delegating and refuses
when it lands on the `lock` domain, a `door`/`garage` cover, or
`alarm_control_panel.alarm_disarm`. Refusal returns an error to the model, not an
exception — the agent should say "I can't do that", not crash the turn.

Because this inspects **arguments**, not tool names, it survives the `HassTurnOff`
problem that defeats name filtering.

**Reads are unaffected.** Per your wording, this denies writes only. Asking "is
the front door locked?" still works via `GetLiveContext`, which is genuinely
useful and harmless.

Note the two layers pull in opposite directions on exposure, and you get to pick:
unexpose locks and layer 1 does the work but reads and HA's own Assist lose them
too; leave them exposed and layer 2 does the work while reads keep working. The
integration supports both and the repair check (below) only fires in the second
case, as a reminder that layer 2 is now load-bearing.

### What the token buys

The MCP endpoint is reached with a long-lived token from a **dedicated,
non-admin HA user**:

- *Audit* — logbook and history attribute agent actions to `Hermes`, separable
  from you and from your wife.
- *Capability* — HA itself refuses admin-only surfaces to a non-admin user.
- *Access* — disabling the user cuts the agent off without touching your session.

Being straight about the limit: HA's per-user permissions are coarse and cannot
express "everything except unlocking". The dedicated user is audit and
defence-in-depth. **Layers 1 and 2 are the lock boundary.**

---

## 4. Config flow and options

Core moved to **subentries** in this era: the config entry holds the connection,
and each conversation agent is a `ConfigSubentry` with options in
`subentry.data`. Verified in `openai_conversation`, `ollama`, `anthropic` and
`google_generative_ai_conversation` (all four: `if subentry.subentry_type !=
"conversation": continue`), and `config_flow.py:217`
(`async_get_supported_subentry_types`). This supersedes the brief's
`entry.options` model.

### Config entry — one per Hermes profile

Named profiles "authenticate through `/p/<profile>/` with their own
`API_SERVER_KEY`" (`hosts/cougar/hermes-vm.nix:192-193`), each key in its own
sops env file. **Profile and key are one credential and cannot be configured
separately** — so the profile belongs on the entry, beside its key, not on the
subentry.

| Key | Selector | Notes |
|---|---|---|
| `base_url` | `TextSelector(URL)` | Gateway root, e.g. `http://10.0.0.3:8642` — not the `/p/…/v1` path |
| `profile` | `TextSelector` | e.g. `home-assist`; free text, validated by probe |
| `api_key` | `TextSelector(PASSWORD)` | That profile's `API_SERVER_KEY` |

Validated by `GET {base_url}/p/{profile}/v1/models`; `401 → invalid_auth`.
`unique_id = f"{base_url}#{profile}"`, so several profiles coexist as several
entries. Editable in place via entry-level `async_step_reconfigure` +
`async_update_reload_and_abort` (`openai_conversation/config_flow.py:249`) — no
delete-and-re-add. **Reauth** re-prompts for `api_key` only.

No profile-listing endpoint exists, hence free text: `api_server.py` matches the
prefix in `_make_profile_prefix_middleware` (2041) against
`multiplex_profile_allowlist` (2002) but never exposes the list.
`gateway.multiplex_profiles` is already `true` on your box
(`hermes-vm.nix:191`).

**Implemented in v0.2.0, by a better probe than this design proposed.** The
original idea — compare `/p/{profile}/v1/models` against `/v1/models` and warn
when they match — is unsound, because two profiles can legitimately advertise
the same models. Instead the integration requests a deliberately nonexistent
profile. `_resolve_request_profile` (`api_server.py:1974`) returns `None` when
multiplexing is off (prefix ignored, so the request succeeds) and
`_PROFILE_REJECTED` → 404 when it is on. A 200 for a profile that cannot exist
therefore proves the prefix is being ignored. Anything else — 401, a timeout —
is inconclusive and stays silent. Setup is never blocked: a single-profile
gateway is a legitimate configuration, so this is a repair warning naming the
profile, not an error.

### Conversation subentry — one per agent

| Key | Selector | Notes |
|---|---|---|
| `name` | `str` | New subentries only |
| `model` | `SelectSelector(custom_value=True)` | From `GET /p/{profile}/v1/models` |
| `prompt` | `TemplateSelector()` | Default `llm.DEFAULT_INSTRUCTIONS_PROMPT` |
| `timeout` | `NumberSelector` | 10–300 s, default 120 |

**`CONF_LLM_HASS_API` is deliberately absent from the subentry.** HA does not
supply tools to Hermes over this transport (§1a), so the field would be dead UI
implying a capability that does not exist. Tool exposure is configured where it
actually takes effect: in the `mcp_server` integration's own config flow, which
lists every registered API — including ours — via `llm.async_get_apis`
(`mcp_server/config_flow.py:37`).

---

## 5. Request flow

```
_async_handle_message(user_input, chat_log)
  ├─ chat_log.async_provide_llm_data(
  │      user_input.as_llm_context(DOMAIN), None,   # no HA LLM API on this path
  │      subentry.data[CONF_PROMPT], user_input.extra_system_prompt)
  ├─ POST {base_url}/p/{profile}/v1/chat/completions
  │      Authorization: Bearer {api_key}
  │      { model, messages, stream: true }
  ├─ async for _ in chat_log.async_add_delta_content_stream(
  │        self.entity_id, _transform_stream(response)): pass
  └─ return conversation.async_get_result_from_chat_log(user_input, chat_log)
```

**No tool-call loop and no `MAX_TOOL_ITERATIONS`.** `chat_log.unresponded_tool_results`
can never become true, because Hermes never returns a tool call. Copying core's
`for _iteration in range(10)` would be cargo cult — it would break on the first
pass, every time.

Hermes' SSE is verified standard OpenAI (`_write_sse_chat_completion`, 4533+): a
leading `{"delta":{"role":"assistant"}}` chunk then `{"delta":{"content":…}}`
chunks, so `_transform_stream` is a thin rename into HA's
`AssistantContentDeltaDict`.

Errors map to translatable keys, never a hardcoded English sentence: runtime 401
→ `ConfigEntryAuthFailed` (reauth); timeout/`ClientError` → `HomeAssistantError`;
setup probe failure → `ConfigEntryNotReady`.

---

## 6. Corrections

**To my own first design.** It specified a Hermes plugin (`plugin.yaml`,
`tools.py`, `register_tools(ctx)`, an HA REST client, a policy HTTP view, and a
clone-onto-the-VM distribution path). All of it is deleted. I had verified the
plugin API correctly — `ctx.register_tool` exists at `hermes_cli/plugins.py:1705`,
tools load from `tools.py` via `register_tools(ctx)` gated on a `provides_tools`
manifest key (4588) — but verifying *how* to build a thing is not the same as
checking *whether* it is needed. I checked the wrong integration point first.

**To the brief — one item I got wrong in the first pass.** I reported that
`llm.LLMTools` and `async_get_tools` "do not exist in 2026.8.3". **That was
wrong.** They exist in `homeassistant.components.llm` — I had grepped only
`homeassistant.helpers.llm` and a handful of extracted components, and the `llm`
component was not among them. The brief's description was accurate:

```python
class LLMToolsPlatformProtocol(Protocol):
    @callback
    def async_get_tools(
        self, hass, llm_context: LLMContext, api_id: str
    ) -> LLMTools | None: ...
```

The one qualification that matters: platforms serve `LLM_API_ASSIST` only, so
this hook adds tools to Assist — it cannot be used to *restrict*, which is why
§3 wraps Assist instead of registering a bare custom API.

**To the brief — items that stand.** `entry.options` is superseded by subentries
(§4).

**POC gap list** (your eleven, verified): **1–9 and 11 confirmed. 10 refuted** —
`openai_conversation`, `ollama` and `anthropic` all still inherit both
`ConversationEntity` and `AbstractConversationAgent` and still call
`conversation.async_set_agent` in `async_added_to_hass`
(`openai_conversation/conversation.py:34-36,61`); the POC is correct, keep it.

One you did not list: the hardcoded `_attr_name` means every profile's entity is
called "Hermes home-assist".

**A correction to my own gap list.** I earlier called
`continue_conversation = answer.endswith("?")` a defect, claiming core derives
this from the chat log rather than from punctuation. Both halves were wrong in
the same place: core *does* derive it from the chat log, and
`ChatLog.continue_conversation` (`chat_log.py:355-373`) is itself a trailing
question-mark test. The POC matched core's behaviour. Delegating to
`conversation.async_get_result_from_chat_log` is still right — it is the
supported helper and also recognises the Greek and Chinese question marks the
hand-rolled check missed — but it is a small correctness gain, not the bug I
described.

Gap 1 changes meaning under this design: the conversation entity **still** has no
tool calling, and that is now correct rather than a defect. Control arrives via
MCP, not via the chat endpoint.

---

## 7. Phases and testing

**Phase 1 — skeleton.** `git init`, move POC to
`custom_components/hermes_conversation/`, MIT `LICENSE`, `README.md`,
`hacs.json`, `pyproject.toml`, `flake.nix`, CI (hassfest, HACS validate, ruff,
mypy, pytest). *Green on the unmodified POC.*

**Phase 2 — integration correctness.** Gaps 4–8, 12, 13: `entry.runtime_data`,
`DeviceInfo`, `ConfigEntryNotReady`, reauth, per-subentry unique IDs, real error
mapping. *Tests: flow success / `cannot_connect` / `invalid_auth` / duplicate /
reauth; setup raises `ConfigEntryNotReady` against a dead gateway.*

**Phase 3 — subentries.** The §4 schema, model list from Hermes, multiplex probe
warning. *Tests: create, reconfigure in place, two profiles as two entities.*

**Phase 4 — streaming.** *Tests: SSE fixtures — normal, mid-stream disconnect,
malformed frame, empty stream; assert deltas arrive incrementally.*

**Phase 5 — the restricted LLM API.** `llm.py`, `GuardedTool`, exposure repair
check. *Tests are the security-critical ones and get a coverage gate:*
- the API registers and appears in `llm.async_get_apis`
- `HassTurnOff` targeting a lock is refused **by name, by area, and by domain**
- `HassTurnOff` targeting a light still succeeds
- door and garage covers refused; other covers allowed
- reads of lock state still succeed
- a repair issue is raised when a lock is exposed

**Phase 6 — docs and release.** `diagnostics.py` redacting `api_key`; README
covering the `mcp_servers` snippet, the dedicated HA user, and the exposure
setup; HACS release.

**Strategy.** `pytest-homeassistant-custom-component` throughout, `aioresponses`
for the Hermes HTTP surface — no live gateway in CI. Phase 5 is where a missing
branch is a vulnerability rather than a style complaint, so it is gated
separately.

**Out of scope for v1:** `ai_task`/STT/TTS subentries, brands PR, HACS default
listing.

---

## 8. Repo layout, HACS, Nix

```
custom_components/hermes_conversation/
  __init__.py  manifest.json  config_flow.py  conversation.py
  const.py  entity.py  llm.py  diagnostics.py
  strings.json  translations/en.json          ← strings.json is source
tests/
.github/workflows/  hassfest.yml  hacs.yml  lint.yml  test.yml
hacs.json  LICENSE  README.md  pyproject.toml  flake.nix  DESIGN.md
```

No `hermes_plugin/`. The Hermes side is configuration in your dotfiles, and the
README documents it.

`manifest.json` keeps its `version` key (HACS requires it, core omits it) and it
must equal the GitHub **release** tag — HACS reads releases, not bare tags.
`dependencies` gains `llm` and `conversation`. `codeowners: ["@mniehe"]`.

**Nix devshell.** `flake.nix` (nixpkgs + flake-utils) exposing python313, uv,
ruff, mypy and git, with `UV_PYTHON` pinned so `uv sync` uses the Nix interpreter
instead of downloading one. Python packages come from `pyproject.toml` via uv and
are locked in `uv.lock`: HA moves far too fast for nixpkgs to track, and pinning
it in Nix would mean hand-maintaining its dependency closure. Nix supplies the
toolchain, uv supplies the packages.

---

## 9. What you still have to do by hand

1. Create the dedicated non-admin HA user and its long-lived token.
2. Add `HA_TOKEN` to `secrets/hermes-home-assist.env` (sops — only you).
3. Add the `mcp_servers.home_assistant` block to the `home-assist` profile.
4. Add the `mcp_server` integration in HA and select the restricted API.
5. Unexpose lock and door/garage-cover entities from the assistant.

## 10. One thing I could not settle

`mcp_server` declares `single_config_entry: true`, so HA serves **one** MCP
endpoint, and the admin gate above means the practical configuration is: one
restricted API, selected in the config flow, reached at the bare `/api/mcp` with
a non-admin token. That works.

It becomes a problem only if you later want Telegram-Hermes and voice-Hermes to
hold *different* permissions. That needs two APIs addressed by id, and the docs
are clear that anything other than Assist addressed that way demands an admin
token — which cancels the dedicated non-admin user. I have not tested that
combination and will not claim it works. Not a v1 blocker, but worth knowing
before it becomes a surprise.
