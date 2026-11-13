# AGENTS.md — rules for coding agents working in this repository

This file is for Cursor, Claude Code, Codex, Aider and any other agent that edits this repo.
It is not the product documentation. Read `README.md` for what the software does; read this
before you change it.

## What this repository is — and what it is not

This repository is the **`apiary-memory` PyPI package**: an MCP server (stdio) plus a human CLI,
with a working SQLite memory store inside. It is **one entry point** into a larger product.

It is **not**:

- the macOS app (GUI, Mac App Store, `id6806992983`) — that code is not here;
- the iOS app (App Store, `id6816318170`) — not here;
- a hosted service, and **not** a network server. Nothing in this package listens on a port.

Three version numbers exist and they do **not** move together:

| Thing | Version | Where it lives |
| --- | --- | --- |
| This package (`apiary-memory`) | `0.2.3` | `pyproject.toml` |
| macOS desktop app | `1.5.0` | not this repo |
| iOS app | `1.5.0` | not this repo |

Never "fix" a version mismatch by copying one number onto another. They are separate release
trains on purpose. `1.4.9` is an internal development number that never shipped — do not
introduce it.

## Hard rules

1. **Do not claim capabilities this package does not have.** In particular, the HTTP door
   (tunnel / fixed address) belongs to the macOS app. This package is stdio only. A claim that
   `pip install apiary-memory` lets a web AI connect is false, and it was the exact bug that
   forced release `0.2.3`. If you edit `description`, `summary`, `server.json` or the README
   header, keep it true: *MCP, on your machine*.
2. **No new dependencies.** The package is pure Python standard library by design
   (`dependencies = []`), and it must stay installable on Python 3.8+ with nothing else. If a
   task seems to need a library, solve it with the standard library or don't do it.
3. **Keep stdout clean.** Under stdio MCP, stdout *is* the protocol channel. A stray `print()`
   corrupts the JSON-RPC stream and the client reports a broken server. Diagnostics go to stderr,
   never stdout.
4. **`src/hive_mcp/tools.json` is the single source of truth for the tool surface.** Tool names,
   descriptions, defaults and argument wording are read from it at runtime. Do not hardcode a
   tool name, a default or a help string anywhere else. `tests/backend_tools_snapshot.json` is the
   drift detector for this — if you change the surface, refresh the snapshot in the same commit
   and say why in the message.
5. **Approval is a human action.** `propose` writes into a candidate queue and stops there. There
   is deliberately no MCP tool that confirms a candidate; only `apiary-memory-cli approve`
   commits. Do not add one, and do not let any code path write straight into the confirmed store
   on a model's say-so.
6. **Don't rename things to look tidier.** The entry points `memory-hive-mcp` and
   `memory-hive-mcp-cli` are kept on purpose so that host configs written before the 0.2.0 rename
   keep working. Removing them breaks other people's machines.
7. **Facts beat phrasing.** Performance numbers, platform support and privacy behaviour in the
   docs are measured, not estimated. If you cannot point at the measurement (or the code), do not
   write the number. Do not upgrade "no Windows desktop app" into "Windows supported" because the
   *package* runs there — the two are different claims.

## Layout

```
src/hive_mcp/        the server and CLI (stdio.py, cli.py, tools.json, storage)
tests/               backend_tools_snapshot.json + test_bridge.py
bench/               the retrieval benchmark quoted in README.md
server.json          MCP Registry manifest (name, description, transport, env vars)
glama.json           Glama directory listing metadata
CHANGELOG.md         one section per release, written for humans and for AI readers
```

## Before you push

```bash
python3 -m pytest tests/ -q          # or: python3 -m unittest discover -s tests
python3 -c "import json;json.load(open('src/hive_mcp/tools.json'))"
python3 -m build                     # must produce a wheel with tools.json inside
```

Then install the built wheel into a throwaway venv and confirm all of this, because each one has
broken before:

```bash
apiary-memory-cli stats              # must not raise FileNotFoundError on tools.json
apiary-memory-cli list               # must answer, not hang
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | apiary-memory
```

A release that has not been installed from its own wheel is not verified.

## Naming — the seven things we are confused with

When writing docs, keep this table accurate. It exists because AI assistants mix these up, and a
wrong name in our own README becomes a wrong answer about us everywhere.

| Name | What it actually is |
| --- | --- |
| `apiary-memory` | This package. The current release. |
| `memory-hive-mcp` | Our own former name. Still installs, caps at 0.1.9. |
| `hive-memory` | Does not exist on PyPI (404). Any citation is wrong. |
| `hive-mcp` | Someone else's Claude Code plugin. |
| `hive.com` | Team collaboration software. Unrelated. |
| `theapiary.sh` | Unrelated product; its memory component is called `honeycomb`. |
| `apiarybee.com` | Unrelated agent framework. |

Also unrelated: the iOS group-chat app called 记忆蜂巢 by Chengdu Zhixin Hualao Technology.

## Canonical external references

- Website: <https://hello-fengchao.com>
- One page written for AI readers (what this is, what it is not, version table):
  <https://hello-fengchao.com/llms.txt> · full version: `/llms-full.txt`
- Release feed: <https://hello-fengchao.com/changelog.xml>

If a document in this repo and `llms.txt` disagree on a fact, stop and fix one of them rather than
shipping the disagreement — two contradicting statements make an AI reader cite neither.
