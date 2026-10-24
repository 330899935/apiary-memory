# Changelog

All notable changes to this project are documented here.
Versions follow [Semantic Versioning](https://semver.org/). This project is pre-1.0, so minor
versions may change configuration.

---

---

---

---

## 0.1.8 — 2026-10-24

**Documentation only. No code, no behaviour, no dependency changes.**

### Why

The official MCP Registry validates PyPI package ownership by looking for a token in the
package description. A published PyPI description cannot be edited, so v0.1.7 could never
satisfy the check. This release adds the token and nothing else.

### Changed

- `README.md`: added the registry ownership marker `<!-- mcp-name: io.github.330899935/hive-memory -->`
  directly under the title. It is an HTML comment — invisible when rendered on PyPI or GitHub.

---

## 0.1.7 — 2026-10-23

**This release gives the package a memory engine of its own.** It is a behaviour change
and it is not cosmetic, so the reason comes first.

### Why

Glama declined to list 0.1.6. Their reason, in full:

> The README describes this repository as a thin bridge that relays MCP traffic to the
> proprietary Hive app running locally. It explicitly states the package contains no memory
> engine and will not work standalone, and that MCP methods like initialize/tools/list are
> answered by the Hive app itself. The actual MCP server/tool implementation therefore lives
> in the closed-source Hive app, not in this repository.

That is a fair reading of what we wrote. The README said "this package contains no memory
engine" and "this package will not work on its own", and the `Dockerfile` said outright that
`tools/call` fails inside a container. A directory that lists servers for people to try is
entitled to refuse one that cannot answer a single call on a clean machine. Rewriting the
description would not have changed any of that.

So the gap was closed in code rather than in prose.

### Added: `hive_mcp.store`

A SQLite database on the user's own disk. Standard library only — no dependencies, no
daemon, no embeddings, no native extensions.

- `propose` writes a row in the `pending` state.
- The human confirms with `memory-hive-mcp-cli approve <id>`, in a terminal, out of band.
- `recall` searches `confirmed` rows only.

Everything else has a local implementation too: `get_context` (recent digest), `manage`
(`list_pending` / `get_by_id` / `forget` / `restore` / `discard_pending`, matching the existing
action names and their reversibility guarantees), `say` and `board` (a shared message board
with cursors, expiry and an optional roster).

Search is deliberately unfashionable: Chinese is indexed as single characters plus overlapping
bigrams, Latin as words, scored by coverage and then nudged by recency. A segmenter would be a
20 MB dependency in a package whose promise is no dependencies; a vector index would need an
embedder. The README states plainly that this ranks, and does not understand, and that scores
are relative per query rather than probabilities.

### The routing rule, and why it is the important part

`HIVE_MODE` decides where a call runs:

| value | behaviour |
| --- | --- |
| `auto` (default) | ask the Hive app; use the local store **only if the app is unreachable** |
| `local` | never touch the network |
| `remote` | the app only; a connection failure is an error, not a reroute |

Under `auto`, the fallback fires on a connection failure and on nothing else. If the app is
running and answers with an error — no agent credential, a bad argument — that error is returned
unchanged. Silently retrying against the local database would write the user's memory into a
second store that nothing displays, while their client believed it had reached the app. Silent
data forking is worse than any error message. `OfflineBehaviour.test_auto_mode_does_not_fall_back_when_the_app_answers`
stands up a backend that refuses exactly the way the real one does, and asserts that the local
database was never created.

### Changed behaviour — read this if you wrote against 0.1.6

**`tools/call` no longer fails when the app is absent.** Through 0.1.6 an offline call returned
JSON-RPC error `-32603` with HTTP status 502 in `error.data`. It now returns a real result from
the local store, tagged `"mode": "local"`. Code that treated "offline" as "always an error"
needs updating; two tests that pinned the old contract were rewritten to pin the new one rather
than deleted.

The failure path still exists under `HIVE_MODE=remote`, and its message still names the endpoint
and the fix — that behaviour is unchanged.

### Added: `memory-hive-mcp-cli`

```
list | show <id> | approve <id>... [--all] | reject <id>... |
search <query> | forget <id>... | restore <id>... | stats | export | path
```

Confirmation is not in the MCP tool surface, here or upstream, and that has not changed. An
agent can only propose. The CLI is how the person opens the gate — deliberately a terminal
command they have to type, not a button an agent can reach. `--db` overrides the database path;
`HIVE_DB` does the same from the environment.

### Honest limits, stated here as well as in the README

- The local store is **plain SQLite**. It is not encrypted. The app's vaults (Argon2id-wrapped
  key, AES-256 at rest) remain the only encrypted option, and the local store says
  `"encrypted": false` in `stats` rather than leaving it to be discovered.
