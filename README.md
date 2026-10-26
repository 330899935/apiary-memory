# Memory Hive MCP

<!-- mcp-name: io.github.330899935/hive-memory -->

> **Also known as:** 记忆蜂巢 · Memory Hive · 蜂巢 · `hive-memory` (the official MCP Registry server name) · `memory-hive-mcp` (this PyPI package).
> Not affiliated with [hive.com](https://hive.com) (team collaboration) or the `hive-mcp` Claude Code plugin.

**Local-first personal memory for AI agents, over MCP.** Your agents can search what you have
confirmed and propose what they think you should keep; **only you can confirm it.** Everything
lives in one file on your own disk, and nothing is sent anywhere.

It runs on its own. A working SQLite memory store ships inside this package — no account, no
cloud, no daemon, no dependencies.

```bash
pip install memory-hive-mcp
```

---

## What you get without the app

A complete, if modest, memory server. On a machine with nothing else installed:

| Call | What happens |
| --- | --- |
| `initialize` | answered locally, protocol `2024-11-05` |
| `tools/list` | answered locally, six tools |
| `propose` | **writes to a real database** on your disk |
| `recall` | **searches that database** and returns ranked entries |
| `get_context` | a digest of what you confirmed most recently |
| `manage` | read one entry, list what is pending, forget, restore |
| `say` / `board` | a shared message board for coordinating several agents |

Storage is SQLite at `~/.memory-hive/hive.db` by default. Search is lexical — character bigrams
for Chinese, words for Latin script — so it ranks well when the words overlap and finds nothing
when they do not. There are no embeddings and no vector index. Every response says
`"mode": "local"` so you can always tell which engine answered.

**Without the app, the database is plain SQLite: it is not encrypted.** That is stated here
rather than discovered later — see [Honest limits](#honest-limits).

## What the app adds

[Memory Hive for macOS](https://apps.apple.com/app/id6806992983) is the same design with the
expensive parts built: eight vaults with four of them locked behind a PIN (Argon2id-wrapped key,
AES-256 at rest), a daily evolution log, multi-agent identity management, and a UI for reading
and confirming. It also answers MCP itself, with richer ranking.

If the app is running, this package uses it and leaves the local file alone. If it is not, the
local store answers. You do not have to choose up front:

| `HIVE_MODE` | Behaviour |
| --- | --- |
| `auto` (default) | Ask the app. Use the local store **only if the app is unreachable.** |
| `local` | Never touch the network. Always use the local store. |
| `remote` | Only the app. A connection failure is an error, not a reroute. |

One rule holds in every mode: **if the app is running and answers with an error, that error is
returned unchanged.** Falling back on a refusal would write your memory into a second database
while your client believed it had reached the app. Silent data forking is worse than an error
message, so a reply from the app is final. The fallback fires on a connection failure, and on
nothing else.

## Install

```bash
pip install memory-hive-mcp      # the server: memory-hive-mcp
                                 # the human's CLI: memory-hive-mcp-cli
```

Python 3.8 or newer. There is nothing else to install — the package uses only the standard
library, and `sqlite3` is part of it.

From a checkout:

```bash
git clone https://github.com/330899935/memory-hive-mcp
cd memory-hive-mcp
PYTHONPATH=src python -m hive_mcp.stdio
```

## Connect a client

Any MCP host that can run a stdio command will work. The minimum is the command; everything else
is optional.

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memory-hive": {
      "command": "memory-hive-mcp",
      "env": { "HIVE_MODE": "local" }
    }
  }
}
```

**Cursor** — `~/.cursor/mcp.json`, same `mcpServers` shape.

**Any host that only speaks HTTP** — start the app and point it at `http://127.0.0.1:8768/mcp`
with the headers `X-Hive-Agent` and `X-Hive-Token`. This package is the stdio path; the HTTP
endpoint belongs to the app.

## Prerequisite

**None, for the local store** — install the package and it works. A running app is a prerequisite
only for the app's own features: encrypted vaults, the evolution log, and multi-agent identity.
If you want those, install [the Hive app](https://apps.apple.com/app/id6806992983) (macOS,
version **1.4.5 or newer**) and leave `HIVE_MODE` at `auto`.

## The six tools

