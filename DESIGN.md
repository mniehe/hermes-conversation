# hermes_conversation — Design

Status: **Phase 0, awaiting review.** No implementation code written.

Verified against:

| Thing | Version | How it was read |
|---|---|---|
| Home Assistant core | **2026.8.2** (your deployment) | Wheels for 2026.8.2 and 2026.8.3 from PyPI, diffed — see below |
| Hermes agent | **0.20.5** | `/nix/store/…-hermes-agent-env/lib/python3.12/site-packages` (matches pinned commit `fcbd107`, whose subject is `chore: release v0.20.5`) |

Every claim below cites the file it came from. Where the brief and the source
disagree, the source wins and the disagreement is called out.

Your deployment is `ghcr.io/home-assistant/home-assistant:2026.8.2`
(`hosts/cougar/services/home-assistant.nix:40`). I had designed against 2026.8.3,
so I diffed the two wheels across the surface this integration touches. Every file
it depends on — `components/conversation/{chat_log,entity,models}.py`,
`helpers/llm.py`, and all of `openai_conversation/` — is **byte-identical**. The
only differences are `default_agent.py` (unused here) and two translation files.
**The design holds exactly as written on 2026.8.2.**

---

## 0. The finding that decides everything

**Hermes' OpenAI-compatible endpoint ignores client-supplied tools.**

`gateway/platforms/api_server.py`, `_handle_chat_completions` (lines 4173–4533).
The entire set of request-body fields it reads is:

```python
messages   = body.get("messages")
stream     = _coerce_request_bool(body.get("stream"), default=False)
model_name = body.get("model", self._model_name)
```

`tools` and `tool_choice` appear exactly once in that function — inside the
idempotency-key fingerprint (line 4433) — so sending them changes the cache key
and nothing else. The handler then calls `self._run_agent(...)`: Hermes runs
**its own** agent loop with **its own** toolsets and returns prose. The response
schema has no `tool_calls`, and the SSE stream emits only
`choices[0].delta.content`.

I checked the other two candidate endpoints. `/v1/responses` (5335–5659) and
`/v1/runs` (6682–7097) read `input`, `instructions`, `previous_response_id`,
`conversation_history`, `model`, `stream` — no `tools` either.

**Consequence: architecture A is not implementable against Hermes 0.20.5.** It
is not a matter of effort or preference; the server has no code path that would
accept a tool definition from Home Assistant or emit a tool call back. This is
the opposite of how `openai_conversation` talks to OpenAI, and it is the single
most important fact in this document.

---

## 1. Architecture decision — B, with an HA-controlled policy plane

**Chosen: architecture B (Hermes drives the tools), plus a policy channel back
from Home Assistant.** I am calling it B rather than "hybrid" because the tool
execution path has exactly one implementation; the hybrid-sounding part is
configuration, not a second execution path.

Three reasons, in order of weight:

1. **A is impossible** (section 0). That alone settles it.
2. **B is what you actually asked for.** You already talk to the `home-assist`
   profile from a Telegram group. Under A, tools exist only inside a HA voice
   pipeline, so Telegram would stay mute — the capability would be scoped to the
   one channel you use least. Under B, every Hermes channel gains the same house
   control.
3. **B puts enforcement server-side.** A boundary enforced in the HA
   integration is bypassed the moment anyone reaches the profile from another
   channel. A boundary inside the Hermes plugin holds for all of them.

What the two halves become:

```
┌─ Home Assistant ──────────────────────────┐     ┌─ Hermes ───────────────────┐
│                                           │     │                            │
│  conversation entity                      │     │  agent loop                │
│    • streams text, no tool loop  ─POST────┼────▶│  /p/<profile>/v1/          │
│    • one per subentry                     │     │      chat/completions      │
│                                           │     │           │                │
│  policy view (new)                        │     │           ▼                │
│    GET /api/hermes_conversation/policy ◀──┼─────┼── hermes_plugin tools       │
│    returns the allow/deny set you picked  │     │      ha_get_state           │
│                                           │     │      ha_call_service        │
│  REST API                                 │     │      ha_list_entities       │
│    /api/services/<domain>/<service> ◀─────┼─────┼──         │                 │
│    /api/states                            │     │           │                 │
└───────────────────────────────────────────┘     └────────────────────────────┘
```

