"""hive_mcp.store — a small, self-contained memory store.

Why this file exists
--------------------
Until 0.1.6 this package was a pure bridge: it relayed MCP traffic to the Hive
app and shipped no engine of its own. That was honest, and it was also the
reason an MCP directory rejected it — a server that answers ``tools/list`` but
cannot answer a single ``tools/call`` on a machine without the app is a shell.

This module is the smallest thing that is not a shell: a SQLite file on the
user's own disk that holds what ``propose`` submits, releases it when the human
confirms, and ranks it when ``recall`` asks.

What it is not
--------------
It is not the Hive app. The app adds encrypted vaults (Argon2id-wrapped key,
AES-256 at rest), daily evolution, multi-agent identity and a UI. None of that
is here, and this module does not pretend otherwise: the store is plain SQLite,
every response it produces is tagged ``"mode": "local"`` so a caller can tell
which engine answered, and ``stats()`` reports ``"encrypted": False`` outright.

Propose-only writes
-------------------
There is no ``approve`` in the MCP tool surface, here or upstream. An agent can
only submit; confirmation is a human action and lives in ``hive_mcp.cli``, run
by the person who owns the machine. Removing that gate would make the tool
cheerful and the promise worthless.

No dependencies
---------------
Standard library only. No segmenter, no embedder, no vector index. Chinese and
English are indexed by overlapping character bigrams plus Latin words, which is
coarse but needs nothing installed — see ``tokenize``.
"""

import json
import math
import os
import random
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1

PENDING = "pending"
CONFIRMED = "confirmed"
DISCARDED = "discarded"

DEFAULT_VAULT = "current"

# The defaults the MCP surface advertises, and therefore the defaults this engine
# applies. ``tools.json`` mirrors the app, and the app uses exactly these numbers
# (mcp_server.py: ``limit = 10`` for recall, ``args.get('compact', True)``,
# ``limit = 8`` per vault for get_context, ``limit = 20`` for board, and
# ``CARD_TTL_DEFAULT = 24 * 3600`` clamped to 60 s .. 30 days for say). Naming
# each promise once is what stops the two from drifting again: 0.1.8 advertised
# ``recall.limit`` 10 and answered 8, advertised ``compact`` true and answered
# false, and advertised a 24-hour card and then stored one that never expired.
# A literal repeated in a signature and in a call site is a promise with two
# sources of truth; ``tests/test_bridge.py`` now checks both the behaviour below
# and the numbers advertised in ``tools.json`` against these names.
DEFAULT_RECALL_LIMIT = 10
DEFAULT_RECALL_COMPACT = True
DEFAULT_CONTEXT_LIMIT = 8
DEFAULT_BOARD_LIMIT = 20
DEFAULT_PENDING_LIMIT = 20
DEFAULT_SAY_TTL = 24 * 3600
SAY_TTL_MIN = 60
SAY_TTL_MAX = 30 * 24 * 3600

# Soft-deleted rows stay on disk so `restore` can undo `forget`. Only
# `discard_pending` physically removes a row, because that entry was never part
# of the user's memory in the first place — the tool description promises
# "permanently drops", so it must actually drop it.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL,
    vault        TEXT NOT NULL DEFAULT 'current',
    tags         TEXT NOT NULL DEFAULT '',
    tokens       TEXT NOT NULL DEFAULT '',
    client_id    TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'mcp',
    created      REAL NOT NULL,
    confirmed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mem_state ON memories(state);
CREATE INDEX IF NOT EXISTS idx_mem_confirmed ON memories(confirmed_at);

