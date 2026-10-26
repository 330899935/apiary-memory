"""hive_mcp — a local-first memory server that speaks MCP over stdio.

Two engines, one tool surface
-----------------------------
This package carries its own memory store (:mod:`hive_mcp.store`): a SQLite
file in the user's home directory, holding what ``propose`` submits, releasing
it when the human confirms, and ranking it when ``recall`` asks. A machine with
nothing else installed therefore gets a server that can genuinely store and
retrieve — not just one that describes tools it cannot run.

If the Hive app is running on the same machine, the bridge prefers it: the app
adds encrypted vaults, daily evolution, multi-agent identity and a UI, and the
client should see the richer surface. The local store is then left untouched.

| ``HIVE_MODE``   | behaviour                                            |
| --------------- | ---------------------------------------------------- |
| ``auto`` (default) | ask the app; use the local store only if it is unreachable |
| ``local``       | never touch the network; always use the local store   |
| ``remote``      | only the app; a connection failure is an error        |

Falling back only on *unreachable*, never on *refused*
------------------------------------------------------
This is the one routing rule that must not be relaxed. If the app is running
and answers with an error — a missing agent credential, a bad argument — the
error is returned to the caller verbatim. Silently retrying against the local
store would write the user's memory into a second database that nothing
displays, while their client believed it had reached the app. Silent data
forking is worse than any error message, so a response from the app is always
final.

Writes are proposals
--------------------
An agent can call ``propose``; it cannot confirm. There is no approve action in
the MCP surface here or upstream — confirmation is a human action, and it lives
in ``memory-hive-mcp-cli`` (see :mod:`hive_mcp.cli`). Removing that gate would
make the tool cheerful and the promise worthless.

Offline behaviour
-----------------
| Method          | app unreachable                                        |
| --------------- | ------------------------------------------------------ |
| ``initialize``  | answered locally, protocol ``2024-11-05``              |
| ``tools/list``  | answered locally from ``tools.json``, minus app-only inputs and wording |
| ``tools/call``  | answered by the local store (real storage, real search) |
| notifications   | swallowed silently (never answered)                    |

``tools.json`` is a *snapshot* of the tool surface, not a second source of truth
for behaviour. When the app is reachable its own ``tools/list`` is what the
client sees, so drift is structurally impossible there; offline the snapshot is
the contract. ``tests/`` enforces that the two agree.

Advertise only inputs some engine in this session can honour
------------------------------------------------------------
``tools.json`` mirrors the app, and the app's surface offers two embedding
inputs — ``recall.query_vector`` and ``propose.vector`` — which the app really
implements and the local store really does not (``store.py``: "No segmenter, no
embedder, no vector index"). Relayed unchanged to a machine the local engine
serves, ``tools/list`` becomes a promise ``tools/call`` cannot keep: the caller
supplies an embedding, gets lexical results, and is told nothing. 0.1.8 shipped
exactly that, which is the same class of dishonesty as inventing a tool result.

Prose is the same defect in another medium: a description is a claim about what
will happen when the input is used, and "Matched semantically, not by substring"
is true of the app's vector path and false of an engine that ranks by word and
character-bigram overlap. The two maps below are therefore applied together -
``LOCAL_UNSUPPORTED_ARGUMENTS`` for inputs, ``LOCAL_PROPERTY_TEXT`` for wording.

The served list is narrowed to the engines that can actually answer a call in
this session:

| ``HIVE_MODE`` | served ``tools/list``                                  |
| ------------- | ------------------------------------------------------ |
| ``local``     | snapshot, minus embedding inputs, local wording        |
| ``auto``      | the app's answer, minus embedding inputs, local wording |
| ``remote``    | the app's answer **verbatim**, embedding inputs intact  |

Defaults are unified rather than narrowed. Both engines answer an omitted
``recall.limit`` with 10, an omitted ``recall.compact`` with true, and an
omitted ``say.ttl`` with 24 hours, because a default is a number in the schema,
not a capability one engine lacks. ``store.py`` holds each one as a named
constant and ``tests/test_bridge.py`` checks the advertised number against it.

``auto`` narrows even when the app answers, and that is the deliberate part.
Auto may route any later ``tools/call`` to the local store the moment the app
stops answering, so a list fetched while the app was alive has to stay true
after it dies. Widening later — ``remote``, or the app coming back — is safe: a
capability that appears mid-session was never promised and nothing cached can
be betrayed by it. Narrowing later is the failure this rule exists to prevent:
the client cached the richer list, keeps sending an embedding, and every call
degrades silently. Declaring the loss in each *response* (an ``ignored`` field)
was the alternative; it states the loss after the model has already acted on
the promise, so removing the input upstream of the decision is the cheaper
honesty. A mode-independent list also means one surface to document, and the
fields never blink as the app starts or stops.

One cursor in the answer, and no silent restart
----------------------------------------------
Inputs are narrowed; the *answer* is aligned, because a caller must not have to
branch on which engine replied. ``board`` returns the app's shape — ``items``,
``unread``, ``count``, ``cursor``, ``reader`` — and its ``cursor`` is a card id,
the same string ``mcp_server.py``'s ``_tool_board`` returns.

Until now it was the opposite, and ``auto`` is what made it bite. This engine
answered ``messages``/``total`` and a float epoch cursor, and parsed ``since``
with ``float()``. A caller that polled while the app answered got a card id,
kept it, and lost the app — at which point ``float('CB2026...')`` raised, the
exception was swallowed into ``since = 0``, and the entire board came back as if
nothing had been read, with no field saying the cursor had been dropped. A float
sent the other way matched no card id in the app, which quietly answered with
its newest ``limit``. Both directions failed silently, which is the one thing
this module refuses everywhere else.

One cursor type removes the misread. It cannot remove the case behind it: the
cursor's card may be gone — expired, trimmed, or issued by the other engine,
whose ids are ``CB…`` against this store's ``msg_…``. Neither engine can resume
from that, so both return their newest window; the app says nothing about it, and
this engine adds ``since_unknown`` and a ``note`` naming the reason. Restarting is
allowed; restarting silently is not.

Two answer shapes stay deliberately unequal, because the engines hold different
data: ``recall`` entries name their vault ``vault`` here and ``wh`` in the app,
and ``get_context`` returns one flat ``digest`` here against one bucket per
warehouse upstream. The README states both, so the difference is read rather
than discovered mid-session.

Configuration
-------------
* ``HIVE_MODE``         — ``auto`` (default), ``local``, ``remote``
* ``HIVE_DB``           — path to the memory file (default ``~/.memory-hive/hive.db``)
* ``HIVE_AGENT_ID``     — agent id issued by the Hive app, for remote calls
* ``HIVE_AGENT_TOKEN``  — access token issued by the Hive app, for remote calls
* ``HIVE_MCP_URL``      — app endpoint (default ``http://127.0.0.1:8768/mcp``)

Zero dependencies: standard library only.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Bumped with pyproject.toml. Only used when neither a source checkout nor
# installed metadata can answer; tests assert it matches to keep the two from
# drifting the way 0.1.0 did.
_FALLBACK_VERSION = "0.1.8"


def _resolve_version():
    """Report the version of the code that is actually running.

    ``importlib.metadata`` describes what pip installed, not what is on
    ``sys.path``. In a source checkout that sits beside an older release in
    site-packages those two disagree, and the installed number is the
    misleading one: you edit 0.1.7, run the tree, and read 0.1.6. A checkout
    therefore reads its own ``pyproject.toml`` first.
    """
    try:
        manifest = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        if manifest.is_file():
            in_project = False
            for line in manifest.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("["):
                    in_project = stripped == "[project]"
                    continue
                if in_project and stripped.startswith("version"):
                    value = stripped.partition("=")[2].strip().strip('"').strip("'")
                    if value:
                        return value
    except OSError:
        pass
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("memory-hive-mcp")
    except Exception:
        return _FALLBACK_VERSION


__version__ = _resolve_version()

from hive_mcp.store import (  # noqa: E402
    DEFAULT_BOARD_LIMIT,
    DEFAULT_CONTEXT_LIMIT,
    DEFAULT_PENDING_LIMIT,
    DEFAULT_RECALL_COMPACT,
    DEFAULT_RECALL_LIMIT,
    DEFAULT_SAY_TTL,
    Store,
    StoreError,
    default_db_path,
)

# Inputs the app accepts and the local store cannot consume, per tool.
#
# The local engine takes neither list: ``Store.recall`` has no parameter for a
# precomputed vector and ``Store.propose`` stores none, so a field left in the
# list would be accepted, ignored, and never mentioned. Kept as a map rather
# than a blanket strip so the reason survives review: each entry is a real
# engine gap, not an oversight. ``tests/test_bridge.py`` checks every pair here
# against both ``tools.json`` and the store's actual signatures, so this map
# cannot quietly outlive the gap it describes.
LOCAL_UNSUPPORTED_ARGUMENTS = {
    "recall": ("query_vector",),
    "propose": ("vector",),
}

# Property descriptions that are true of the app and false of the local engine.
# Same rule as the map above, applied to prose: a description is a promise about
# what the input will do, and serving the app's promise from an engine that
# cannot keep it is the same dishonesty as advertising an input that gets
# dropped.
#
# ``recall.query`` is the case that forced this. The app *can* rank by meaning,
# but only through ``query_vector`` (cosine, see ``_vector_recall``); its plain
# text path is an inverted index over words and character bigrams with synonym
# expansion and recency decay (``index.py``: ``search_memories``). The local
# ranker is the same family without the synonyms: token overlap, a verbatim
# bonus, a recency nudge. Neither engine matches "semantically, not by
# substring" on text alone, so ``tools.json`` no longer claims it; it states
# what the app does and points at ``query_vector`` for the richer claim, which
# exists exactly where that input exists (``remote``). This override says what
# the local engine can do, including the part a caller has to plan around: no
# shared wording means no hit.
#
# ``tests/test_bridge.py`` checks every entry against ``tools.json``, so an
# override cannot outlive the property it replaces.
LOCAL_PROPERTY_TEXT = {
    "recall": {
        "query": (
            "Natural-language search text, e.g. 'what did we decide about the "
            "pricing page'. Ranked by how much of it the entry's words and "
            "character bigrams cover, plus a small recency nudge and a bonus "
            "when the whole query appears verbatim. This engine has no "
            "embeddings, so an entry that shares no wording with the query "
            "will not be found - add plain keywords alongside the sentence."
        ),
    },
}

AGENT_ID = os.environ.get("HIVE_AGENT_ID", "")
AGENT_TOKEN = os.environ.get("HIVE_AGENT_TOKEN", "")
MCP_URL = os.environ.get("HIVE_MCP_URL", "http://127.0.0.1:8768/mcp")

# An unrecognised value is treated as `auto` rather than failing: a typo in an
# MCP host's env block should not produce a server that refuses to start.
MODE = (os.environ.get("HIVE_MODE") or "auto").strip().lower()
if MODE not in ("auto", "local", "remote"):
    MODE = "auto"

SERVER_NAME = "memory-hive-mcp"
APP_STORE = "https://apps.apple.com/app/id6806992983"

# Offline we claim only what we actually implement. Do not raise this to a
# protocol version the local path does not speak: overstating a capability is
# the same class of dishonesty as inventing a tool result.
OFFLINE_PROTOCOL = "2024-11-05"

# Discovery must not hang. A crawler should get an answer, not a stall.
DISCOVERY_TIMEOUT = 1.5
CALL_TIMEOUT = 30.0

# JSON-RPC codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
INTERNAL_ERROR = -32603

# 502 = "the upstream is not there". Same number whether the app is stopped,
# the port is wrong, or a proxy answers on its behalf: one condition, and the
# only condition that justifies falling back to the local store.
UPSTREAM_DOWN = 502

_TOOLS_CACHE = None

LOCAL_TOOLS = ("recall", "propose", "manage", "get_context", "say", "board")


def _is_loopback(url):
    """True when the endpoint is on this machine.

    Loopback traffic must never go through an HTTP proxy: (a) it is semantically
    wrong, (b) when a proxy is configured it answers 502 on the proxy's behalf,
    which used to strip the actionable part off our error message.
    """
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0") or host.startswith(
        "127."
    )


def _opener():
    """Proxy-free opener for loopback, normal opener otherwise."""
    if _is_loopback(MCP_URL):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def load_tools():
    """Read the bundled snapshot of the tool surface.

    Cached after first read. A missing or malformed file is a packaging bug, so
    it raises rather than returning ``[]`` — an empty list would look like a
    healthy server with no tools, which is a lie a caller cannot detect.
    """
    global _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE
    path = Path(__file__).with_name("tools.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    tools = data["tools"]
    if not isinstance(tools, list) or not tools:
        raise ValueError("%s contains no tools" % path)
    _TOOLS_CACHE = tools
    return tools


def _narrow_for_local(tools):
    """Copy ``tools`` with what the local engine cannot honour dropped or restated.

    Two kinds of narrowing in one function, because they answer one question -
    what can the engine serving this session really do with this tool? -
    and splitting them would eventually apply one where the other was
    forgotten:

    * an input the engine never reads is removed (``LOCAL_UNSUPPORTED_ARGUMENTS``);
    * a property description the engine would falsify is replaced
      (``LOCAL_PROPERTY_TEXT``).

    Returns new dicts rather than editing in place: ``load_tools()`` hands out
    one cached list, and the same process may serve a relayed app list later in
    the session. Trimming the cache would make a remote answer lose fields
    because a local answer had been served earlier.

    Tolerant of shape: anything unrecognised is passed through untouched rather
    than dropped, because losing the app's answer would be a worse failure than
    one field too many.
    """
    out = []
    for tool in tools:
        if not isinstance(tool, dict):
            out.append(tool)
            continue
        name = tool.get("name")
        droppable = LOCAL_UNSUPPORTED_ARGUMENTS.get(name) or ()
        restated = LOCAL_PROPERTY_TEXT.get(name) or {}
        schema = tool.get("inputSchema")
        props = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(props, dict):
            out.append(tool)
            continue
        dropped = [prop for prop in droppable if prop in props]
        rewritten = [prop for prop in restated if prop in props]
        if not dropped and not rewritten:
            out.append(tool)
            continue
        narrowed_props = {}
        for prop_name, spec in props.items():
            if prop_name in dropped:
                continue
            text = restated.get(prop_name)
            if text and isinstance(spec, dict):
                spec = dict(spec)
                spec["description"] = text
            narrowed_props[prop_name] = spec
        narrowed = dict(tool)
        narrowed["inputSchema"] = dict(schema)
        narrowed["inputSchema"]["properties"] = narrowed_props
        out.append(narrowed)
    return out


def _narrow_tools_result(resp):
    """Apply the same narrowing to a ``tools/list`` result relayed from the app.

    Tolerant of shape: anything we do not recognise is passed through untouched
    rather than dropped, because losing the app's answer would be a worse
    failure than advertising one field too many.
    """
    if not isinstance(resp, dict) or not isinstance(resp.get("result"), dict):
        return resp
    tools = resp["result"].get("tools")
    if not isinstance(tools, list):
        return resp
    narrowed = dict(resp)
    narrowed["result"] = dict(resp["result"])
    narrowed["result"]["tools"] = _narrow_for_local(tools)
    return narrowed


def _rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _hive_hint(reason=""):
    """One-line, actionable explanation for the remote-only failure path."""
    tail = " (%s)" % reason if reason else ""
    return (
        "Hive app not reachable at %s%s, and HIVE_MODE=remote forbids the local "
        "fallback. Either start the Hive app (macOS: %s) or set HIVE_MODE=local "
        "to use the local memory file at %s. Set HIVE_MCP_URL if your Hive "
        "listens elsewhere." % (MCP_URL, tail, APP_STORE, _db_path_hint())
    )


def _db_path_hint():
    """The configured database path, without creating anything as a side effect."""
    try:
        return str(default_db_path())
    except Exception:
        return os.environ.get("HIVE_DB") or "~/.memory-hive/hive.db"


def _auth_hint():
    """Appended when the app is up but the caller has no usable credential.

    This is the most common real-world stall: the app is installed, the user
    never registered an agent, and the backend replies in Chinese.
    """
    return (
        " No usable agent credential. Register an agent inside the Hive app, then "
        "set HIVE_AGENT_ID and HIVE_AGENT_TOKEN in your MCP host config "
        "(see the Environment variables table in the README)."
    )


_LOOKS_LIKE_AUTH = ("agent_id", "token", "鉴权", "凭证", "身份", "401", "403")


def _upstream_error(payload, code, detail, reason=""):
    """Uniform, honest failure for anything we could not deliver."""
    body = _hive_hint(reason)
    try:
        text = json.dumps(detail, ensure_ascii=False) if detail is not None else ""
    except Exception:
        text = str(detail)
    if text and any(k in text for k in _LOOKS_LIKE_AUTH):
        body += _auth_hint()
    return _rpc_error(
        payload.get("id"),
        INTERNAL_ERROR,
        body,
        {
            "http_status": code,
            "upstream": MCP_URL,
            "method": payload.get("method"),
        },
    )


def _decorate_error(parsed, msg_id):
    """Add the credential hint to an auth-looking JSON-RPC error, else pass through.

    The Hive answers auth failures as **HTTP 200 with a JSON-RPC error** (code
    -32001), not as an HTTP status. Checking only the ``HTTPError`` branch left
    the most common real-world stall — app installed, agent never registered, and
    a Chinese-only message — with no pointer to HIVE_AGENT_ID / HIVE_AGENT_TOKEN.
    """
    if not isinstance(parsed, dict) or not parsed.get("error"):
        return parsed
    err = dict(parsed["error"])
    msg = str(err.get("message", ""))
    if any(k in msg for k in _LOOKS_LIKE_AUTH) and "HIVE_AGENT_ID" not in msg:
        err["message"] = msg + _auth_hint()
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def forward(payload, timeout=CALL_TIMEOUT):
    """POST the payload to the Hive app.

    Returns a response dict, or ``None`` for a 202 (notification accepted, no
    reply expected). On any transport failure returns a ready-to-send JSON-RPC
    error carrying ``data.http_status``, which is how the caller distinguishes
    "nothing is listening" from "the app said no".
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MCP_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if AGENT_ID:
        req.add_header("X-Hive-Agent", AGENT_ID)
    if AGENT_TOKEN:
        req.add_header("X-Hive-Token", AGENT_TOKEN)
    try:
        with _opener().open(req, timeout=timeout) as resp:
            if resp.status == 202:
                return None  # notification: backend accepted, no response needed
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return None  # empty body: stay silent rather than emit a false error
            # The app reports auth failures as HTTP 200 + JSON-RPC error, so the
            # hint must be applied here too, not only in the HTTPError branch.
            return _decorate_error(json.loads(raw), payload.get("id"))
    except urllib.error.HTTPError as e:
        if e.code == 202:
            return None
        # A proxy answering 502 used to fall through to a bare "HTTP 502" with no
        # endpoint and no fix — exactly the failure mode this error path exists to
        # prevent. Try the body first (the app speaks JSON), then always attach
        # the actionable hint.
        try:
            parsed = json.loads(e.read().decode("utf-8"))
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("error"):
            # The app itself answered: pass it through verbatim (plus the
            # credential hint when it looks like auth). No fallback here.
            return _decorate_error(parsed, payload.get("id"))
        return _upstream_error(payload, e.code, "HTTP %s" % e.code, "HTTP %s" % e.code)
    except Exception as e:  # URLError, socket timeout, JSON decode, ...
        return _upstream_error(payload, UPSTREAM_DOWN, str(e), str(e))


