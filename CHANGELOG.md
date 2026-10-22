# Changelog

All notable changes to this project are documented here.
Versions follow [Semantic Versioning](https://semver.org/). This project is pre-1.0, so minor
versions may change configuration.

---

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
| 0.1.2 | 2026-10-22 | metadata fix (Registry repository pointer) | pending publish |
| 0.1.1 | 2026-10-21 | metadata + docs | yes |
| 0.1.0 | 2026-10-06 | first release | yes |