The HA integration is a transport **and** the place you configure policy. The
Hermes plugin is where tools live **and** where policy is enforced. Configuration
and enforcement are deliberately in different processes — see below.

### Why the conversation entity still earns its place

It is fair to ask why the HA integration exists at all if Hermes does the work.
It gives you: a Voice-PE / Assist-pipeline target with streaming TTS, per-profile
agents selectable in HA's UI, the policy editor, and diagnostics. Without it you
would configure the allowlist by editing YAML on the Hermes box.

---

## 2. The capability boundary

Requirement: *some HA actions (unlocking doors) must NOT be reachable by the
agent*, the boundary must be **explicit and enforceable**, and the **UI must let
you pick what is exposed**.

Those pull in opposite directions — the UI is in HA, but enforcement has to be in
Hermes or Telegram bypasses it. Resolution: **HA narrows, Hermes ceilings.**

```
effective_capabilities =
        (tools registered at all)         ← per-profile plugins.enabled
      ∩ (policy fetched from HA)          ← what you picked in the HA UI
      ∩ (plugin allowlist in plugin.yaml) ← the ceiling, set on the Hermes box
      − (hard never-list, in code)        ← not configurable anywhere
```

Four layers, each able only to *remove*:

**Layer 0 — the profile boundary. Free, and stronger than anything below it.**
Hermes enables plugins *per profile*: `plugins.enabled = ["web-crawl4ai"]` sits
inside each profile's `settings` block (`hosts/cougar/hermes-vm.nix:305`). Enable
the Home Assistant plugin only for `home-assist`, and the `ha_*` tools do not
exist in your `wife` profile or the default one — not "are denied", but are never
registered. No policy evaluation to get wrong. This is the cheapest and most
robust layer, and it is worth using deliberately rather than by accident.

**Layer 1 — hard never-list. Compiled in, no config key reaches it.**
`lock.open`, `lock.unlock`, `alarm_control_panel.alarm_disarm`,
`cover.open_cover` on entities in the `garage` device class, and every
`homeassistant.*` meta-service. If you later want one of these, it is a code
change and a release — which is the correct amount of friction for the door lock.

**Layer 2 — plugin ceiling.** `plugin.yaml` `config_schema` carries
`allowed_domains` and `denied_entities`. This lives on the Hermes host, edited
via Nix, and is the maximum the plugin will ever do regardless of what HA says.

**Layer 3 — HA-side policy.** The subentry options flow writes a domain/entity
allowlist. The plugin fetches it from a new authenticated HA view and intersects.

The critical invariant: **HA-supplied policy can only narrow, never widen.** If
HA is compromised and returns `{"allowed_domains": ["lock"]}`, the intersection
with layer 2 and the subtraction of layer 1 still yields no lock control. HA is
therefore not a trusted authority here, only a restricting one.

**Fail-closed.** If the policy fetch fails (HA down, token expired, timeout), the
plugin falls back to the layer-2 ceiling — *not* to "allow everything". Policy is
cached with a short TTL so a HA restart doesn't strand the agent.

**Enforcement point.** Every tool handler calls one `_check_allowed(domain,
service, entity_id)` before touching HA, and there is exactly one such function.
A tool that forgets to call it is a bug caught by a test that enumerates every
registered handler.

Two things this deliberately does *not* rely on:

- *HA's exposed-entity setting* is used to decide what the agent can **see**
  (`ha_list_entities` only returns exposed entities), but never as a security
  boundary — exposure is a discoverability feature, and a determined model can
  name an unexposed entity directly. The allowlist is checked on every call
  regardless of exposure.
- *HA user permissions.* HA's own permission model is too coarse to express
  "everything except unlocking", so a scoped HA user is not a substitute.

---

## 3. Config entry and subentry schema