- No embeddings, no semantic recall. A query with no lexical overlap finds nothing, and the
  response says so instead of returning a plausible neighbour.
- Every local response carries `"mode": "local"` so a caller can always tell which engine
  answered.

### Fixed

- **Version reporting in a source checkout.** `importlib.metadata` describes what pip installed,
  not what is on `sys.path`. Editing a checkout beside an older installed copy reported the
  older version. A checkout now reads its own `pyproject.toml`, and
  `NoDrift.test_version_agrees_across_files` compares the runtime value instead of only
  the literals.
- **Tests were not closed against the environment.** `_clean_env` inherited `HIVE_MODE` and a
  real `HIVE_DB` from the runner's shell. Both are now overridden with a temporary database
  removed at exit, so the suite cannot read or write a developer's memory.

### Not changed

`tools.json`, the six tool names, every parameter and enum, and all descriptions. The surface a
client sees is identical; only the engine behind it is new.

## 0.1.6 — 2026-10-22

Presentation release, aimed at directory crawlers. No behavioural change to the bridge.

**The tool definitions were scoring badly, and the score is public.** Glama scores every tool
of every listed server with TDQS (Tool Definition Quality Score) and publishes the
per-dimension breakdown. Auditing our own six tools against that rubric before submitting put
the set at roughly **3.05, tier C+** — squarely in the population the framework's source
studies describe, where 97% of tool definitions carry at least one defect and 89% never say
when a tool should or should not be used.

Two gaps accounted for almost all of it, and both were 0 of 6:

- **No MCP annotations.** `readOnlyHint`, `destructiveHint`, `idempotentHint` and
  `openWorldHint` now declare the safety profile of each tool, so the descriptions can spend
  their words on what annotations cannot express. `recall`, `get_context` and `board` are
  marked read-only; `propose` and `manage` are not, and `manage` is explicitly *not*
  destructive because every action except one is reversible.
- **No usage guidance.** Every description now says when to use the tool, when not to, and
  names the sibling that answers the excluded case — `recall` versus `get_context` (query
  versus recency), `manage` versus `recall` (address versus query), `say` versus `board`
  (write versus read).

Also corrected while there: `propose` now states plainly that a success response means
*queued, not stored*, which is the product's central claim and was previously only in the
README; `manage` documents that `forget` is a soft delete restorable with `restore`, and that
only `discard_pending` is irreversible; `board` notes that messages expire, so an empty result
is ambiguous.

Descriptions were kept in English here because this snapshot is what an English-language
crawler indexes. A caller with a running Hive sees the backend's own (Chinese) descriptions
instead, via forwarding — see 0.1.4.

**Added `Dockerfile`.** Glama builds each server from a Dockerfile, either supplied by the
maintainer or inferred by its build system; **if the inferred build fails, the profile is kept
but distribution is withheld** and the server never appears in search. For a stdlib-only
bridge there is nothing to infer, so a five-line file replaces that risk. It runs as a
non-root user, installs from `pyproject.toml`, and opens no port.

**Fixed stderr noise.** `python -m hive_mcp.stdio` emitted
`RuntimeWarning: 'hive_mcp.stdio' found in sys.modules...` because `__init__.py` imported the
submodule. A stdio server should keep stderr clean — a warning on every run teaches hosts and
crawlers to ignore the channel they use to surface real errors. The package no longer imports
`stdio`; use `from hive_mcp.stdio import main` or the `memory-hive-mcp` command.

## 0.1.5 — 2026-10-22

Completes what 0.1.4 claimed but did not do. Same-day follow-up, for one reason: 0.1.4's
changelog and README both said an auth failure gets a pointer to `HIVE_AGENT_ID` /
`HIVE_AGENT_TOKEN`. It did not.

**The hint was attached to the wrong code path.** The Hive reports authentication failures as
**HTTP 200 with a JSON-RPC error** (code `-32001`), not as an HTTP status. The credential hint
was only applied inside the `urllib.error.HTTPError` branch, so on the real path — app
installed, no agent registered, backend replying in Chinese — the message went through
untouched. Reproduced against a live Hive, then pinned by a test.

