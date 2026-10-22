"""hive_mcp — stdio bridge to a Hive running on your own machine.

Reads MCP JSON-RPC messages line-by-line from stdin, forwards each to the local
Hive service's HTTP ``/mcp`` endpoint, and writes the response back to stdout.

This is a *thin bridge*. It does not read or write your data files, and it does
not embed a memory engine. The Hive app on your machine is the single writer;
this package only relays protocol traffic to it, so there is no cross-process
write contention and no working-directory dependency.

What runs without a Hive (deliberately, see "Offline behaviour" below)
---------------------------------------------------------------------
``initialize`` and ``tools/list`` are answered *locally*, from ``tools.json``.
A client can therefore complete the MCP handshake and discover the tool surface
on a machine where the Hive app is not installed — which is exactly what an
automated directory crawler does when it evaluates this package.

``tools/call`` is **not** answered locally. It is forwarded to the Hive, and if
the Hive is unreachable the call fails with an explicit error naming the
endpoint and telling the user how to fix it. It never fabricates a successful
result. Saying "no Hive here" is the honest answer; inventing one would be a lie
that costs the caller real work.

Offline behaviour
-----------------
| Method              | Without a running Hive            |
| ------------------- | --------------------------------- |
| ``initialize``      | answered locally                  |
| ``tools/list``      | answered locally from tools.json  |
| ``tools/call``      | forwarded; fails with HTTP 502    |
| anything else       | forwarded; fails with HTTP 502    |

Credentials are injected via environment variables:

* ``HIVE_AGENT_ID``     — agent id issued by your own Hive
* ``HIVE_AGENT_TOKEN``  — access token issued by your own Hive
* ``HIVE_MCP_URL``      — Hive MCP endpoint (default http://127.0.0.1:8768/mcp)

Zero dependencies: standard library only.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:  # 版本号只认一次装出来的元数据，别再手写一份（0.1.0 就曾和 pyproject 对不上）
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("memory-hive-mcp")
    except PackageNotFoundError:  # 源码树里直接跑，没有安装元数据
        __version__ = "0.1.3"
except ImportError:  # Python < 3.8 之外的兜底
    __version__ = "0.1.3"

AGENT_ID = os.environ.get("HIVE_AGENT_ID", "")
AGENT_TOKEN = os.environ.get("HIVE_AGENT_TOKEN", "")
MCP_URL = os.environ.get("HIVE_MCP_URL", "http://127.0.0.1:8768/mcp")

SERVER_NAME = "memory-hive-mcp"

# Protocol revisions this bridge understands. If a client asks for one of these,
# echo it back; otherwise offer our newest so the client can downgrade itself.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = SUPPORTED_PROTOCOLS[0]

# JSON-RPC standard error codes plus the two MCP-relevant ones.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

# 502 = "the upstream is not there". Same number whether the Hive is stopped,
# the port is wrong, or nothing is listening: from here they are one condition.
UPSTREAM_DOWN = 502

_TOOLS_CACHE = None


def load_tools():
    """Read the tool surface from tools.json — the single source of truth.

    Cached after first read. A missing or malformed file is a packaging bug, not
    a runtime condition, so it raises rather than silently returning ``[]``:
    an empty ``tools/list`` would look like a healthy server with no tools.
    """
    global _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE
    path = Path(__file__).with_name("tools.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    tools = data["tools"]
    if not isinstance(tools, list) or not tools:
        raise ValueError(f"{path} contains no tools")
    _TOOLS_CACHE = tools
    return tools


def _rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _hive_hint():
    """One-line, actionable explanation of how to make the Hive reachable."""
    return (
        f"Hive not reachable at {MCP_URL}. {SERVER_NAME} is a bridge and ships "
        "no memory engine of its own, so it needs the Hive app running on the "
        "same machine. Install it from "
        "https://apps.apple.com/app/id6806992983, open it, then retry. "
        "Set HIVE_MCP_URL if your Hive listens somewhere else."
    )


def handle_initialize(msg_id, params):
    """Answer locally: this is pure protocol, the Hive is not involved."""
    asked = (params or {}).get("protocolVersion")
    version = asked if asked in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
    return _rpc_result(
        msg_id,
        {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            # MCP allows an instructions field; keep it short and true.
            "instructions": (
                "Local-first memory. This server is a bridge to the Hive app on "
                "the same machine; it stores nothing itself. Agents can propose "
                "memories but only the human can confirm them."
            ),
        },
    )


def handle_tools_list(msg_id, _params):
    """Answer locally from tools.json so discovery works without a Hive."""
    return _rpc_result(msg_id, {"tools": load_tools()})


def forward(payload):
    """POST the payload to the Hive. Returns a response dict, or None for 202."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MCP_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if AGENT_ID:
        req.add_header("X-Hive-Agent", AGENT_ID)
    if AGENT_TOKEN:
        req.add_header("X-Hive-Token", AGENT_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 202:
                return None  # notification: backend accepted, no response needed
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return None  # empty body: stay silent rather than emit a false error
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code == 202:
            return None
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return _rpc_error(
                payload.get("id"),
                INTERNAL_ERROR,
                f"Hive returned HTTP {e.code} for {payload.get('method')}.",
                {"http_status": e.code, "upstream": MCP_URL},
            )
    except Exception as e:  # URLError, socket timeouts, JSON errors, ...
        return None, e  # caller decides how to phrase this


def call_forward(payload):
    """forward() with the error path made explicit and honest."""
    outcome = forward(payload)
    if isinstance(outcome, tuple):  # transport failure
        _res, exc = outcome
        return _rpc_error(
            payload.get("id"),
            INTERNAL_ERROR,
            _hive_hint(),
            {
                "http_status": UPSTREAM_DOWN,
                "upstream": MCP_URL,
                "reason": str(exc),
                "method": payload.get("method"),
            },
        )
    return outcome


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
            if method == "notifications/initialized":
                continue
            # Other notifications: let the Hive see them, but stay silent.
            call_forward(payload)
            continue

        try:
            if method == "initialize":
                resp = handle_initialize(msg_id, payload.get("params"))
            elif method == "tools/list":
                resp = handle_tools_list(msg_id, payload.get("params"))
            else:
                resp = call_forward(payload)
        except Exception as e:
            resp = _rpc_error(
                msg_id,
                INTERNAL_ERROR,
                f"{SERVER_NAME} failed to handle {method}: {e}",
            )

        if resp is None:
            continue  # nothing to send back
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