The brief says options live in `entry.options` and that
`CONF_LLM_HASS_API` is read from there. **That is no longer how core does it.**
In 2026.8.3, `openai_conversation`, `ollama`, `anthropic` and
`google_generative_ai_conversation` all use **subentries**: the config entry holds
the connection, and each conversation agent is a `ConfigSubentry` whose options
live in `subentry.data`. Verified in all four `conversation.py` files
(`if subentry.subentry_type != "conversation": continue`) and in
`openai_conversation/config_flow.py:217` (`async_get_supported_subentry_types`).

This is better news than it sounds: subentries are exactly the mechanism for
"multiple Hermes profiles must coexist", and they make **profile a per-agent
option** as you asked mid-session — without a second config entry or a
delete-and-re-add.

### Config entry — one per Hermes **profile**

Your Nix config settles a question I had guessed wrong. Named profiles
"authenticate through `/p/<profile>/` with their own `API_SERVER_KEY`"
(`hosts/cougar/hermes-vm.nix:192-193`), and each profile's key comes from its own
sops env file (`hermes-home-assist.env`, `hermes-wife.env`). **Profile and API key
are one credential pair and cannot be configured independently.**

My first draft put `api_key` on the entry and `profile` on the subentry. That is
wrong: switching a subentry's profile would silently keep the previous profile's
key and 401. It also breaks reauth, which HA runs against a config *entry* — an
entry-level reauth would not know which subentry's key had expired.

So the config entry is **per profile**:

| Key | Selector | Stored in | Notes |
|---|---|---|---|
| `base_url` | `TextSelector(URL)` | `entry.data` | **Gateway root**, e.g. `http://10.0.0.3:8642` — *not* the `/p/…/v1` path |
| `profile` | `TextSelector` | `entry.data` | e.g. `home-assist`. **Free text — see below** |
| `api_key` | `TextSelector(PASSWORD)` | `entry.data` | That profile's `API_SERVER_KEY` |

Validated by `GET {base_url}/p/{profile}/v1/models`. `401 → invalid_auth`,
otherwise `cannot_connect`. `unique_id = f"{base_url}#{profile}"`, so the same
gateway can host several profiles as several entries — which is what
"multiple config entries must coexist" asked for.

**You still get to change the profile without deleting the entry.** Core supports
an entry-level `async_step_reconfigure` returning `async_update_reload_and_abort`
(`openai_conversation/config_flow.py:249`), so profile, URL and key are all
editable in place. That satisfies the no-delete-and-re-add requirement; it just
happens on the entry rather than the subentry, because that is where the
credential lives.

**Reauth** (`async_step_reauth` / `async_step_reauth_confirm`) re-prompts for
`api_key` only, triggered by `ConfigEntryAuthFailed` on a runtime 401.

**Why `profile` is free text and not a dropdown:** there is no profile-listing
endpoint. `api_server.py` handles the `/p/<profile>/` prefix in
`_make_profile_prefix_middleware` (2041) against a `multiplex_profile_allowlist`
(2002) but never exposes that list over HTTP. The field is validated by probing
`GET /p/{profile}/v1/models` on submit.

**`gateway.multiplex_profiles` is already `true`** on your box
(`hosts/cougar/hermes-vm.nix:191`), so the prefix is live. The config flow will
still probe `/p/{profile}/v1/models` against `/v1/models` and warn if they are
indistinguishable — someone else installing this from HACS will not have it on,
and a silently-wrong profile is a miserable thing to debug.

### Conversation subentry — one per agent, under a profile's entry

| Key | Selector | Notes |
|---|---|---|
| `name` | `str` | Entity name; new subentries only |
| `model` | `SelectSelector` | Populated from `GET /p/{profile}/v1/models` for this entry's profile; `custom_value=True` |
| `prompt` | `TemplateSelector()` | System prompt, default `llm.DEFAULT_INSTRUCTIONS_PROMPT` |
| `timeout` | `NumberSelector` | 10–300 s, default 120 |
| `allowed_domains` | `SelectSelector(multiple=True)` | Layer-3 policy: which HA domains the agent may act on |
| `denied_entities` | `EntitySelector(multiple=True)` | Layer-3 policy: specific entities to withhold |

