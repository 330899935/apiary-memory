"""hive_mcp — stdio bridge to a Hive running on your own machine.

Reads MCP JSON-RPC messages line-by-line from stdin, forwards each to the local
Hive service's HTTP ``/mcp`` endpoint, and writes the response back to stdout.

This is a *thin bridge*. It does not read or write your data files, and it does
not embed a memory engine. The Hive app on your machine is the single writer;
this package only relays protocol traffic to it, so there is no cross-process
write contention and no working-directory dependency.

Talk to the Hive first, answer locally only when it is unreachable
------------------------------------------------------------------
``initialize``, ``tools/list`` and ``tools/call`` are all forwarded to the Hive
when it is reachable. The client therefore always sees the tool surface and the
protocol version the backend *actually* has, instead of a copy that can drift.

When the Hive is unreachable, ``initialize`` and ``tools/list`` fall back to
answering locally (so that a directory crawler on a clean machine can still
complete the handshake and enumerate the tools), while ``tools/call`` fails with
an explicit error. **No result is ever invented.**

``tools.json`` is a *snapshot*, not a second source of truth. It exists only for
the offline fallback. Anything that changes the backend schema must change it
too — ``tests/test_bridge.py`` enforces that against a recorded backend snapshot.

Offline behaviour
-----------------
| Method                 | Hive unreachable                          |
| ---------------------- | ----------------------------------------- |
| ``initialize``         | answered locally, protocol ``2024-11-05`` |
| ``tools/list``         | answered locally from ``tools.json``      |
| ``tools/call``         | fails with HTTP 502 + a fixable message   |
| anything else          | fails with HTTP 502 + a fixable message   |
| notifications          | swallowed silently (never answered)       |

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
import urllib.parse
import urllib.request
from pathlib import Path

try:  # 版本号只认一次装出来的元数据，别再手写一份（0.1.0 就曾和 pyproject 对不上）
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("memory-hive-mcp")
    except PackageNotFoundError:  # 源码树里直接跑，没有安装元数据
        __version__ = "0.1.6"
except ImportError:  # Python < 3.8 之外的兜底
    __version__ = "0.1.6"

AGENT_ID = os.environ.get("HIVE_AGENT_ID", "")
AGENT_TOKEN = os.environ.get("HIVE_AGENT_TOKEN", "")
MCP_URL = os.environ.get("HIVE_MCP_URL", "http://127.0.0.1:8768/mcp")

SERVER_NAME = "memory-hive-mcp"
APP_STORE = "https://apps.apple.com/app/id6806992983"

# Offline we claim only what the backend actually implements. Do not raise this
# to a version the Hive does not speak: advertising a capability the backend
# lacks is the same class of dishonesty as inventing a tool result.
OFFLINE_PROTOCOL = "2024-11-05"

# Discovery must not hang. A crawler should get an answer, not a 30s stall.
DISCOVERY_TIMEOUT = 1.5
CALL_TIMEOUT = 30.0

# JSON-RPC codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

# 502 = "the upstream is not there". Same number whether the Hive is stopped,
# the port is wrong, or a proxy answers on its behalf: one condition.
UPSTREAM_DOWN = 502

_TOOLS_CACHE = None


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
    """Read the offline snapshot of the tool surface.

    This is a fallback, not the source of truth: when the Hive is reachable the
    backend's own ``tools/list`` is what the client sees. Cached after first read.
    A missing or malformed file is a packaging bug, so it raises rather than
    returning ``[]`` — an empty list would look like a healthy server with no
    tools, which is a lie a caller cannot detect.
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


def _hive_hint(reason=""):
    """One-line, actionable explanation of how to make the Hive reachable."""
    tail = f" ({reason})" if reason else ""
    return (
        f"Hive not reachable at {MCP_URL}{tail}. {SERVER_NAME} is a bridge and "
        "ships no memory engine of its own, so it needs the Hive app running on "
        f"the same machine (macOS only for now). Install it from {APP_STORE}, "
        "open it, then retry. Set HIVE_MCP_URL if your Hive listens elsewhere."
    )


def _auth_hint():
    """Appended when the Hive is up but the caller has no usable credential.

    This is the most common real-world stall: the app is installed, the user
    never registered an agent, and the backend replies in Chinese.
    """
    return (
        " No usable agent credential. Register an agent inside the Hive, then set "
        "HIVE_AGENT_ID and HIVE_AGENT_TOKEN in your MCP host config "
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
    """POST the payload to the Hive.

    Returns a response dict, or ``None`` for a 202 (notification accepted, no
    reply expected). On any transport failure returns a ready-to-send JSON-RPC
    error that names the endpoint and the fix.
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
            # The Hive reports auth failures as HTTP 200 + JSON-RPC error, so the
            # hint must be applied here too, not only in the HTTPError branch.
            return _decorate_error(json.loads(raw), payload.get("id"))
    except urllib.error.HTTPError as e:
        if e.code == 202:
            return None
        # A proxy answering 502 used to fall through to a bare "HTTP 502" with no
        # endpoint and no fix — exactly the failure mode this error path exists to
        # prevent. Try the body first (the Hive speaks JSON), then always attach
        # the actionable hint.
        try:
            parsed = json.loads(e.read().decode("utf-8"))
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("error"):
            # The Hive itself answered with a JSON-RPC error: pass it through
            # verbatim, but add the credential hint when it looks like auth.
            return _decorate_error(parsed, payload.get("id"))
        return _upstream_error(payload, e.code, f"HTTP {e.code}", f"HTTP {e.code}")
    except Exception as e:  # URLError, socket timeout, JSON decode, ...
        return _upstream_error(payload, UPSTREAM_DOWN, str(e), str(e))


def _local_initialize(msg_id):
    return _rpc_result(
        msg_id,
        {
            "protocolVersion": OFFLINE_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                "Local-first memory. This server is a bridge to the Hive app on "
                "the same machine; it stores nothing itself. Agents can propose "
                "memories but only the human can confirm them."
            ),
        },
    )


def _forwarded_result(msg_id, payload, timeout):
    """Ask the Hive; return its response dict only if it carries a usable result."""
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
            forward(payload, timeout=DISCOVERY_TIMEOUT)  # best effort, stay silent
            continue

        try:
            if method == "initialize":
                # Prefer the backend's real handshake; fall back locally so a
                # clean machine can still connect.
                resp = _forwarded_result(msg_id, payload, DISCOVERY_TIMEOUT)
                if resp is None:
                    resp = _local_initialize(msg_id)

            elif method == "tools/list":
                # Prefer the backend's live tool surface: that is what makes
                # drift structurally impossible for anyone with a Hive.
                resp = _forwarded_result(msg_id, payload, DISCOVERY_TIMEOUT)
                if resp is None:
                    resp = _rpc_result(msg_id, {"tools": load_tools()})

            else:
                resp = forward(payload)
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