# --------------------------------------------------------------------------- #
# the local engine
# --------------------------------------------------------------------------- #


def _tool_result(payload_dict, is_error=False):
    """Wrap a local answer in the same envelope the app uses.

    The client must not be able to tell the two engines apart by shape, only by
    the ``"mode"`` field inside the payload.
    """
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload_dict, ensure_ascii=False)}
        ],
        "isError": bool(is_error),
    }


def local_call(name, arguments):
    """Run one tool against the local store. Returns the MCP result envelope.

    Every default here is a named constant from ``store.py`` rather than a
    literal, because these are the numbers ``tools.json`` advertises. The
    literals used to be typed twice - ``limit=10`` in the schema and
    ``arguments.get("limit", 8)`` here - and a caller that omitted the argument
    got the smaller, unadvertised answer with nothing to indicate the
    substitution.
    """
    if name not in LOCAL_TOOLS:
        return _tool_result(
            {"ok": False, "error": "unknown tool %r" % name, "mode": "local"}, True
        )
    arguments = arguments or {}
    store = Store()
    try:
        if name == "recall":
            payload = store.recall(
                arguments.get("query", ""),
                limit=arguments.get("limit", DEFAULT_RECALL_LIMIT),
                vaults=arguments.get("vaults"),
                compact=bool(arguments.get("compact", DEFAULT_RECALL_COMPACT)),
            )
        elif name == "propose":
            payload = store.propose(
                arguments.get("content", ""),
                title=arguments.get("title", ""),
                vault=arguments.get("vault", "current"),
                tags=arguments.get("tags"),
                client_id=arguments.get("client_id", ""),
            )
        elif name == "manage":
            action = (arguments.get("action") or "").strip()
            mem_id = arguments.get("id", "")
            if action == "list_pending":
                payload = store.list_pending(
                    limit=arguments.get("limit", DEFAULT_PENDING_LIMIT)
                )
            elif action == "get_by_id":
                payload = store.get_by_id(mem_id)
            elif action == "forget":
                payload = store.forget(mem_id)
            elif action == "restore":
                payload = store.restore(mem_id)
            elif action == "discard_pending":
                payload = store.drop_pending(mem_id)
            else:
                raise StoreError(
                    "`action` must be one of: list_pending, forget, restore, "
                    "get_by_id, discard_pending (got %r)" % action
                )
        elif name == "get_context":
            payload = store.get_context(
                limit=arguments.get("limit", DEFAULT_CONTEXT_LIMIT)
            )
        elif name == "say":
            payload = store.say(
                arguments.get("summary", ""),
                to=arguments.get("to", ""),
                kind=arguments.get("kind", "data"),
                stage=arguments.get("stage", ""),
                task_id=arguments.get("task_id", ""),
                refs=arguments.get("refs"),
                ttl=arguments.get("ttl", DEFAULT_SAY_TTL),
                actor=AGENT_ID,
            )
        else:  # board
            # `since` is the card id `board` returned last time, the same string
            # the app hands out, so it survives a change of engine. Passing a
            # default of 0 here is what used to make a foreign cursor a silent
            # full re-read; `Store.board` now reports a cursor it cannot place.
            payload = store.board(
                limit=arguments.get("limit", DEFAULT_BOARD_LIMIT),
                since=arguments.get("since", ""),
                roster=bool(arguments.get("roster")),
                reader=AGENT_ID,
            )
    except StoreError as exc:
        return _tool_result({"ok": False, "error": str(exc), "mode": "local"}, True)
    finally:
        store.close()
    # Tagged in one place rather than in every method: the docstring promises
    # every local answer says which engine produced it, and a promise enforced
    # at seventeen return statements is a promise that will drift.
    if isinstance(payload, dict) and "mode" not in payload:
        payload["mode"] = "local"
    return _tool_result(payload)