**Why `profile` is free text and not a dropdown:** I looked for a profile-listing
endpoint and there is none. `api_server.py` handles the `/p/<profile>/` prefix in
`_make_profile_prefix_middleware` (2041) against a
`multiplex_profile_allowlist` (2002), but never exposes that list over HTTP. So
the field is a text input validated by probing `GET /p/<profile>/v1/models` when
the form is submitted.

**Operational prerequisite worth stating loudly:** the `/p/<profile>/` prefix only
works when `gateway.multiplex_profiles` is `true` in Hermes' `config.yaml`. With
it off, the prefix is *silently ignored* and every request lands on the default
profile (line 1990-1991). Silently hitting the wrong profile is a nasty failure
mode, so the config flow will probe both `/p/<profile>/v1/models` and
`/v1/models` and warn when they are indistinguishable.

**`CONF_LLM_HASS_API` is deliberately absent.** Under architecture B, HA does not
supply tools, so the field would be dead UI. Including it would imply a
capability the integration does not have.

`ConversationEntityFeature.CONTROL` is set when `allowed_domains` is non-empty,
so HA's UI correctly reports whether the agent can control the house.

---

## 4. Request / response flow

```
_async_handle_message(user_input, chat_log)
  │
  ├─ chat_log.async_provide_llm_data(
  │      user_input.as_llm_context(DOMAIN),
  │      None,                          # no HA LLM API — Hermes owns tools
  │      subentry.data[CONF_PROMPT],
  │      user_input.extra_system_prompt)
  │
  ├─ messages = [ {role, content} for content in chat_log.content ]
  │
  ├─ POST {base_url}/p/{profile}/v1/chat/completions
  │      Authorization: Bearer {api_key}
  │      { model, messages, stream: true }
  │
  ├─ async for content in chat_log.async_add_delta_content_stream(
  │        self.entity_id, _transform_stream(response)):
  │      pass                           # TTS starts on the first delta
  │
  └─ return conversation.async_get_result_from_chat_log(user_input, chat_log)
```

**No tool-call loop, and no `MAX_TOOL_ITERATIONS`.** This is the structural
difference from `openai_conversation`, and it follows directly from section 0:
`chat_log.unresponded_tool_results` can never become true, because Hermes never
sends a tool call back. Hermes runs its own loop internally. Writing a
`for _iteration in range(10)` loop here would be cargo-culting core's shape
around a protocol that cannot use it — the loop would always break on the first
pass.

`_transform_stream` maps Hermes SSE to HA deltas. Hermes' format is verified
standard OpenAI (`_write_sse_chat_completion`, 4533+): a leading
`{"delta": {"role": "assistant"}}` chunk, then `{"delta": {"content": "…"}}`
chunks. So the transform is a thin rename into HA's
`AssistantContentDeltaDict`, plus a `[DONE]` sentinel.

Streaming satisfies the voice-latency requirement: TTS begins on the first
content delta instead of after the full response.

**Errors** map to `HomeAssistantError` with translatable keys, never to a
hardcoded English sentence:

| Condition | Result |
|---|---|
| 401 at runtime | `ConfigEntryAuthFailed` → reauth flow |
| Timeout / `ClientError` | `HomeAssistantError("cannot_connect")` |
| Malformed SSE / empty | `HomeAssistantError("invalid_response")` |
| Setup-time probe fails | `ConfigEntryNotReady` |

---

## 5. The Hermes plugin

### Registration hook — verified, and your warning was right to give

You told me not to assume `ctx.register_tool`. I checked: **it does exist**, at
`hermes_cli/plugins.py:1705`. But the surrounding contract is not what the
web-search example suggests, and that is the part worth having checked:

```python
ctx.register_tool(
    name: str, toolset: str, schema: dict, handler: Callable,
    check_fn: Callable | None = None, requires_env: list | None = None,
    is_async: bool = False, description: str = "", emoji: str = "",
    override: bool = False,
) -> Optional[PluginRegistration]
```

Two contract details that shape the plugin layout:

1. **Tools must be declared in the manifest or they are not loaded.**
   `provides_tools` in `plugin.yaml` is what opts the plugin in
   (`plugins.py:4588`); a `tools.py` that exists but isn't declared is ignored
   on purpose. Declaring tools with no `tools.py` logs a warning and silently
   yields no tools.
2. **Tools go in `tools.py`, exposing `register_tools(ctx)`** — *not* the
   `register(ctx)` in your web-crawl4ai example. `register()` is the
   general/backend hook; `register_tools()` is loaded at discovery time
   specifically so the SDK stays unimported and startup stays cheap
   (`plugins.py:4616`). Both can coexist.

`override=True` is not used — it exists to replace built-ins and requires an
operator opt-in. Registering fresh `ha_*` names needs none of that.

### Layout

```
hermes_plugin/
  plugin.yaml        name: home-assistant, kind: backend,
                     provides_tools: [ha_list_entities, ha_get_state, ha_call_service],
                     config_schema: { ha_base_url, allowed_domains, denied_entities },
                     requires_env: [HA_TOKEN]
  __init__.py        register(ctx) — import-light
  tools.py           register_tools(ctx) — the three registrations
  client.py          HA REST client (aiohttp)
  policy.py          _check_allowed(), policy fetch + cache, the three layers
```

This mirrors your existing `modules/nixos/hermes-vm-plugins/crawl4ai/`
(`plugin.yaml`, `__init__.py`, `provider.py`), and deploys the same way — a
systemd-tmpfiles symlink, confirmed at `modules/nixos/hermes-vm.nix:787`:

```
"L+ /data/hermes/.hermes/plugins/crawl4ai - - - - ${./hermes-vm-plugins/crawl4ai}"
```

So: no absolute paths, no writes inside the plugin directory, all state in memory
or under Hermes' own state dir. Since the plugin lives in *this* repo rather than
in dotfiles, the dotfiles side gets a fetched source (flake input or pinned
tarball) whose store path is the symlink target.

**Two dotfiles changes this will need** — flagged now, not made:
`home-assist` currently has only `secretEnvFile` and no `settings` block
(`hosts/cougar/hermes-vm.nix:346-348`), so it needs
`settings.plugins.enabled = ["home-assistant"]`, plus a tmpfiles line for the new
plugin and `HA_TOKEN` added to `hermes-home-assist.env`.

### Tools

| Tool | Purpose | Policy check |
|---|---|---|
| `ha_list_entities` | Enumerate exposed entities, optionally by domain | Filters to `allowed_domains`; never lists denied entities |
| `ha_get_state` | Read one entity's state + attributes | Read is allowed for any *exposed* entity |
| `ha_call_service` | Call `<domain>.<service>` on entities | Full three-layer check; the only mutating tool |

Keeping mutation in exactly one tool means the boundary has one chokepoint.

### Authentication to Home Assistant

**Long-lived access token**, read via
`hermes_cli.config.get_env_value("HA_TOKEN")` with an `os.getenv` fallback, per
your existing convention (`hermes_cli/config.py:4451`). Declared as
`requires_env: [HA_TOKEN]` so Hermes reports a clear error when it is missing
rather than failing at first tool call.

The token is created against a **dedicated HA user**, not your admin account, so
the HA logbook attributes agent actions to `Hermes` and revocation doesn't log
you out. The plugin sends `Authorization: Bearer {HA_TOKEN}` to
`{ha_base_url}/api/…` and never logs the token.

The same token authenticates the policy fetch, so there is no second credential.

### The policy view (HA side)

A new `HomeAssistantView` at `/api/hermes_conversation/policy`, authenticated by
HA's normal token auth, returning the union of every conversation subentry's
policy, keyed by profile:

```json
{ "home-assist": { "allowed_domains": ["light","switch","climate"],
                   "denied_entities": ["switch.server_rack"] } }
```

**One item I could not fully verify and will confirm at implementation time:**
whether a plugin can reliably read *its own current profile name* at tool-call
time under multiplexing. `api_server.py` enters a profile runtime scope
(`_enter_profile_scope`, 2014) and `agent.secret_scope.is_multiplex_active()`
exists, but I did not trace a public accessor for the profile name. If there
isn't one, the plugin uses a single global policy (the intersection of all
profiles' — i.e. the most restrictive), which is safe but coarser. This does not
change the architecture, only the granularity, so it is not worth blocking on.