- **One decorator now handles auth-looking errors on every path**, including the HTTP 200
  success path. The backend's own code and wording are preserved verbatim; only the
  actionable pointer is appended.
- Added `AuthHint` tests that stand up a fake backend returning `200` + `-32001`, so this
  cannot regress silently. The suite is now 30 tests.
- The hint is appended at most once: a message that already names the variables is left alone.

That 0.1.4 shipped with a false claim is the point worth recording. The failure was not the
bug — it was writing the changelog from *intent* instead of from a verified run. Every claim
in this file is now checked against an actual call, and where a claim could rot, a test holds
it.

## 0.1.4 — 2026-10-22

Correctness release. Three defects in 0.1.3 could bite a real user; one was mine to
answer for, and it was found by an independent read-only review of the published package.

**The honest-failure rule did not hold under a proxy.** 0.1.3 promised that an unreachable
Hive produces an error naming the endpoint and the fix. That held only without a proxy. When
`http_proxy` was set — routine for Claude Code and Cursor users behind Clash, Surge or a
corporate proxy — the proxy answered `502 Bad Gateway`, which arrived as `urllib.error.HTTPError`
and took a different branch that emitted a bare `Hive returned HTTP 502 for tools/call.`
No endpoint, no App Store link, no way to self-diagnose. Reproduced against a live proxy.

- **Loopback traffic no longer goes through a proxy.** A local service has no business being
  proxied, and a proxy answering on its behalf erased the actionable part of the message.
- **Every failure path now carries the same hint**, including `HTTPError`.
- **Tests are closed against the environment.** They previously inherited `*_proxy`, so
  "14 tests OK" was only true in some shells. A regression test now asserts the message is
  identical with and without a proxy — the previous suite passed while the product was broken.
- **Error text names the platform** (`macOS only for now`) and, when the Hive is up but the
  caller has no credential, adds a pointer to `HIVE_AGENT_ID` / `HIVE_AGENT_TOKEN`. That stall
  is more common than a missing app, and the backend explains it in Chinese only.

**`tools.json` was a rewrite, not a mirror.** It was introduced as the single source of truth
but had no mechanical link to the backend, so it drifted immediately: it advertised a
`manage.limit` parameter the backend does not have, documented `say.kind` and `say.stage` as
free text when the backend enforces enums (`update` and `request`, the examples given, are
both invalid), and invented `maximum`/`minimum` bounds the backend never declared.

- **`initialize` and `tools/list` are now forwarded whenever the Hive is reachable.** Anyone
  running a Hive sees the real tool surface, constraints and protocol version, so drift is
  structurally impossible for them. The local answer is a fallback only.
- **`tools.json` is demoted to a snapshot** and corrected to match: `manage.limit` removed,
  the `say` enums added, all invented bounds removed.
- **A recorded backend snapshot now gates CI.** `tests/backend_tools_snapshot.json` holds a
  real `tools/list` response; `SchemaFidelity` compares names, parameters, enums, required
  fields and types against it, so a drifting snapshot fails without a running Hive. This
  matters because **directory crawlers have no Hive — the snapshot is exactly what they index.**
- `test_server_json_matches_tools_json` used to assert only that a package name appeared
  somewhere in the JSON. It now compares schemas; a test name larger than its assertions is
  worse than no test.

**`initialize` was over-claiming a protocol revision.** Offline, the bridge answered whatever
version the client asked for, up to `2025-06-18`, while the backend implements `2024-11-05`.
Advertising a capability the backend lacks is the same class of dishonesty as inventing a
result, which is what the honest-failure rule exists to prevent.

- Offline now answers `2024-11-05` only. With a Hive reachable, its own answer is relayed.

**Documentation**

- README states the **Hive 1.4.5+** floor: `say` and `board` do not exist in 1.4.4 and below.
  Previously the package claimed six tools with no version requirement, and a 1.4.4 user would
  have discovered the gap only at call time.
- This file gains its missing 0.1.3 row, and 0.1.2 is no longer marked `pending publish`.

## 0.1.3 — 2026-10-22

Behaviour release. Makes the bridge usable — and honest — on a machine where the Hive app is
not installed.

**Why.** 0.1.2 forwarded *every* method to the Hive. With no Hive running, even `initialize`
and `tools/list` failed, so a client could not discover the tool surface at all. Any automated
evaluation in a clean environment (directory crawlers, CI smoke tests) saw a server that could
not list its own tools, and concluded there was nothing here.

