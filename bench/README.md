# bench — measured numbers, and the script that produced them

Every number in the main README comes from this directory. Run it against your
own Hive and you will get different numbers — that is the point. There is no
vendor benchmark here, only a script you can execute in one command.

## What it measures

| # | Metric | How |
|---|---|---|
| 1 | **Recall (top-1 / top-5 / top-20)** | Sample approved entries, feed each entry's own title back as the query, record where that entry appears in the results. |
| 2 | **Latency (p50 / p95, warm)** | 32 queries per `limit` value, timed end-to-end including the HTTP round-trip. Cold first query reported separately. |
| 3 | **Corpus size** | Entries / characters / distinct days, read directly from the plaintext `warehouse_*.json` files. |

Everything is `GET /api/search`. The script never writes to the Hive, never
touches the encrypted warehouse, and needs no credentials.

## Result — 2026-10-27

Verbatim output: [`results-2026-10-27.txt`](results-2026-10-27.txt)

| Metric | Value | Caveat |
|---|---|---|
| top-5 recall | **97.5 %** (39/40) | title-as-query, easy set — see below |
| top-1 recall | 85.0 % (34/40) | same |
| p95 latency, limit=20 | **111 ms** | warm, includes HTTP |
| p50 latency, limit=20 | 76 ms | same |
| corpus | **1,303 entries / 2,387,417 chars / 45 days** | plaintext only |

### The caveat is part of the number

Recall here is measured by feeding an entry's **own title terms** back in as the
query. That proves two things and only two things: the index contains the entry,
and it can rank that entry highly when the words match. It does **not** prove
that a person can ask in their own words and find it.

A natural-language figure is **not** part of this directory. One pilot ran
separately on 2026-10-27: plain-spoken questions, 2 of which found the right entry
in the top 5 (`bench_semantic.py`, kept outside this repo). Its per-query output
was never saved — the top-5 lists were read and judged by hand — and the write-up
records 14 questions while the script lists 15, so even the denominator is
unsettled. Treat it as an order-of-magnitude signal about a real weakness, not as
a benchmark. A proper natural-language run will be measured the same way as above
— script in this directory, raw output next to it — and will almost certainly
come in lower than 97.5 %.

Same goes for latency: these are numbers from one machine, a MacBook, with the
service running locally. A network hop to a remote host would change them.

Corpus size counts the four **plaintext** warehouses. The encrypted warehouse is
excluded because it is not readable without your keys; it is reported by byte
size only.

## Reproduce

```bash
# a running Hive on the default port
HIVE_DIR=/path/to/hive python3 bench_search.py
```

| Env | Meaning | Default |
|---|---|---|
| `HIVE_DIR` | directory holding `warehouse_*.json` | current directory |
| `HIVE_API` | search endpoint | `http://127.0.0.1:8768/api/search` |
| `HIVE_SAMPLE` | entries sampled for recall | `40` |
| `HIVE_SEED` | RNG seed, so runs are comparable | `20261027` |

Python 3.8+, standard library only. No dependencies.

```bash
HIVE_DIR=~/memory-hive HIVE_SAMPLE=100 python3 bench_search.py
```

Raising `HIVE_SAMPLE` is the cheapest way to make the recall figure less noisy.
40 samples means one entry is worth 2.5 percentage points.