---

## 6. Gap list — verified against the POC

You asked me to confirm or refute all eleven. **Nine confirmed, one refuted, one
partly wrong.**

| # | Your claim | Verdict | Evidence |
|---|---|---|---|
| 1 | No tool calling | **Confirmed** — and worse than stated | `conversation.py:103`. But the fix is not "call `async_add_assistant_content`": Hermes cannot emit tool calls at all (§0). Tools move to the plugin. |
| 2 | No options flow | **Confirmed** | No `async_get_options_flow`. Fix is a *subentry* flow, not an options flow (§3). |
| 3 | Model + profile hardcoded | **Confirmed** | `const.py:6-7`; profile also baked into `DEFAULT_BASE_URL`'s `/p/home-assist/v1`. |
| 4 | `_attr_unique_id = DOMAIN` collides | **Confirmed** | `conversation.py:40`. Under subentries it becomes `subentry.subentry_id`. |
| 5 | No `DeviceInfo` | **Confirmed** | Absent. |
| 6 | No connectivity check on setup | **Confirmed** | `__init__.py:12-13` forwards and returns `True` unconditionally. |
| 7 | Errors swallowed into English string | **Confirmed** | `conversation.py:99-101` catches five exception types into one sentence, then returns it as a *successful* result — HA cannot tell the request failed. |
| 8 | No reauth | **Confirmed** | No `async_step_reauth`. |
| 9 | `stream: False` hurts voice latency | **Confirmed** | `conversation.py:83`. Hermes does support SSE (§4), so this is pure loss. |
| 10 | Mixing `ConversationEntity` + `AbstractConversationAgent` and calling `async_set_agent` is obsolete | **REFUTED** | All three of `openai_conversation`, `ollama`, `anthropic` still inherit both and still call `conversation.async_set_agent` in `async_added_to_hass` (e.g. `openai_conversation/conversation.py:34-36,61`). The POC is correct here. **Keep it.** |
| 11 | No tests/CI/HACS/LICENSE/README | **Confirmed** | Seven files, none of them these. |

Two defects you did not list:

12. **`supported_languages` returns `MATCH_ALL` while the entity claims
    `_attr_has_entity_name = True` with a hardcoded `_attr_name`** — the name
    should come from the subentry, or every profile's entity is called
    "Hermes home-assist".
13. **`continue_conversation = answer.endswith("?")`** (`conversation.py:114`) —
    a rhetorical question in a response silently reopens the mic. Core derives
    this from the chat log, not from punctuation.

And one correction to the brief itself: **`llm.LLMTools` and the
`async_get_tools(hass, llm_context, api_id)` hook do not exist in 2026.8.3.**
I grepped the whole wheel; there are no matches. The actual extension point is
subclassing the `llm.API` ABC (`helpers/llm.py:210`) and registering it with
`llm.async_register_api`. Filtering the `tools` list on the returned
`APIInstance` is genuinely enforceable — `APIInstance.async_call_tool` raises
`HomeAssistantError` for any tool not in that list (`helpers/llm.py:203-206`).
That would have been the right boundary *for architecture A*; it is unused here,
but it is the mechanism to reach for if Hermes ever gains tool passthrough.

---

## 7. Phased plan and testing

Each phase ends green and committed. Conventional commits, single-line subjects.

**Phase 1 — repo skeleton.** `git init`; move the POC to
`custom_components/hermes_conversation/`; add MIT `LICENSE`, `README.md`,
`hacs.json`, `pyproject.toml`, `flake.nix`, `.gitignore`. CI: hassfest, HACS
validate, ruff, mypy, pytest. *Test: CI green on the unmodified POC.*

**Phase 2 — integration correctness.** Gaps 4–8, 12, 13. `entry.runtime_data`,
`DeviceInfo`, `ConfigEntryNotReady`, reauth, per-subentry unique IDs, real error
mapping. *Test: config flow (success, `cannot_connect`, `invalid_auth`,
duplicate, reauth); setup raises `ConfigEntryNotReady` on a dead gateway.*