The six tools are the whole surface. They are the same six whether the app or the local store
answers; only the ranking behind them differs — and, for two inputs, whether they exist at all.
The app accepts precomputed embeddings (`recall.query_vector`, `propose.vector`); the local
store has no embedder, so `tools/list` omits those two wherever the local store can answer,
including under `auto` where the app might stop answering mid-session. A field that would be
accepted and silently ignored is not advertised at all.

The same rule covers wording. `recall.query` is described as the lexical ranker it is wherever
the local store can answer — words and character bigrams, no embeddings — and as the app's
index-based ranking with `query_vector` for meaning wherever only the app can answer. A
description is a claim about what will happen to the input, so it is narrowed with the inputs.

Defaults are not narrowed, they are shared. Omit `limit` and you get 10 from `recall` and 8 from
`get_context`; omit `compact` and bodies come back trimmed; omit `ttl` on `say` and the card
lives 24 hours. Both engines answer those numbers identically, because a default is a number in
the schema rather than a capability one engine lacks. `store.py` holds each one as a named
constant, and the tests compare the answer against the advertised number instead of a second
hand-written copy of it.

| Tool | Kind | What it is for |
| --- | --- | --- |
| `recall` | read | Search confirmed memory by topic. `{"query": "..."}` plus optional `limit`, `vaults`, `compact`. |
| `propose` | write | Submit a memory for the human to approve. Lands in a queue; changes no confirmed memory. |
| `manage` | read/write | Address one entry: `list_pending`, `get_by_id`, `forget`, `restore`, `discard_pending`. |
| `get_context` | read | A short digest of recent confirmed memory, to seed a conversation. |
| `say` | write | Post to the shared board so other agents see what was done or handed over. |
| `board` | read | Read the board, optionally only what arrived since your last cursor. |

Two properties are worth stating plainly, because they are the product:

- **`propose` succeeding means "queued", not "stored".** `recall` will not return it until a
  human confirms it.
- **`manage` has no `approve` action.** There is no path from an agent to a confirmed memory.
  That is not an omission; it is the design.

## Response shapes

`tools/list` is one surface for both engines, and an answer should be too: a model that has
to branch on which engine replied will eventually branch wrong. `board` therefore answers
the app's shape, field for field — `items`, `unread`, `count`, `cursor`, `reader` — and
`cursor` is the id of the newest card, the same kind of value the app returns.

The cursor is what a poll carries *across* an engine switch, which is why it gets the space.
Under `auto` one call can be answered by the app and the next by the local store. Before 0.1.9
the local engine answered a float epoch timestamp and read the app's card id with `float()`,
so a cursor fetched from the app came back as a full re-read of the board and nothing said
so; a float sent the other way matched no card id, and the app answered with its newest
`limit`, equally quietly.

A cursor can still fail to resolve: its card expired (cards live 24 hours by default), or it
was issued by the other engine, whose ids are `CB…` in the app and `msg_…` here. Neither
engine can resume from that, so both return the newest window — but this one also sets
`since_unknown` and a `note` saying which of those happened. `unread` counts every live card
after the cursor and `count` counts what the answer carries, so `unread > count` means the
page did not cover everything waiting, and the `note` says so in words.

Three shapes are **not** unified, because the engines really do hold different data:

- a `recall` entry names its vault `vault` from the local store and `wh` from the app;
- `get_context` returns one flat `digest` locally and one bucket per warehouse (`context`,
  keyed by warehouse name) from the app;
- `roster` entries share `agent_id`; locally they also carry `messages` and `last_seen`,
  while the app adds its registry card — display name, seat, leases, current task — which a
  SQLite file has no way to know.

## Confirming what agents propose

Agents propose. You confirm. The confirmation lives in a terminal, in a command you have to
type — deliberately not a tool an agent can call.

```bash
memory-hive-mcp-cli list                  # what is waiting
memory-hive-mcp-cli show <id>             # read one in full
memory-hive-mcp-cli approve <id>          # confirm it, or --all
memory-hive-mcp-cli reject <id>           # drop it for good
memory-hive-mcp-cli search "deploy key"   # search confirmed memory
memory-hive-mcp-cli forget <id>           # soft delete, reversible
memory-hive-mcp-cli restore <id>          # undo forget
memory-hive-mcp-cli stats                 # counts and the database path
memory-hive-mcp-cli export --out dump.json
memory-hive-mcp-cli path
```