- **`initialize` and `tools/list` are now answered locally.** They are pure protocol and do not
  touch user data, so they no longer depend on the Hive being up. A clean machine can complete
  the handshake and enumerate all six tools.
- **`tools/call` still requires the Hive, and now fails honestly.** When the Hive is
  unreachable, the call returns a JSON-RPC error carrying `http_status: 502` and a message that
  names `HIVE_MCP_URL` and the App Store link. No result is fabricated, no empty list is
  returned in place of an error. Reporting "no Hive here" is true; returning an empty memory
  would be a lie the caller cannot detect.
- **`tools.json` is now the single source of truth** for the six tool schemas. Code reads it at
  runtime; the README is checked against it. It ships inside the wheel via
  `[tool.setuptools.package-data]` — without that entry an installed copy would raise
  `FileNotFoundError` on the first `tools/list`, breaking the package on exactly the machines
  that never ran it from a source checkout.
- **Added a 14-test suite** (`tests/test_bridge.py`, standard library only) pinning both
  contracts: offline behaviour, and no drift between `tools.json`, the README, `server.json`
  and `glama.json`.
- Unknown methods are forwarded rather than invented, so future MCP revisions are not silently
  swallowed.
- Added `glama.json` so directory metadata has an explicit home.

Behaviour unchanged when a Hive *is* running: traffic is still relayed verbatim, and the bridge
still stores nothing.

## 0.1.2 — 2026-10-22

Metadata release. No change to the MCP protocol surface.

- **Fixed the repository pointer.** 0.1.1 was registered in the official MCP Registry with
  `repository.url` pointing at `330899935/memory-hive-ios`, an iOS app repo. Directories and
  agents that read the Registry would classify the project as a phone app rather than an MCP
  server. The pointer now targets `330899935/memory-hive-mcp`.
- Registry entries are immutable per version, so the correction could only be shipped by
  publishing a new version and re-registering — that is the sole reason this release exists.
- `hive_mcp.__version__` now reads the installed package metadata instead of a hand-written
  literal, which had drifted to `0.1.0` while the distribution was `0.1.1`.
- Publishing now runs through GitHub Actions with PyPI Trusted Publishing (OIDC). Releases are
  tagged, not uploaded by hand, and require no long-lived API token.

## 0.1.1 — 2026-10-21

First version listed in the official MCP Registry
(`io.github.330899935/hive-memory`). Metadata and documentation release; no code changes.

- Added the `mcp-name` ownership marker to the README, which is how the official MCP Registry
  verifies that the publisher controls this PyPI package.
- PyPI metadata: `Homepage` moved from the App Store listing to the project site
  (`https://hello-fengchao.com`); App Store link kept under `App (macOS)`.
- Expanded package keywords (`mcp-server`, `ai-memory`, `claude`, `cursor`) so directory
  crawlers classify the package correctly.
- README: documented all six tools (`recall`, `propose`, `manage`, `get_context`, `say`,
  `board`), added the propose-vs-confirm model, the vault access table, and the
  "why a bridge, not a bundle" note.

> Note: 0.1.0 remains on PyPI and cannot be modified or deleted. Its README does not carry the
> ownership marker, which is why 0.1.1 exists.

## 0.1.0 — 2026-10-06

First public release.

- `memory-hive-mcp`: a stdio MCP server that relays protocol traffic to the Hive app running on
  the local machine (`127.0.0.1:8768`). The bridge stores no state.
- Configuration through `HIVE_AGENT_ID` and `HIVE_AGENT_TOKEN`; optional `HIVE_MCP_URL`.
- No third-party dependencies; requires Python 3.8+.

---

## Version history, at a glance

| Version | Date | Kind | Still installable |
| --- | --- | --- | --- |
| 0.1.6 | 2026-10-22 | TDQS-oriented tool definitions, annotations, Dockerfile, quiet stderr | yes |
| 0.1.5 | 2026-10-22 | completes 0.1.4's credential hint (it was on the wrong code path) | yes |
| 0.1.4 | 2026-10-22 | correctness: proxy-safe error path, schema fidelity, protocol floor | yes |
| 0.1.3 | 2026-10-22 | offline degradation: handshake and tools/list without a Hive | yes |
| 0.1.2 | 2026-10-22 | metadata fix (Registry repository pointer) | yes |
| 0.1.1 | 2026-10-21 | metadata + docs | yes |
| 0.1.0 | 2026-10-06 | first release | yes |