**Phase 3 — subentries and the options UI.** Subentry flow with the section-3
schema; model list fetched from Hermes; multiplex probe warning. *Test: create,
reconfigure without re-adding, two profiles coexisting as two entities.*

**Phase 4 — streaming.** `stream: true`, `_transform_stream`,
`async_add_delta_content_stream`. *Test: SSE fixtures — normal, mid-stream
disconnect, malformed frame, empty stream; assert deltas arrive incrementally,
not batched.*

**Phase 5 — Hermes plugin.** `plugin.yaml`, `register_tools`, HA client, the
three tools, three-layer policy. *Test (pure pytest, no HA): `_check_allowed`
truth table; the never-list survives an adversarial policy claiming
`allowed_domains: ["lock"]`; fail-closed on policy-fetch failure; a test that
enumerates registered handlers and asserts each calls the check.*

**Phase 6 — policy view + docs.** The HA view, `diagnostics.py` (redacting
`api_key` and `HA_TOKEN`), README with the Hermes-side `multiplex_profiles`
and `HA_TOKEN` setup, HACS release.

**Testing strategy.** `pytest-homeassistant-custom-component` for everything
HA-side, with `aioresponses` for the Hermes HTTP surface — no live gateway in
CI. The plugin half is plain pytest with a faked `ctx`, since it must not import
`homeassistant` at all. Coverage gate on `policy.py` specifically: the security
boundary is the one module where a missing branch is a real vulnerability rather
than a style complaint.

**Deliberately out of scope for v1:** `ai_task` / STT / TTS subentries,
brands PR (custom-repo install doesn't need it), and HACS default listing.

---

## 8. Repo layout

```
custom_components/hermes_conversation/
  __init__.py  manifest.json  config_flow.py  conversation.py
  const.py  entity.py  diagnostics.py  http.py        ← policy view
  strings.json  translations/en.json                  ← strings.json is source
hermes_plugin/
  plugin.yaml  __init__.py  tools.py  client.py  policy.py
tests/
  test_config_flow.py  test_conversation.py  test_init.py
  test_streaming.py  test_plugin_policy.py
.github/workflows/  hassfest.yml  hacs.yml  lint.yml  test.yml
hacs.json  LICENSE  README.md  pyproject.toml  flake.nix  DESIGN.md
```

`manifest.json` keeps its `version` key (HACS requires it; core integrations
omit it) and it must equal the GitHub **release** tag — HACS reads releases, not
bare tags. `codeowners` gets `["@mniehe"]`.

### Nix devshell

`flake.nix` with `nixpkgs` + `flake-utils`, exposing a devshell containing
python313, uv, ruff, mypy, and git, with `UV_PYTHON` pinned so `uv sync` uses the
Nix interpreter rather than downloading one. Dependencies
(`homeassistant`, `pytest-homeassistant-custom-component`, `aioresponses`) are
resolved by uv from `pyproject.toml` and locked in `uv.lock` — HA moves far too
fast for nixpkgs' packaging to track, and pinning it in Nix would mean
hand-maintaining the dependency closure. Nix supplies the toolchain; uv supplies
the Python packages.

---

## Open questions for you

1. ~~**Hermes `multiplex_profiles`**~~ — **answered:** already `true`
   (`hosts/cougar/hermes-vm.nix:191`). Still documented in the README for other
   installers.
2. **Dedicated HA user for the agent** — do you want me to document creating one,
   or will you reuse an existing token? Either way `HA_TOKEN` needs adding to
   `hermes-home-assist.env`, which only you can do.
3. **Never-list scope** — I have `lock.open`/`lock.unlock`,
   `alarm_control_panel.alarm_disarm`, garage covers, and `homeassistant.*`.
   Anything else that should be un-configurable? Frigate, or anything on the
   Vultr box?
4. **HA target version** — I designed against 2026.8.3 (current latest). Confirm
   that matches your deployment; the subentry API is the part most sensitive to
   a mismatch.