`--db <path>` on any command points at a different database. `HIVE_DB` does the same from the
environment.

## Where your memory lives

`~/.memory-hive/hive.db`, unless `HIVE_DB` says otherwise. One SQLite file; copy it to back it
up, delete it to start over.

The board keeps its messages in the same file, so there is only ever one thing to back up. Cards
expire: 24 hours unless `ttl` says otherwise, clamped to between 60 seconds and 30 days, which
is what the app does too — a board that grows forever stops being a board.

## How search works, and what it does not do

Chinese text is indexed as single characters plus overlapping character bigrams; Latin text as
words. A query is scored by how much of it the entry covers, then nudged by recency — a 10%
multiplier, enough to break ties and not enough to overturn relevance.

This is a ranker, not an understander.

- **Scores are relative.** `0.8` means "closer than `0.4` for this query", not "80% confident".
  They are not comparable across queries.
- **No semantic recall.** Ask about "money" and an entry that only says "invoice" will not be
  found. Related-word coverage needs an embedder; this package has no dependencies and does not
  pretend otherwise.
- **The ranking is the one the description states.** `tools/list` says "words and character
  bigrams ... no embeddings" here, and that sentence is true of this engine. It is narrowed per
  engine like the embedding inputs are, because the app's answer to the same question differs.
- **A miss is reported as a miss.** When nothing matches, `recall` says so and mentions how many
  entries are awaiting confirmation, rather than returning a plausible neighbour.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HIVE_MODE` | `auto` | `auto`, `local`, or `remote` — see the table above |
| `HIVE_DB` | `~/.memory-hive/hive.db` | Where the local database lives |
| `HIVE_MCP_URL` | `http://127.0.0.1:8768/mcp` | The app's endpoint |
| `HIVE_AGENT_ID` | — | Agent id from the app; only for calls the app serves |
| `HIVE_AGENT_TOKEN` | — | Access token from the app; only for calls the app serves |

`HIVE_AGENT_ID` and `HIVE_AGENT_TOKEN` are **not** required to use the local store. They are
read straight from the environment and never written to disk by this package.

## Honest limits

Written down because a memory tool that oversells itself is worse than one that does less.

1. **The local store is not encrypted.** Plain SQLite. Anyone who can read your home directory
   can read it. The app's vaults are the encrypted option, and `stats` reports
   `"encrypted": false` instead of leaving you to find out.
2. **No semantic search**, as above.
3. **One writer at a time.** WAL mode lets the CLI confirm while a server is connected, but this
   is a personal memory file, not a multi-host service. Do not point two machines at one
   database over a network share.
4. **The board is coordination, not memory.** Messages are for hand-offs between agents; they
   are not searchable with `recall`.
5. **The app is macOS only.** The local store is not — it is pure Python.

## Troubleshooting

**"no confirmed memory matched this query" with entries pending.** Correct behaviour. Confirm
them: `memory-hive-mcp-cli list`, then `approve`.

**A call fails with a message about `HIVE_AGENT_ID`.** The app answered and refused the
credential. The call did **not** fall back to the local store, on purpose. Register an agent in
the app and set both variables, or set `HIVE_MODE=local` if you meant to use the local file.

**"Hive app not reachable ... and HIVE_MODE=remote forbids the local fallback".** You asked for
the app only and it is not running. Start it, or drop `HIVE_MODE=remote`.

**Where did my memory go?** `memory-hive-mcp-cli stats` prints the database path. If `HIVE_MODE`
is `auto` and the app is running, the app has been answering — the local file is untouched by
design.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The suite needs no running app and no network: it stands up its own fake backend where a real
response is required, and points `HIVE_DB` at a temporary file so a test never touches a real
memory database.

Layout:

```
src/hive_mcp/store.py   the memory engine (SQLite, stdlib only)
src/hive_mcp/stdio.py   the MCP server: routing, envelopes, the app bridge
src/hive_mcp/cli.py     the human's half: review and confirm
src/hive_mcp/tools.json the tool surface snapshot for the offline path
```

## License

MIT. Your memory file is yours; nothing here phones home.
