# memory-hive-mcp

<!-- mcp-name: io.github.330899935/hive-memory -->

**Local-first memory your AI agents share over MCP. Agents propose; only you confirm.**

`memory-hive-mcp` is a thin [Model Context Protocol](https://modelcontextprotocol.io) server.
It lets any MCP-capable host (Claude Desktop, Cursor, and friends) read and write *your*
memory — stored on *your* computer, by *your* Hive, never uploaded anywhere.

> **This package contains no memory engine.** It relays MCP protocol traffic to the Hive app
> already running on your machine (`127.0.0.1:8768`). Your data never passes through this
> bridge as stored state, and nothing leaves your computer.

## The problem it solves

Every major assistant now has cross-session memory, so "my AI has no memory" is no longer the
problem. The problem is what replaced it: **memory is siloed per vendor, in formats you cannot
export, governed by policies you did not set.**

| | Without Hive | With Hive |
| --- | --- | --- |
| Where memory lives | Each vendor's servers | A file on your machine |
| Switching clients | Start over from zero | Same memory, any MCP client |
| Who can write | The model, silently | The model can only `propose`; you confirm |
| Export | A "delete everything" button | It is already on your disk |
| Using 3 assistants | 3 partial memories, none yours | 1 memory, 3 readers |

**Who this is for:** people who use two or more AI assistants seriously enough to be annoyed by
re-introducing themselves. With one assistant, your vendor's built-in memory is probably enough.

## Install

```bash
pip install memory-hive-mcp
```

Requires Python 3.8+. No third-party dependencies. This installs the command
**`memory-hive-mcp`**.

(Note: an unrelated project owns the plain `hive-mcp` name on PyPI — that is not us.)

## Prerequisite: a running Hive

`memory-hive-mcp` talks to the Hive on your own machine. Get it from the Mac App Store:

**https://apps.apple.com/app/id6806992983**

Then register an agent inside the Hive (`POST /api/agent/register`) to obtain an
`agent_id` and `access_token`.

**Hive 1.4.5 or newer is required.** The `say` and `board` tools do not exist in earlier
builds — on 1.4.4 and below, only the first four tools are available, and calling `say` or
`board` will fail.

**This package will not work on its own.** It contains no memory engine, no database and
no index — it only relays MCP traffic. If the Hive app is not installed and running, you get
a connection to a bridge that has nothing behind it. That limitation is stated here on
purpose rather than buried, so nobody installs this expecting a standalone memory server.

The Hive app is **macOS only** at present. The bridge itself is pure Python and runs
anywhere, but it needs a Hive to talk to.

### What happens without a running Hive

| MCP method | Without a running Hive |
| --- | --- |
| `initialize` | Answered locally — the handshake completes, protocol `2024-11-05`. |
| `tools/list` | Answered locally — the tools below are listed. |
| `tools/call` | **Fails with an explicit error** naming `HIVE_MCP_URL` and the App Store link. No result is invented. |

With a Hive running, `initialize` and `tools/list` are answered by the Hive itself, so the
tool list, its constraints and the protocol version you see are always the real ones.

So a directory crawler, a CI check, or a curious `pip install` can still see the tool
surface on a machine with no Hive — while any real memory operation fails honestly instead
of pretending to succeed. A fabricated empty result would be worse than an error: the
caller would conclude the user's memory is empty.

## Configure your MCP host

Add the server to your host's MCP config. For Claude Desktop
(`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hive-memory": {
      "command": "memory-hive-mcp",
      "env": {
        "HIVE_AGENT_ID": "your-agent-id",
        "HIVE_AGENT_TOKEN": "your-access-token"
      }
    }
  }
}
```

### Environment variables

| Name | Required | Description |
| --- | --- | --- |
| `HIVE_AGENT_ID` | yes | Agent id issued by your own Hive. |
| `HIVE_AGENT_TOKEN` | yes | Access token issued when the agent was registered. Keep it secret. |
| `HIVE_MCP_URL` | no | Hive MCP endpoint. Defaults to `http://127.0.0.1:8768/mcp`. |

## The six tools

Every tool declares its MCP annotations, so a host knows the safety profile before reading the
description: `recall`, `get_context` and `board` are read-only; `propose` and `manage` are not,
and neither is marked destructive (`manage` reverses everything except `discard_pending`).
None of them reach the open internet — the bridge only talks to your local Hive.

| Tool | Direction | What it does |
| --- | --- | --- |
| `recall` | read | Semantic search over confirmed memory. `{"query": "...", "limit": 10, "compact": true}` — optional `vaults` narrows the search, `query_vector` switches to embedding recall. Use it for a specific question; use `get_context` at session start instead, and `manage list_pending` to review what is still unapproved. |
| `propose` | write → **pending** | Submits a memory for your approval. `{"content": "..."}` plus optional `title`, `vault`, `tags`, `vector`, and `client_id` as an idempotency key. Returns a `conflict_warning` when a near-duplicate exists. **A success response means queued, not stored** — it stays pending until you accept it. |
| `manage` | admin | Routed by `action`: `list_pending` / `forget` (soft delete, reversible with `restore`) / `restore` / `get_by_id` / `discard_pending` (the only irreversible action). Addressed by id or queue state — to search by topic, use `recall`. |
| `get_context` | session start | Pulls recent memory from your active threads and open tasks to seed a conversation. `{"limit": 8}`. Recency-based, not query-based; for a specific question use `recall`. |
| `say` | collaboration | Posts to the shared board. `{"summary": "..."}` plus optional `to`, `kind` (`data`/`handoff`/`result`), `stage` (`start`/`handoff`/`done`/`blocked`), `task_id`, `refs`, `ttl`. Requires Hive 1.4.5+. To read the board, use `board`. |
| `board` | collaboration | Reads the shared board with a cursor. `{"limit": 20, "since": "<cursor>"}` — `roster: true` also returns who is on duty. Messages expire, so an empty result may mean they aged out. Requires Hive 1.4.5+. |

A typical session: `get_context` on open → `recall` mid-conversation → when you say "keep
this", it `propose`s → you confirm in your Hive on the Mac.

## A shared ledger, not just a shared store

`say` and `board` make the Hive something more than a memory table: a place where several
agents and one human keep the same ledger.

An agent posts what it just finished (`say`); another picks it up on its own schedule
(`board` with a cursor) — no push, no waking anyone up, no orchestrator in the middle.
**Identity comes from the credential, not from a field the caller can set**, so one agent
cannot speak as another, and the human sees every hand-off in the same thread.

## Your agent can propose. It cannot decide.

This is the design point that separates this server from a memory cache.

**An agent can `propose` a memory, but it cannot confirm one.** What it writes lands in a
pending queue and stays there until *you* accept it inside your own Hive. There is no
`self-confirm`, no silent background write, no path by which a model quietly decides what
is true about you.

Memory is partitioned into vaults, and the boundary is enforced by the Hive — checked
server-side, not promised by a prompt:

| Agent type | Public vaults | Private vaults |
| --- | --- | --- |
| Cloud agent (`mcp` / `mobile`) | ✅ open | 🔒 sealed |
| Local agent (non-cloud) | ✅ open | ✅ open |

Agents missing from the registration table are sealed by default. A private vault entry is
not readable, deletable, or restorable by an agent that is not allowed to see it.

## Why a bridge, not a bundle

Your memory should have exactly one writer: the Hive on your machine. This server therefore
speaks protocol and nothing else — no data files are opened, no state is cached, no engine is
duplicated. That keeps your memory single-sourced, local, and under your control.

## Status

Alpha (`0.x`). The wire protocol is stable; configuration and tool signatures may still change.
See [CHANGELOG.md](CHANGELOG.md) for what changed between versions.

**Platforms.** The Hive app (the memory engine this bridge talks to) currently ships for
**macOS** — [App Store](https://apps.apple.com/app/id6806992983). Windows and Linux builds are
in progress; the bridge itself is pure Python and runs anywhere, but it needs a Hive to talk to.

**No benchmarks.** We deliberately do not publish retrieval benchmarks. On a retrieval
leaderboard we would lose to purpose-built vector stores, and we are not making that claim.
What we do claim is narrower and checkable: the data stays on your machine, and the write
authority stays with you.

## Links

- Repository: https://github.com/330899935/memory-hive-mcp
- Project home: https://hello-fengchao.com
- MCP integration docs: https://hello-fengchao.com/mcp.html
- Package: https://pypi.org/project/memory-hive-mcp/
- Security policy: [SECURITY.md](SECURITY.md)

## License

MIT