def _call_tool(payload, name, arguments):
    """Route one ``tools/call`` to the app or to the local store.

    The order and the fallback condition are the whole design, so they are
    stated once here rather than scattered:

    * ``local``   — never network.
    * ``remote``  — never local; a connection failure is reported as such.
    * ``auto``    — ask the app first. Fall back only when nothing answered
      (``http_status == 502``). An error the app itself produced is returned
      unchanged: retrying it locally would split the user's memory across two
      databases, one of which nothing displays.
    """
    if MODE == "local":
        # Wrap in the same JSON-RPC envelope the remote path produces: a tool
        # result that is not inside `result` is not a valid MCP response.
        return _rpc_result(payload.get("id"), local_call(name, arguments))

    resp = forward(payload)
    if resp is None:
        # Notification-shaped call: nothing to answer.
        return None
    if "error" not in resp:
        return resp

    data = resp.get("error", {}).get("data") or {}
    unreachable = data.get("http_status") == UPSTREAM_DOWN
    if MODE == "auto" and unreachable:
        return _rpc_result(payload.get("id"), local_call(name, arguments))
    return resp


def _local_initialize(msg_id):
    return _rpc_result(
        msg_id,
        {
            "protocolVersion": OFFLINE_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                "Local-first personal memory. Agents may propose entries; only "
                "the human confirms them (memory-hive-mcp-cli approve), so "
                "recall searches confirmed memory only, and the tool list "
                "advertises only inputs and wording the engine serving this "
                "session can honour. This server keeps its "
                "data in a SQLite file on this machine and sends nothing "
                "offline; if the Hive app is running, this bridge uses it "
                "instead and leaves the local file untouched. Responses state "
                "which engine answered in their `mode` field."
            ),
        },
    )