CREATE TABLE IF NOT EXISTS board (
    id       TEXT PRIMARY KEY,
    summary  TEXT NOT NULL,
    to_agent TEXT NOT NULL DEFAULT '',
    kind     TEXT NOT NULL DEFAULT 'data',
    stage    TEXT NOT NULL DEFAULT '',
    task_id  TEXT NOT NULL DEFAULT '',
    refs     TEXT NOT NULL DEFAULT '',
    actor    TEXT NOT NULL DEFAULT '',
    created  REAL NOT NULL,
    expires  REAL
);
CREATE INDEX IF NOT EXISTS idx_board_created ON board(created);
"""

# Rows scanned by one recall. A personal memory file is measured in thousands,
# never in millions; this cap keeps a pathological database from turning a
# search into a multi-second stall, and `stats()` reports when it was hit.
SCAN_CAP = 20000

# ids get read aloud, typed into a terminal and pasted into chat, so the
# alphabet drops 0/o, 1/l/i and other pairs that survive OCR but not humans.
_ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"

_CJK = re.compile(u"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_LATIN = re.compile(r"[a-z0-9]+")

# Words that would match almost anything. Dropping them at index time and at
# query time keeps a one-character Chinese query from returning the whole file.
_STOP = {
    "the", "a", "an", "of", "to", "in", "is", "it", "and", "or", "for", "on",
    "with", "this", "that", "be", "was", "were", "as", "at", "by", "from",
    u"的", u"了", u"是", u"和", u"在", u"我", u"有", u"就", u"不", u"人", u"都",
    u"一", u"上", u"也", u"很", u"到", u"说", u"要", u"去", u"你", u"会", u"着",
    u"个", u"这", u"那", u"吗", u"呢", u"吧", u"啊",
    # Question words. They carry no topic, and because Chinese is indexed by
    # bigrams a query like "什么系统" would otherwise match every sentence
    # containing "什么" ("干什么", "为什么") as strongly as the one that
    # actually mentions a system.
    u"什么", u"怎么", u"如何", u"为什么", u"哪里", u"哪儿", u"哪些", u"哪个", u"多少",
    u"这个", u"那个", u"这些", u"那些", u"一下", u"可以",
}


class StoreError(Exception):
    """A caller mistake we can explain, as opposed to a broken database."""


# --------------------------------------------------------------------------- #
# locating and opening the database
# --------------------------------------------------------------------------- #


def default_db_path():
    """Where the memory file lives when the caller does not say.

    ``HIVE_DB`` wins so a container can point at a mounted volume. Otherwise the
    file sits under the user's home, which is the whole point: the data belongs
    to the person running the server, on the machine they are sitting at.
    """
    override = (os.environ.get("HIVE_DB") or "").strip()
    if override:
        return Path(override).expanduser()
    home = Path.home()
    try:
        exists = home.exists()
    except Exception:
        exists = False
    if exists and str(home) not in ("", "."):
        return home / ".memory-hive" / "hive.db"
    return Path.cwd() / "hive.db"


def connect(path=None):
    """Open (and initialise) the store. Creates the parent directory if needed."""
    resolved = Path(path).expanduser() if path else default_db_path()
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreError(
            "cannot create the directory for the memory database at %s (%s). "
            "Set HIVE_DB to a writable path." % (resolved, exc)
        )
    try:
        conn = sqlite3.connect(str(resolved), timeout=5.0)
    except sqlite3.Error as exc:
        raise StoreError("cannot open the memory database at %s (%s)" % (resolved, exc))
    conn.row_factory = sqlite3.Row
    # WAL matters here, not as a tuning knob: the stdio server holds a long-lived
    # connection while the human runs `memory-hive-mcp-cli approve` in another
    # process. Under the default rollback journal a write collides with a write,
    # the CLI raises "database is locked", and the confirmation silently does not
    # happen - the worst possible failure for a store whose entire promise is
    # that confirmation is deliberate.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass  # a filesystem that refuses WAL (some network mounts) still works
    conn.execute("PRAGMA synchronous=NORMAL")
    _initialise(conn)
    return conn


def _initialise(conn):
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


# --------------------------------------------------------------------------- #
# search: bigrams for Chinese, words for Latin
# --------------------------------------------------------------------------- #


def _flush_cjk(buf, out):
    """Append single characters and overlapping bigrams for one CJK run.

    Single characters alone are too coarse (a one-character query would match
    everything); bigrams alone lose one-character queries. Keeping both and
    weighting bigrams higher covers queries of any length without shipping a
    segmenter - jieba would be a 20 MB dependency in a package that promises
    none.
    """
    if not buf:
        return
    for ch in buf:
        if ch not in _STOP:
            out.append(ch)
    for i in range(len(buf) - 1):
        pair = buf[i] + buf[i + 1]
        if pair not in _STOP:
            out.append(pair)


def tokenize(text):
    """Turn mixed Chinese/Latin text into a de-duplicated list of search tokens."""
    if not text:
        return []
    low = text.lower()
    raw = []
    for word in _LATIN.findall(low):
        if len(word) >= 2 and word not in _STOP:
            raw.append(word)
    buf = []
    for ch in low:
        if _CJK.match(ch):
            buf.append(ch)
        else:
            _flush_cjk(buf, raw)
            buf = []
    _flush_cjk(buf, raw)

    seen = set()
    out = []
    for tok in raw:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _weight(token):
    """A two-character (or longer) token is evidence; a single character is a hint."""
    return 2.0 if len(token) >= 2 else 1.0


def _score(query_tokens, doc_tokens, content_lower, query_lower, age_days):
    """Relevance in the 0..~1.5 range.

    This is a ranking score, not a probability, and it is not comparable across
    queries — the README says so rather than letting a caller read 0.8 as
    "80% sure".
    """
    if not query_tokens:
        if query_lower and query_lower in content_lower:
            return 1.0
        return 0.0
    if not doc_tokens:
        # A row written before the index existed, or by another tool.
        return 1.0 if query_lower and query_lower in content_lower else 0.0
    total = sum(_weight(t) for t in query_tokens) or 1.0
    hit = sum(_weight(t) for t in query_tokens if t in doc_tokens)
    if hit <= 0:
        return 0.0
    score = hit / total
    if query_lower and len(query_lower) >= 2 and query_lower in content_lower:
        score += 0.5  # the whole query appears verbatim: strong evidence
    # Gentle recency. Recency should break ties, not overturn relevance, which
    # is why this is a 10% multiplier and not a dominant term.
    score *= 1.0 + 0.10 * math.exp(-max(0.0, age_days) / 180.0)
    return round(score, 4)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _new_id(prefix):
    return "%s_%d_%s" % (
        prefix,
        int(time.time()),
        "".join(random.choice(_ID_ALPHABET) for _ in range(4)),
    )


def _iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except (ValueError, OSError):
        return None


def _clean(text, limit):
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


def _ttl_seconds(ttl):
    """How long a board card lives, mirroring the app rather than inventing a rule.

    The app treats an absent or non-numeric ``ttl`` as 24 hours and clamps every
    value to 60 s .. 30 days, so that no card is immortal and none is invisible
    the moment it is posted. This engine used to store ``expires = NULL`` for
    anything falsy while its own ``tools.json`` said "Defaults to 24 hours" - the
    card outlived the promise silently, which is the same defect as a default
    that answers 8 where 10 was advertised. ``0`` becomes 60 s here exactly as it
    does in the app; "forever" is not an option either engine offers.
    """
    try:
        secs = int(ttl) if ttl is not None else DEFAULT_SAY_TTL
    except (TypeError, ValueError):
        secs = DEFAULT_SAY_TTL
    return max(SAY_TTL_MIN, min(SAY_TTL_MAX, secs))


class Store(object):
    """The memory file. One instance per process is enough."""

    def __init__(self, path=None):
        self.path = Path(path).expanduser() if path else default_db_path()
        self.conn = connect(self.path)

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # -- writes ------------------------------------------------------------ #

    def propose(self, content, title="", vault=DEFAULT_VAULT, tags=None, client_id=""):
        """Queue a memory for the human. Never touches confirmed memory.

        Re-submitting the same text is a no-op that returns the existing id
        instead of piling up duplicates: an agent that retries on a timeout
        should not fill the human's queue with copies of one sentence.
        """
        content = (content or "").strip()
        if not content:
            raise StoreError("propose requires non-empty `content`")
        if len(content) > 20000:
            raise StoreError("`content` is longer than 20000 characters")
        vault = _clean(vault, 40) or DEFAULT_VAULT

        existing = self.conn.execute(
            "SELECT id, state FROM memories WHERE content = ? AND state IN (?, ?) "
            "ORDER BY created DESC LIMIT 1",
            (content, PENDING, CONFIRMED),
        ).fetchone()
        if existing is not None:
            return {
                "ok": True,
                "id": existing["id"],
                "state": existing["state"],
                "duplicate": True,
                "message": (
                    "This text is already stored as %s; nothing new was queued."
                    % existing["state"]
                ),
            }

        mem_id = _new_id("mem")
        now = time.time()
        clean_title = _clean(title, 120) or _clean(content, 60)
        tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()][:20]
        self.conn.execute(
            "INSERT INTO memories(id, state, title, content, vault, tags, tokens,"
            " client_id, source, created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                mem_id,
                PENDING,
                clean_title,
                content,
                vault,
                " ".join(tag_list),
                " ".join(tokenize(clean_title + " " + content + " " + " ".join(tag_list))),
                _clean(client_id, 80),
                "mcp",
                now,
            ),
        )
        self.conn.commit()
        return {
            "ok": True,
            "id": mem_id,
            "state": PENDING,
            "vault": vault,
            "duplicate": False,
            "message": (
                "Queued for the human to confirm. It is not searchable by recall "
                "until they approve it with `memory-hive-mcp-cli approve %s`." % mem_id
            ),
        }

    def approve(self, ids=None, all_pending=False):
        """Move pending entries into confirmed memory. Human-only, via the CLI."""
        now = time.time()
        approved = []
        if all_pending:
            rows = self.conn.execute(
                "SELECT id FROM memories WHERE state = ?", (PENDING,)
            ).fetchall()
            ids = [r["id"] for r in rows]
        if not ids:
            raise StoreError("approve needs one or more ids, or --all")
        for mem_id in ids:
            row = self.conn.execute(
                "SELECT id, state FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()
            if row is None:
                raise StoreError("no memory with id %s" % mem_id)
            if row["state"] == CONFIRMED:
                continue
            if row["state"] != PENDING:
                raise StoreError(
                    "%s is %s, not pending; use restore first" % (mem_id, row["state"])
                )
            self.conn.execute(
                "UPDATE memories SET state = ?, confirmed_at = ? WHERE id = ?",
                (CONFIRMED, now, mem_id),
            )
            approved.append(mem_id)
        self.conn.commit()
        return approved

    def forget(self, mem_id):
        """Soft delete. Reversible with restore."""
        row = self._require(mem_id)
        if row["state"] == DISCARDED:
            return {"ok": True, "id": mem_id, "state": DISCARDED, "already": True}
        self.conn.execute(
            "UPDATE memories SET state = ? WHERE id = ?", (DISCARDED, mem_id)
        )
        self.conn.commit()
        return {"ok": True, "id": mem_id, "state": DISCARDED, "reversible": True}

    def restore(self, mem_id):
        """Bring a forgotten entry back as confirmed memory."""
        row = self._require(mem_id)
        if row["state"] != DISCARDED:
            raise StoreError("%s is %s, not forgotten" % (mem_id, row["state"]))
        self.conn.execute(
            "UPDATE memories SET state = ?, confirmed_at = ? WHERE id = ?",
            (CONFIRMED, time.time(), mem_id),
        )
        self.conn.commit()
        return {"ok": True, "id": mem_id, "state": CONFIRMED}

    def drop_pending(self, mem_id):
        """Physically remove an entry that was never confirmed."""
        row = self._require(mem_id)
        if row["state"] != PENDING:
            raise StoreError(
                "%s is %s; only pending entries can be dropped. Use forget for "
                "confirmed memory." % (mem_id, row["state"])
            )
        self.conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        self.conn.commit()
        return {"ok": True, "id": mem_id, "state": "dropped"}

    def say(self, summary, to="", kind="data", stage="", task_id="", refs=None,
            ttl=DEFAULT_SAY_TTL, actor=""):
        """Post to the shared board. The board is coordination, not memory."""
        summary = (summary or "").strip()
        if not summary:
            raise StoreError("say requires non-empty `summary`")
        if kind not in ("data", "handoff", "result"):
            raise StoreError("`kind` must be one of: data, handoff, result")
        if stage and stage not in ("start", "handoff", "done", "blocked"):
            raise StoreError("`stage` must be: start, handoff, done, blocked")
        now = time.time()
        msg_id = _new_id("msg")
        ttl = _ttl_seconds(ttl)
        expires = now + ttl
        self.conn.execute(
            "INSERT INTO board(id, summary, to_agent, kind, stage, task_id, refs,"
            " actor, created, expires) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                msg_id,
                _clean(summary, 8000),
                _clean(to, 80),
                kind,
                _clean(stage, 20),
                _clean(task_id, 80),
                json.dumps(refs or [], ensure_ascii=False),
                _clean(actor, 80) or "local",
                now,
                expires,
            ),
        )
        self.conn.commit()
        # `ttl` and `expires` are echoed because the effective lifetime is the
        # only way a caller can see that omitting ttl capped the card at a day.
        return {
            "ok": True,
            "id": msg_id,
            "created": _iso(now),
            "ttl": ttl,
            "expires": _iso(expires),
        }

    # -- reads ------------------------------------------------------------- #

    def recall(self, query, limit=DEFAULT_RECALL_LIMIT, vaults=None,
               compact=DEFAULT_RECALL_COMPACT):
        """Rank confirmed memory against a natural-language query."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_RECALL_LIMIT
        limit = max(1, min(limit, 50))
        query = (query or "").strip()
        query_tokens = tokenize(query)
        query_lower = query.lower()

        sql = (
            "SELECT id, title, content, vault, tags, tokens, created, confirmed_at"
            " FROM memories WHERE state = ?"
        )
        params = [CONFIRMED]
        if vaults:
            if not isinstance(vaults, (list, tuple)):
                raise StoreError("`vaults` must be an array of vault names")
            wanted = [_clean(v, 40) for v in vaults if _clean(v, 40)]
            if wanted:
                sql += " AND vault IN (%s)" % ",".join("?" * len(wanted))
                params.extend(wanted)
        sql += " ORDER BY COALESCE(confirmed_at, created) DESC LIMIT %d" % SCAN_CAP

        rows = self.conn.execute(sql, params).fetchall()
        now = time.time()
        scored = []
        for row in rows:
            doc_tokens = set((row["tokens"] or "").split())
            age_days = max(0.0, (now - (row["confirmed_at"] or row["created"])) / 86400.0)
            score = _score(
                query_tokens,
                doc_tokens,
                (row["content"] or "").lower(),
                query_lower,
                age_days,
            )
            if query_tokens and score <= 0:
                continue
            scored.append((score, row))

        scored.sort(key=lambda pair: (pair[0], pair[1]["confirmed_at"] or 0.0), reverse=True)
        picked = scored[:limit]

        results = []
        for score, row in picked:
            entry = {
                "id": row["id"],
                "title": row["title"],
                "vault": row["vault"],
                "tags": (row["tags"] or "").split(),
                "timestamp": _iso(row["confirmed_at"] or row["created"]),
                "score": score,
            }
            if compact:
                entry["content"] = _clean(row["content"], 160)
            else:
                entry["content"] = row["content"]
            results.append(entry)

        response = {
            "ok": True,
            "query": query,
            "results": results,
            "total": len(results),
            "mode": "local",
        }
        if query_tokens and not results:
            pending = self.conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE state = ?", (PENDING,)
            ).fetchone()["n"]
            response["note"] = (
                "no confirmed memory matched this query"
                + (
                    "; %d entr%s awaiting the human's approval"
                    % (pending, "y is" if pending == 1 else "ies are")
                    if pending
                    else ""
                )
            )
        return response

    def get_context(self, limit=DEFAULT_CONTEXT_LIMIT):
        """A digest of what the human has confirmed most recently."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_CONTEXT_LIMIT
        limit = max(1, min(limit, 30))
        rows = self.conn.execute(
            "SELECT id, title, content, vault, tags, confirmed_at, created"
            " FROM memories WHERE state = ?"
            " ORDER BY COALESCE(confirmed_at, created) DESC LIMIT ?",
            (CONFIRMED, limit),
        ).fetchall()
        pending = self.conn.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE state = ?", (PENDING,)
        ).fetchone()["n"]
        digest = [
            {
                "id": r["id"],
                "title": r["title"],
                "vault": r["vault"],
                "tags": (r["tags"] or "").split(),
                "timestamp": _iso(r["confirmed_at"] or r["created"]),
                "excerpt": _clean(r["content"], 200),
            }
            for r in rows
        ]
        response = {
            "ok": True,
            "digest": digest,
            "total": len(digest),
            "pending": pending,
            "mode": "local",
        }
        if pending:
            response["note"] = (
                "%d entr%s awaiting the human's approval; call manage with action "
                "list_pending to read %s."
                % (pending, "y is" if pending == 1 else "ies are", "it" if pending == 1 else "them")
            )
        if not digest and not pending:
            response["note"] = (
                "the store is empty; nothing has been proposed or confirmed yet"
            )
        return response

    def get_by_id(self, mem_id):
        row = self._require(mem_id)
        return {
            "ok": True,
            "id": row["id"],
            "state": row["state"],
            "title": row["title"],
            "content": row["content"],
            "vault": row["vault"],
            "tags": (row["tags"] or "").split(),
            "created": _iso(row["created"]),
            "confirmed_at": _iso(row["confirmed_at"]),
            "mode": "local",
        }

    def list_pending(self, limit=DEFAULT_PENDING_LIMIT):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_PENDING_LIMIT
        limit = max(1, min(limit, 100))
        rows = self.conn.execute(
            "SELECT id, title, content, vault, created FROM memories WHERE state = ?"
            " ORDER BY created ASC LIMIT ?",
            (PENDING, limit),
        ).fetchall()
        total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE state = ?", (PENDING,)
        ).fetchone()["n"]
        return {
            "ok": True,
            "pending": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "excerpt": _clean(r["content"], 200),
                    "vault": r["vault"],
                    "created": _iso(r["created"]),
                }
                for r in rows
            ],
            "total": total,
            "returned": len(rows),
            "mode": "local",
            "next": (
                "Approve or reject from a terminal: memory-hive-mcp-cli approve <id>"
                if total
                else None
            ),
        }

    def board(self, limit=DEFAULT_BOARD_LIMIT, since=0, roster=False):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_BOARD_LIMIT
        limit = max(1, min(limit, 100))
        try:
            since = float(since or 0)
        except (TypeError, ValueError):
            since = 0.0
        now = time.time()
        rows = self.conn.execute(
            "SELECT * FROM board WHERE created > ? AND (expires IS NULL OR expires > ?)"
            " ORDER BY created ASC LIMIT ?",
            (since, now, limit),
        ).fetchall()
        messages = [
            {
                "id": r["id"],
                "summary": r["summary"],
                "to": r["to_agent"] or None,
                "kind": r["kind"],
                "stage": r["stage"] or None,
                "task_id": r["task_id"] or None,
                "refs": json.loads(r["refs"] or "[]"),
                "from": r["actor"],
                "created": _iso(r["created"]),
            }
            for r in rows
        ]
        response = {
            "ok": True,
            "messages": messages,
            "total": len(messages),
            "cursor": rows[-1]["created"] if rows else since,
            "mode": "local",
        }
        if roster:
            seen = self.conn.execute(
                "SELECT actor, COUNT(*) AS n, MAX(created) AS last FROM board"
                " WHERE expires IS NULL OR expires > ? GROUP BY actor ORDER BY last DESC",
                (now,),
            ).fetchall()
            response["roster"] = [
                {"agent": r["actor"], "messages": r["n"], "last_seen": _iso(r["last"])}
                for r in seen
            ]
        return response

    def stats(self):
        counts = {}
        for state in (CONFIRMED, PENDING, DISCARDED):
            counts[state] = self.conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE state = ?", (state,)
            ).fetchone()["n"]
        board_n = self.conn.execute("SELECT COUNT(*) AS n FROM board").fetchone()["n"]
        return {
            "ok": True,
            "mode": "local",
            "database": str(self.path),
            "exists": self.path.exists(),
            "encrypted": False,
            "memories": counts,
            "board_messages": board_n,
            "schema_version": SCHEMA_VERSION,
        }

    # -- internals --------------------------------------------------------- #

    def _require(self, mem_id):
        mem_id = (mem_id or "").strip()
        if not mem_id:
            raise StoreError("this action needs an `id`")
        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (mem_id,)
        ).fetchone()
        if row is None:
            raise StoreError("no memory with id %s" % mem_id)
        return row
