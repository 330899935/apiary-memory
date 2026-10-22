# Changelog

All notable changes to this project are documented here.
Versions follow [Semantic Versioning](https://semver.org/). This project is pre-1.0, so minor
versions may change configuration.

---

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