def _forwarded_result(msg_id, payload, timeout):
    """Ask the app; return its response dict only if it carries a usable result."""
    resp = forward(payload, timeout=timeout)
    if isinstance(resp, dict) and resp.get("result"):
        return resp
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            sys.stdout.write(
                json.dumps(_rpc_error(None, PARSE_ERROR, "Parse error")) + "\n"
            )
            sys.stdout.flush()
            continue

        if not isinstance(payload, dict):
            sys.stdout.write(
                json.dumps(_rpc_error(None, INVALID_REQUEST, "Invalid Request")) + "\n"
            )
            sys.stdout.flush()
            continue

        method = payload.get("method")
        msg_id = payload.get("id")

        # Notifications carry no id and must never be answered.
        if msg_id is None and method is not None:
            if MODE != "local":
                forward(payload, timeout=DISCOVERY_TIMEOUT)  # best effort, silent
            continue

        try:
            if method == "initialize":
                resp = None
                if MODE != "local":
                    # Prefer the app's real handshake; answer locally otherwise.
                    resp = _forwarded_result(msg_id, payload, DISCOVERY_TIMEOUT)
                if resp is None:
                    resp = _local_initialize(msg_id)

            elif method == "tools/list":
                resp = None
                if MODE != "local":
                    # Prefer the app's live tool surface: that is what makes
                    # drift structurally impossible for anyone with the app.
                    resp = _forwarded_result(msg_id, payload, DISCOVERY_TIMEOUT)
                if resp is not None and MODE == "remote":
                    # The app is the only engine remote can route to, so its
                    # own list is exactly true. Relay it verbatim.
                    pass
                elif resp is not None:
                    # auto: the app answered, but it may stop answering before
                    # the next tools/call. Narrow now so the list cannot turn
                    # into a lie mid-session (see the module docstring).
                    resp = _narrow_tools_result(resp)
                else:
                    resp = _rpc_result(
                        msg_id, {"tools": _narrow_for_local(load_tools())}
                    )

            elif method == "tools/call":
                params = payload.get("params") or {}
                resp = _call_tool(
                    payload, params.get("name"), params.get("arguments") or {}
                )

            elif MODE == "local":
                # Nothing else is implemented locally, and inventing an answer
                # would be worse than admitting it.
                resp = _rpc_error(
                    msg_id,
                    INTERNAL_ERROR,
                    "method %r is not implemented by the local engine; it is "
                    "served by the Hive app only." % method,
                    {"mode": "local", "method": method},
                )

            else:
                resp = forward(payload)
        except Exception as e:
            resp = _rpc_error(
                msg_id,
                INTERNAL_ERROR,
                "%s failed to handle %s: %s" % (SERVER_NAME, method, e),
            )

        if resp is None:
            continue  # nothing to send back
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
