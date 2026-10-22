"""hive_mcp — stdio bridge to a Hive running on your own machine.

Reads MCP JSON-RPC messages line-by-line from stdin, forwards each to the local
Hive service's HTTP ``/mcp`` endpoint, and writes the response back to stdout.

This is a *thin bridge*. It does not read or write your data files, and it does
not embed a memory engine. The Hive app on your machine is the single writer;
this package only relays protocol traffic to it, so there is no cross-process
write contention and no working-directory dependency.

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

try:  # 版本号只认一次装出来的元数据，别再手写一份（0.1.0 就曾和 pyproject 对不上）
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("memory-hive-mcp")
    except PackageNotFoundError:  # 源码树里直接跑，没有安装元数据
        __version__ = "0.1.2"
except ImportError:  # Python < 3.8 之外的兜底
    __version__ = "0.1.2"

AGENT_ID = os.environ.get("HIVE_AGENT_ID", "")
AGENT_TOKEN = os.environ.get("HIVE_AGENT_TOKEN", "")
MCP_URL = os.environ.get("HIVE_MCP_URL", "http://127.0.0.1:8768/mcp")


def forward(payload):
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
            return {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {"code": -32603, "message": f"HTTP {e.code}"},
            }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32603, "message": f"forward failed: {e}"},
        }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        resp = forward(payload)
        if resp is None:
            continue  # nothing to send back for notifications
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
