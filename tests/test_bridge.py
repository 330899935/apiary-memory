"""Tests for the bridge. Standard library only — no test dependencies.

Seven groups, each pinning a contract that has already been broken once:

``OfflineBehaviour``
    What a machine with no Hive app gets. The handshake and tool discovery must
    work, and since 0.1.7 a real tool call must really store and retrieve — the
    package carries its own engine. What must never happen is inventing a
    result, so the honest-failure rule is still pinned, under ``HIVE_MODE=remote``
    where falling back would be wrong.

``ProxyEnvironment``
    2026-10-22 regression: with an HTTP proxy in the environment, a loopback
    call came back as a bare "HTTP 502" with no endpoint and no fix. Tests were
    also not closed, so "14 tests OK" depended on the runner's shell.

``LocalCapabilitySurface``
    2026-10-26 regression, two cases of one defect. ``tools/list`` advertised
    ``recall.query_vector`` and ``propose.vector`` from an engine with no
    embedder, and described a bigram ranker as "semantically, not by substring".
    Inputs and wording are both narrowed to the engine that will answer.

``AdvertisedDefaults``
    Same defect, numbers instead of words: the schema's defaults and the
    engine's defaults were maintained separately, so an omitted ``limit``,
    ``compact`` or ``ttl`` answered something the schema never promised. Each
    test compares the answer against the number ``tools.json`` declares.

``BoardCursor``
    2026-10-26 regression, the same defect on the answer side. ``board``
    answered ``messages``/``total`` and a float epoch cursor while the schema
    declared ``since`` as a string and the app returned a card id. Under ``auto``
    a cursor from one engine reached the other, failed to parse, and came back as
    a silent full re-read. One cursor type in the answer, and a cursor that
    cannot be placed says so.

``SchemaFidelity``
    2026-10-22 regression: ``tools.json`` was a hand-rewrite, not a mirror. It
    advertised a ``manage.limit`` the backend does not have and wrote ``say.kind``
    / ``say.stage`` as free text when the backend enforces enums. Compared here
    against a recorded backend snapshot so CI needs no running Hive.

``NoDrift``
    The tool surface has exactly one owner and every document describing it must
    agree. Version strings must agree too (0.1.0 once drifted from its own package).
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "src" / "hive_mcp"
TOOLS_JSON = PKG_DIR / "tools.json"
SNAPSHOT = Path(__file__).resolve().parent / "backend_tools_snapshot.json"
REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = ["recall", "propose", "manage", "get_context", "say", "board"]

# A port nothing listens on: stands in for "Hive not installed".
DEAD = "http://127.0.0.1:59999/mcp"

# Since 0.1.7 the bridge owns a memory database. A test must never touch the
# developer's real one, and must not inherit a HIVE_MODE the runner happens to
# have exported - a test that passes only in the author's shell is not a test.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="hive-test-")
atexit.register(lambda: shutil.rmtree(_TEST_DB_DIR, ignore_errors=True))


def _fresh_db():
    """A path guaranteed empty, so tests cannot see each other's writes."""
    return os.path.join(_TEST_DB_DIR, uuid.uuid4().hex + ".db")


def _clean_env(extra=None):
    """Environment with every proxy variable removed, so tests are closed."""
    env = dict(os.environ)
    for key in list(env):
        if key.lower() in (
            "http_proxy", "https_proxy", "all_proxy",
            "http_proxy_upper", "no_proxy",
        ) or key.lower().endswith("_proxy"):
            env.pop(key, None)
    env.pop("HIVE_AGENT_ID", None)
    env.pop("HIVE_AGENT_TOKEN", None)
    env.pop("HIVE_MODE", None)
    env.setdefault("HIVE_DB", _fresh_db())
    env["PYTHONPATH"] = str(PKG_DIR.parent)
    if extra:
        env.update(extra)
    return env


def run_bridge(messages, env_extra=None, timeout=60, raw_input=None):
    """Feed JSON-RPC lines to the bridge, return (stdout_lines, proc)."""
    env = _clean_env(env_extra)
    data = raw_input if raw_input is not None else (
        "\n".join(json.dumps(m) for m in messages) + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "hive_mcp.stdio"],
        input=data,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    out = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return out, proc


def one(messages, env_extra=None):
    out, proc = run_bridge(messages, env_extra)
    assert out, f"no output; stderr={proc.stderr}"
    return json.loads(out[0])


class OfflineBehaviour(unittest.TestCase):
    """What a crawler sees on a machine with no Hive installed."""

    def test_initialize_succeeds_without_hive(self):
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }],
            {"HIVE_MCP_URL": DEAD},
        )
        self.assertIn("result", resp, f"expected a result, got {resp}")
        result = resp["result"]
        self.assertEqual(result["serverInfo"]["name"], "apiary-memory")
        self.assertIn("tools", result["capabilities"])

    def test_offline_protocol_is_the_conservative_one(self):
        """Never claim a protocol revision the backend does not implement.

        The live backend answers 2024-11-05. Offline we must not answer
        2025-06-18 just because the client asked for it.
        """
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }],
            {"HIVE_MCP_URL": DEAD},
        )
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_tools_list_succeeds_without_hive(self):
        resp = one(
            [{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}],
            {"HIVE_MCP_URL": DEAD},
        )
        self.assertIn("result", resp, f"tools/list must not fail offline: {resp}")
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(names, EXPECTED_TOOLS)

    def test_every_tool_has_a_usable_schema(self):
        resp = one(
            [{"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}],
            {"HIVE_MCP_URL": DEAD},
        )
        for tool in resp["result"]["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool.get("description", "").strip())
                schema = tool["inputSchema"]
                self.assertEqual(schema.get("type"), "object")
                self.assertIn("properties", schema)
                for req in schema.get("required", []):
                    self.assertIn(req, schema["properties"])

    def test_tools_call_really_stores_and_retrieves_without_app(self):
        """0.1.7: a machine without the app is no longer a dead end.

        Until 0.1.6 this server could enumerate six tools and run none of them
        off-app, which is precisely why an MCP directory refused to list it. The
        contract now: propose writes to a real database, the human confirms, and
        recall finds the entry. Verified end to end rather than by inspection.
        """
        db = _fresh_db()
        out, _proc = run_bridge(
            [
                {
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {
                        "name": "propose",
                        "arguments": {
                            "content": "the deploy key rotates on the first Monday",
                            "title": "deploy key",
                        },
                    },
                }
            ],
            {"HIVE_MCP_URL": DEAD, "HIVE_DB": db},
        )
        self.assertTrue(out, "no output from the bridge")
        resp = json.loads(out[0])
        self.assertIn("result", resp, f"propose must work offline, got {resp}")
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload["state"], "pending")
        self.assertEqual(payload["mode"], "local")
        mem_id = payload["id"]

        # Unconfirmed entries must not be searchable: that is the whole point of
        # the gate, and it is what makes "only you confirm" true rather than polite.
        before = one(
            [{
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "deploy key"}},
            }],
            {"HIVE_MCP_URL": DEAD, "HIVE_DB": db},
        )
        found = json.loads(before["result"]["content"][0]["text"])
        self.assertEqual(found["total"], 0, f"pending entry leaked into recall: {found}")

        # The human confirms, out of band, with the CLI only.
        sys.path.insert(0, str(PKG_DIR.parent))
        from hive_mcp.store import Store  # noqa: E402  (test-local import)

        store = Store(db)
        try:
            approved = store.approve(ids=[mem_id])
        finally:
            store.close()
        self.assertEqual(approved, [mem_id])

        after = one(
            [{
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "deploy key"}},
            }],
            {"HIVE_MCP_URL": DEAD, "HIVE_DB": db},
        )
        hit = json.loads(after["result"]["content"][0]["text"])
        self.assertEqual(hit["total"], 1, f"confirmed entry not found: {hit}")
        self.assertEqual(hit["results"][0]["id"], mem_id)

    def test_every_tool_answers_locally_in_one_session(self):
        """All six tools must run against the local store, in one session.

        The capability work above narrows schemas; this pins the other half —
        that narrowing was not achieved by quietly disabling a tool.
        """
        db = _fresh_db()
        out, _proc = run_bridge(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "propose", "arguments": {"content": "rotation lives in the vault note", "title": "rotation"}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "manage", "arguments": {"action": "list_pending"}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "say", "arguments": {"summary": "rotation note queued", "kind": "result"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "board", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "get_context", "arguments": {}}},
            ],
            {"HIVE_MCP_URL": DEAD, "HIVE_MODE": "local", "HIVE_DB": db},
        )
        payloads = []
        for line in out:
            resp = json.loads(line)
            self.assertIn("result", resp, f"a tool refused to run locally: {resp}")
            self.assertFalse(resp["result"].get("isError"), resp)
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(payload.get("mode"), "local", f"mode tag lost: {payload}")
            payloads.append(payload)
        self.assertEqual(len(payloads), 5)

        mem_id = payloads[0]["id"]
        self.assertIn(mem_id, [p["id"] for p in payloads[1]["pending"]] if "pending" in payloads[1] else [mem_id])

        # The human confirms out of band, exactly as documented.
        sys.path.insert(0, str(PKG_DIR.parent))
        from hive_mcp.store import Store  # noqa: E402

        store = Store(db)
        try:
            self.assertEqual(store.approve(ids=[mem_id]), [mem_id])
        finally:
            store.close()

        after = one(
            [{"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "recall", "arguments": {"query": "rotation vault note"}}}],
            {"HIVE_MCP_URL": DEAD, "HIVE_MODE": "local", "HIVE_DB": db},
        )
        hit = json.loads(after["result"]["content"][0]["text"])
        self.assertEqual(hit["mode"], "local")
        self.assertGreaterEqual(hit["total"], 1, f"recall did not find the confirmed entry: {hit}")

    def test_remote_mode_fails_loudly_instead_of_falling_back(self):
        """The honest-failure rule, kept for the mode where it still applies.

        ``HIVE_MODE=remote`` means the caller wants the app and nothing else. A
        silent fallback there would put data in a database the caller did not
        ask for.
        """
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "x"}},
            }],
            {"HIVE_MCP_URL": DEAD, "HIVE_MODE": "remote"},
        )
        self.assertIn("error", resp, f"tools/call must error in remote mode, got {resp}")
        err = resp["error"]
        self.assertEqual(err["data"]["http_status"], 502)
        # The message must name the endpoint AND the fix, not just say "failed".
        self.assertIn("127.0.0.1:59999", err["message"])
        self.assertIn("HIVE_MODE=local", err["message"])

    def test_auto_mode_does_not_fall_back_when_the_app_answers(self):
        """A refusal from the app is final; only silence justifies the fallback.

        This is the one routing rule that must not be relaxed. If the app is up
        and says "no agent credential", retrying against the local store would
        split the user's memory across two databases, one of which nothing
        displays. The test stands up a backend that refuses the way the real one
        does, then asserts the error came back and the local store stayed empty.
        """
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                body = json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "error": {"code": -32001, "message": "missing agent_id + token"},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        db = _fresh_db()
        try:
            resp = one(
                [{
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {
                        "name": "propose",
                        "arguments": {"content": "must not be written locally"},
                    },
                }],
                {
                    "HIVE_MCP_URL": "http://127.0.0.1:%d/mcp" % port,
                    "HIVE_MODE": "auto",
                    "HIVE_DB": db,
                },
            )
        finally:
            server.shutdown()
            server.server_close()  # keep the suite free of ResourceWarnings

        self.assertIn("error", resp, f"a refusal must not be replaced by a local write: {resp}")
        self.assertIn("HIVE_AGENT_ID", resp["error"]["message"])
        self.assertFalse(
            os.path.exists(db),
            "the local database was created even though the app answered: data forking",
        )

    def test_auto_mode_prefers_the_app_when_it_answers(self):
        """The other half of the rule: when the app works, the local store is unused.

        A bridge that quietly served both engines would let one database grow in
        the user's home directory while they believed everything was in the app.
        The fake backend here answers with a result the local store could not
        possibly produce, so seeing it proves who answered.
        """
        import http.server
        import threading

        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                seen.append(raw.decode("utf-8"))
                body = json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({"ok": True, "answered_by": "app"}),
                        }],
                        "isError": False,
                    },
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        db = _fresh_db()
        try:
            resp = one(
                [{
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "recall", "arguments": {"query": "x"}},
                }],
                {
                    "HIVE_MCP_URL": "http://127.0.0.1:%d/mcp" % port,
                    "HIVE_MODE": "auto",
                    "HIVE_DB": db,
                },
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertTrue(seen, "the app was never asked")
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload.get("answered_by"), "app", f"the local store answered instead: {payload}")
        self.assertNotIn("mode", payload, "an app answer must not be relabelled as local")
        self.assertFalse(os.path.exists(db), "the local database was created while the app was answering")

    def test_notification_gets_no_response(self):
        out, _ = run_bridge(
            [{"jsonrpc": "2.0", "method": "notifications/initialized"}],
            {"HIVE_MCP_URL": DEAD},
        )
        self.assertEqual(out, [], f"notifications must never be answered: {out}")

    def test_parse_error_is_reported(self):
        out, _ = run_bridge(None, {"HIVE_MCP_URL": DEAD}, raw_input="not json\n")
        self.assertTrue(out)
        self.assertEqual(json.loads(out[0])["error"]["code"], -32700)

    def test_unknown_method_fails_with_a_fixable_message(self):
        resp = one(
            [{"jsonrpc": "2.0", "id": 5, "method": "resources/list"}],
            {"HIVE_MCP_URL": DEAD, "HIVE_MODE": "remote"},
        )
        self.assertIn("error", resp)
        self.assertIn("apps.apple.com", resp["error"]["message"])


class ProxyEnvironment(unittest.TestCase):
    """Loopback traffic must not depend on whether a proxy is configured."""

    # A proxy that will refuse and yield HTTPError(502) rather than
    # Connection refused — the path that used to lose the actionable hint.
    PROXY = {"http_proxy": "http://127.0.0.1:59998", "HTTP_PROXY": "http://127.0.0.1:59998"}

    # The failure message names the local database path from 0.1.7 onward, so a
    # fixed path per class is required for the comparison below to mean
    # "the proxy changed nothing" rather than "both runs picked a different
    # temp file".
    @classmethod
    def setUpClass(cls):
        cls.DB = _fresh_db()

    def _message(self, env_extra):
        # remote: this class pins the error path, and since 0.1.7 that path is
        # only reached when the caller has asked for the app and nothing else.
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "x"}},
            }],
            {"HIVE_MCP_URL": DEAD, "HIVE_MODE": "remote", "HIVE_DB": self.DB, **env_extra},
        )
        self.assertIn("error", resp)
        return resp["error"]["message"]

    def test_error_message_keeps_the_fix_under_a_proxy(self):
        """2026-10-22 regression: proxied runs lost the endpoint and the link."""
        message = self._message(self.PROXY)
        self.assertIn("127.0.0.1:59999", message, f"endpoint lost under proxy: {message}")
        self.assertIn("apps.apple.com", message, f"fix lost under proxy: {message}")

    def test_error_message_without_a_proxy(self):
        message = self._message({})
        self.assertIn("127.0.0.1:59999", message)
        self.assertIn("apps.apple.com", message)

    def test_loopback_is_not_routed_through_the_proxy(self):
        """The same call must give the same answer either way."""
        self.assertEqual(self._message({}), self._message(self.PROXY))


class AuthHint(unittest.TestCase):
    """The most common real stall: Hive up, no agent registered.

    2026-10-22: the Hive answers auth failures as **HTTP 200 with a JSON-RPC error**,
    not as an HTTP status. The hint was bolted onto the ``HTTPError`` branch only, so
    it never fired on the real path — while the CHANGELOG claimed it did. This test
    stands up a fake backend that behaves exactly like the real one.
    """

    @classmethod
    def setUpClass(cls):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {
                        "code": -32001,
                        "message": "缺少 agent_id + token（MCP Host 需先注册成触角领凭证）",
                    },
                }).encode("utf-8")
                self.send_response(200)  # note: 200, not 401
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.port}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_auth_error_over_http_200_gets_the_credential_hint(self):
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "x"}},
            }],
            {"HIVE_MCP_URL": self.url},
        )
        self.assertIn("error", resp)
        err = resp["error"]
        # The backend's own code and text survive verbatim.
        self.assertEqual(err["code"], -32001)
        self.assertIn("agent_id", err["message"])
        # ...and the actionable pointer is added.
        self.assertIn(
            "HIVE_AGENT_ID", err["message"],
            f"auth failure must point at the env vars: {err['message']}",
        )
        self.assertIn("HIVE_AGENT_TOKEN", err["message"])

    def test_hint_is_not_added_twice(self):
        """A message that already mentions the env vars must not be doubled."""
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "x"}},
            }],
            {"HIVE_MCP_URL": self.url},
        )
        self.assertEqual(resp["error"]["message"].count("HIVE_AGENT_ID"), 1)


class LocalCapabilitySurface(unittest.TestCase):
    """``tools/list`` must never offer an input the answering engine cannot use.

    2026-10-26 regression: the app accepts ``recall.query_vector`` and
    ``propose.vector``; the local store has no embedder and ``local_call()``
    never read either argument. The snapshot advertised both anyway, so a model
    that followed ``tools/list`` sent an embedding, received lexical results,
    and was told nothing — a silent substitution, which this project treats as
    the same class of dishonesty as inventing a tool result.
    """

    HIDDEN = {"recall": "query_vector", "propose": "vector"}

    def _components(self, env_extra, app=None):
        """The full ``tools/list`` answer, optionally against a fake app.

        ``app`` is the list the fake backend returns from ``tools/list``; when
        omitted the backend is simply unreachable.
        """
        url = DEAD
        server = None
        if app is not None:
            import http.server
            import threading

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_POST(self):
                    length = int(self.headers.get("Content-Length") or 0)
                    request = json.loads(self.rfile.read(length) or b"{}")
                    if request.get("method") == "tools/list":
                        body = json.dumps({
                            "jsonrpc": "2.0", "id": request.get("id"),
                            "result": {"tools": app},
                        }).encode()
                    else:
                        body = json.dumps({
                            "jsonrpc": "2.0", "id": request.get("id"),
                            "result": {
                                "content": [{"type": "text", "text": "{}"}],
                                "isError": False,
                            },
                        }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
            url = "http://127.0.0.1:%d/mcp" % server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
        env = dict(env_extra)
        env["HIVE_MCP_URL"] = url
        try:
            resp = one(
                [{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}],
                env,
            )
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
        self.assertIn("result", resp, f"tools/list failed: {resp}")
        return resp["result"]["tools"]

    def _tools(self, env_extra, app=None):
        """tools/list properties per tool, optionally against a fake app."""
        return {
            tool["name"]: set(tool["inputSchema"].get("properties") or {})
            for tool in self._components(env_extra, app)
        }

    def _description(self, tools, tool_name, prop):
        for tool in tools:
            if tool.get("name") == tool_name:
                return tool["inputSchema"]["properties"][prop].get("description")
        return None

    def _assert_hidden(self, props, why):
        for tool, hidden in self.HIDDEN.items():
            with self.subTest(tool=tool, why=why):
                self.assertNotIn(
                    hidden, props[tool],
                    f"{why}: {tool} still advertises {hidden}, which the local "
                    f"engine ignores. properties={sorted(props[tool])}",
                )

    def _assert_visible(self, props, why):
        for tool, visible in self.HIDDEN.items():
            with self.subTest(tool=tool, why=why):
                self.assertIn(
                    visible, props[tool],
                    f"{why}: {tool}.{visible} was removed although the app "
                    f"implements it in this mode. properties={sorted(props[tool])}",
                )

    def test_local_mode_hides_embedding_inputs(self):
        props = self._tools({"HIVE_MODE": "local"})
        self._assert_hidden(props, "HIVE_MODE=local")

    def test_auto_mode_hides_them_when_the_app_is_down(self):
        """auto has to be at least as conservative as local: it may fall back."""
        props = self._tools({"HIVE_MODE": "auto"})
        self._assert_hidden(props, "HIVE_MODE=auto, app unreachable")

    def test_auto_mode_hides_them_even_when_the_app_answers(self):
        """The same session must not see the fields, then lose them.

        If the app's answer were relayed intact, a client would cache a list
        promising embeddings; when the app died, every subsequent call would
        degrade against the local store with nothing said. Narrowing while the
        app is still alive is what makes the two answers identical.
        """
        app_tools = [
            {
                "name": "recall",
                "description": "app recall",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "query_vector": {"type": "array"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "propose",
                "description": "app propose",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "vector": {"type": "array"},
                    },
                    "required": ["content"],
                },
            },
        ]
        props = self._tools({"HIVE_MODE": "auto"}, app=app_tools)
        self._assert_hidden(props, "HIVE_MODE=auto, app answering")
        # The rest of the app's answer survives: narrowing must not erase it.
        self.assertEqual(props["recall"], {"query"})
        self.assertEqual(props["propose"], {"content"})

    def test_remote_mode_keeps_them_when_the_app_answers(self):
        """remote can only ever route to the app, so its list is exactly true."""
        app_tools = [
            {
                "name": "recall",
                "description": "app recall",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "query_vector": {"type": "array"},
                    },
                },
            },
            {
                "name": "propose",
                "description": "app propose",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "vector": {"type": "array"},
                    },
                },
            },
        ]
        props = self._tools({"HIVE_MODE": "remote"}, app=app_tools)
        self._assert_visible(props, "HIVE_MODE=remote, app answering")

    def test_narrowing_removes_only_the_two_fields(self):
        """Guard against over-trimming: everything else must still be offered."""
        with open(TOOLS_JSON, encoding="utf-8") as fh:
            declared = {
                t["name"]: set((t["inputSchema"] or {}).get("properties") or {})
                for t in json.load(fh)["tools"]
            }
        props = self._tools({"HIVE_MODE": "local"})
        for name, params in declared.items():
            with self.subTest(tool=name):
                expected = params - {self.HIDDEN[name]} if name in self.HIDDEN else params
                self.assertEqual(props[name], expected, f"{name} lost or gained parameters")

    def test_the_omit_map_matches_the_local_engine(self):
        """Each omitted input must really be one the local store cannot take.

        Without this, the map becomes a wish list: someone adds a field here
        for a reason of their own, or removes an entry after the store grew the
        feature, and the serves-list quietly lies in the other direction.
        """
        import inspect

        sys.path.insert(0, str(PKG_DIR.parent))
        from hive_mcp import stdio  # noqa: E402  (test-local import)
        from hive_mcp.store import Store  # noqa: E402

        with open(TOOLS_JSON, encoding="utf-8") as fh:
            declared = {
                t["name"]: set((t["inputSchema"] or {}).get("properties") or {})
                for t in json.load(fh)["tools"]
            }
        for tool, hidden in stdio.LOCAL_UNSUPPORTED_ARGUMENTS.items():
            with self.subTest(tool=tool):
                method = getattr(Store, tool)
                accepted = set(inspect.signature(method).parameters)
                for arg in hidden:
                    with self.subTest(arg=arg):
                        self.assertIn(
                            arg, declared[tool],
                            f"{tool}.{arg} is omitted but not declared in tools.json",
                        )
                        self.assertNotIn(
                            arg, accepted,
                            f"{tool}.{arg} is omitted, but Store.{tool} accepts it - "
                            f"the omission is now a lie in the other direction",
                        )

    # -- prose is a claim too ------------------------------------------------ #

    APP_CLAIM = "Matched semantically, not by substring."

    def _app_with_claim(self):
        """The app's surface carrying the claim the local engine cannot keep."""
        return [
            {
                "name": "recall",
                "description": "app recall",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": self.APP_CLAIM},
                        "query_vector": {"type": "array"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "propose",
                "description": "app propose",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "vector": {"type": "array"},
                    },
                    "required": ["content"],
                },
            },
        ]

    def test_local_mode_does_not_claim_semantic_matching(self):
        """The local ranker is words and character bigrams, so say that.

        0.1.8 served "Matched semantically, not by substring" from an engine
        whose entire ranker is ``store.tokenize`` plus a recency nudge. A model
        reading that plans around a capability it was never going to get: it
        writes a paraphrased query, gets a miss, and is told nothing.
        """
        text = self._description(
            self._components({"HIVE_MODE": "local"}), "recall", "query"
        )
        self.assertNotIn("semantic", text.lower())
        self.assertIn("bigram", text.lower())
        self.assertIn("words", text.lower())

    def test_auto_mode_restates_the_apps_claim_when_the_app_answers(self):
        """auto may fall back mid-session, so it cannot keep a richer promise."""
        text = self._description(
            self._components({"HIVE_MODE": "auto"}, app=self._app_with_claim()),
            "recall", "query",
        )
        self.assertNotEqual(text, self.APP_CLAIM)
        self.assertNotIn("semantic", text.lower())

    def test_remote_mode_relays_the_apps_claim_verbatim(self):
        """remote only reaches the app, which does rank by meaning on request."""
        text = self._description(
            self._components({"HIVE_MODE": "remote"}, app=self._app_with_claim()),
            "recall", "query",
        )
        self.assertEqual(text, self.APP_CLAIM)

    def test_the_wording_map_describes_real_declared_properties(self):
        """An override must replace declared text, and must actually differ.

        Otherwise it is dead weight that looks like a fix, or a restatement of a
        property this engine does not serve at all.
        """
        sys.path.insert(0, str(PKG_DIR.parent))
        from hive_mcp import stdio  # noqa: E402  (test-local import)

        with open(TOOLS_JSON, encoding="utf-8") as fh:
            declared = {
                t["name"]: (t.get("inputSchema") or {}).get("properties") or {}
                for t in json.load(fh)["tools"]
            }
        for tool, props in stdio.LOCAL_PROPERTY_TEXT.items():
            for prop, text in props.items():
                with self.subTest(tool=tool, prop=prop):
                    self.assertIn(prop, declared.get(tool, {}),
                                  f"{tool}.{prop} is restated but never declared")
                    self.assertNotIn(
                        prop, stdio.LOCAL_UNSUPPORTED_ARGUMENTS.get(tool, ()),
                        f"{tool}.{prop} cannot be both described and dropped",
                    )
                    self.assertNotEqual(
                        declared[tool][prop].get("description"), text,
                        f"{tool}.{prop} override merely repeats tools.json",
                    )


class AdvertisedDefaults(unittest.TestCase):
    """An omitted argument must behave the way the schema says it will.

    2026-10-26 regression, second case of the same defect: ``tools.json``
    declared ``recall.limit`` 10, ``recall.compact`` true and ``say.ttl``
    "24 hours" while ``local_call()`` answered 8 results, full bodies, and a
    board card that never expired. A client that trusts a default is doing
    exactly what the schema invited, so this drift is silent by construction -
    the only defence is to compare the answer against the declared number rather
    than against a second hand-written expectation.
    """

    MEMORIES = 14   # more than recall's and get_context's default, so the cap shows
    BOARD = 25      # more than board's default
    # Longer than the 160 characters a compact recall returns, so trimming is
    # observable rather than inferred.
    BODY = "关于蜂巢检索默认值的测试条目，用来验证 compact 与 limit 的默认值。" * 6

    def setUp(self):
        with open(TOOLS_JSON, encoding="utf-8") as fh:
            self.declared = {
                t["name"]: (t.get("inputSchema") or {}).get("properties") or {}
                for t in json.load(fh)["tools"]
            }
        self.db = _fresh_db()
        sys.path.insert(0, str(PKG_DIR.parent))
        from hive_mcp.store import Store  # noqa: E402  (test-local import)

        store = Store(self.db)
        try:
            ids = [
                store.propose(
                    self.BODY + " 编号%02d" % i,
                    title="probe-%02d" % i,
                )["id"]
                for i in range(self.MEMORIES)
            ]
            store.approve(ids=ids)
            for i in range(self.BOARD):
                store.say("probe board message %02d" % i, ttl=86400)
        finally:
            store.close()

    def _call(self, name, arguments):
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }],
            {"HIVE_MCP_URL": DEAD, "HIVE_MODE": "local", "HIVE_DB": self.db},
        )
        self.assertIn("result", resp, f"{name} failed locally: {resp}")
        return json.loads(resp["result"]["content"][0]["text"])

    def _stored(self, mem_id):
        from hive_mcp.store import Store  # noqa: E402  (test-local import)

        store = Store(self.db)
        try:
            return store.conn.execute(
                "SELECT content FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()["content"]
        finally:
            store.close()

    def test_recall_limit_default(self):
        advertised = self.declared["recall"]["limit"]["default"]
        self.assertLess(advertised, self.MEMORIES,
                        "the seed must exceed the default or this proves nothing")
        got = self._call("recall", {"query": "蜂巢 检索 默认值"})
        self.assertEqual(len(got["results"]), advertised,
                         f"advertised {advertised}, answered {len(got['results'])}")
        self.assertEqual(got["total"], advertised)

    def test_recall_compact_default(self):
        """compact:true promises trimmed bodies, not full ones."""
        advertised = self.declared["recall"]["compact"]["default"]
        got = self._call("recall", {"query": "蜂巢 检索 默认值"})
        self.assertTrue(got["results"], got)
        content = got["results"][0]["content"]
        full = self._stored(got["results"][0]["id"])
        self.assertGreater(len(full), 160, "the seed is too short to show trimming")
        if advertised:
            self.assertEqual(len(content), 160,
                             "compact defaults to true, so the body must be trimmed")
        else:
            self.assertEqual(content, full)

    def test_get_context_limit_default(self):
        advertised = self.declared["get_context"]["limit"]["default"]
        self.assertLess(advertised, self.MEMORIES)
        got = self._call("get_context", {})
        self.assertEqual(len(got["digest"]), advertised)

    def test_board_limit_default(self):
        advertised = self.declared["board"]["limit"]["default"]
        self.assertLess(advertised, self.BOARD)
        got = self._call("board", {})
        self.assertEqual(len(got["items"]), advertised)
        self.assertEqual(got["count"], advertised)

    def test_say_default_ttl(self):
        """The card must expire when the description says it will."""
        self.assertIn("24 hours", self.declared["say"]["ttl"]["description"])
        got = self._call("say", {"summary": "probe default ttl"})
        self.assertEqual(got["ttl"], 24 * 3600, got)

        from hive_mcp.store import Store  # noqa: E402  (test-local import)

        store = Store(self.db)
        try:
            row = store.conn.execute(
                "SELECT created, expires FROM board WHERE id = ?", (got["id"],)
            ).fetchone()
        finally:
            store.close()
        self.assertIsNotNone(row["expires"], "the card was stored immortal")
        self.assertEqual(round(row["expires"] - row["created"]), 24 * 3600)

    def test_say_ttl_is_clamped_not_immortal(self):
        """Neither engine offers "forever": the app clamps to 60 s .. 30 days."""
        zero = self._call("say", {"summary": "probe ttl zero", "ttl": 0})
        huge = self._call("say", {"summary": "probe ttl huge", "ttl": 10 ** 9})
        self.assertEqual(zero["ttl"], 60, zero)
        self.assertEqual(huge["ttl"], 30 * 24 * 3600, huge)


class BoardCursor(unittest.TestCase):
    """The answer side of the same defect: two engines, two shapes, one cursor.

    2026-10-26 regression. ``tools.json`` declares ``board.since`` as a string and
    the app returns the id of its newest card, but the local engine answered
    ``messages``/``total`` and a float epoch timestamp, and parsed ``since`` with
    ``float()``. Under ``HIVE_MODE=auto`` a caller polls while the app answers,
    keeps the card id it was handed, and loses the app: ``float('CB2026...')``
    raised, the exception was swallowed into ``since = 0``, and the entire board
    came back as if nothing had been read — with no field saying the cursor had
    been discarded. A float sent the other way matched no card id in the app,
    which answered with its newest ``limit`` just as quietly.

    Both directions now speak one cursor type, and the case underneath — a cursor
    whose card is gone (expired, trimmed, or from the other engine, whose ids are
    ``CB…`` against this store's ``msg_…``) — is reported with ``since_unknown``
    and a ``note`` instead of being indistinguishable from a first read.
    """

    APP_SHAPE = {
        "ok", "cursor", "unread", "count", "items", "reader", "mode",
    }  # ``mode`` is this engine's tag; the rest is ``_tool_board``'s dict.

    def setUp(self):
        with open(TOOLS_JSON, encoding="utf-8") as fh:
            tools = {t["name"]: t for t in json.load(fh)["tools"]}
        self.board_tool = tools["board"]

    def _post(self, db, summaries):
        """Put cards on the board through the store, the way ``say`` does."""
        sys.path.insert(0, str(PKG_DIR.parent))
        from hive_mcp.store import Store  # noqa: E402  (test-local import)

        store = Store(db)
        try:
            return [store.say(s, ttl=86400)["id"] for s in summaries]
        finally:
            store.close()

    def _board(self, db, arguments, env=None):
        extra = {"HIVE_MCP_URL": DEAD, "HIVE_MODE": "local", "HIVE_DB": db}
        extra.update(env or {})
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "board", "arguments": arguments},
            }],
            extra,
        )
        self.assertIn("result", resp, f"board failed locally: {resp}")
        self.assertFalse(resp["result"].get("isError"), resp)
        return json.loads(resp["result"]["content"][0]["text"])

    def test_the_answer_is_the_shape_the_app_answers_with(self):
        """A caller must not have to branch on which engine replied."""
        db = _fresh_db()
        self._post(db, ["one", "two", "three"])
        got = self._board(db, {})
        self.assertEqual(set(got), self.APP_SHAPE, got)
        self.assertEqual(got["count"], len(got["items"]))
        self.assertEqual(got["unread"], 3)
        # The private names this engine used for two of those fields are gone,
        # not merely joined by the new ones: two names for one fact is how the
        # two engines drifted apart in the first place.
        self.assertNotIn("messages", got)
        self.assertNotIn("total", got)

    def test_the_cursor_is_the_id_of_a_card(self):
        db = _fresh_db()
        ids = self._post(db, ["one", "two", "three"])
        got = self._board(db, {})
        self.assertIsInstance(got["cursor"], str)
        self.assertEqual(got["cursor"], ids[-1])
        self.assertEqual(got["items"][-1]["id"], ids[-1])

    def test_the_cursor_has_the_type_tools_json_declares(self):
        """The engine answers the declared type, not a second one beside it.

        ``since`` is declared ``string``, so the value this engine emits has to
        survive being handed straight back as ``since``: a float cursor was a
        promise the schema never made.
        """
        declared = self.board_tool["inputSchema"]["properties"]["since"]
        self.assertEqual(declared["type"], "string")

        db = _fresh_db()
        self._post(db, ["one", "two"])
        first = self._board(db, {})
        again = self._board(db, {"since": first["cursor"]})
        self.assertNotIn("since_unknown", again)
        self.assertEqual(again["count"], 0)
        self.assertEqual(again["cursor"], first["cursor"])

    def test_polling_returns_only_what_arrived_since_the_cursor(self):
        db = _fresh_db()
        self._post(db, ["one", "two"])
        first = self._board(db, {})
        new_id = self._post(db, ["three"])[0]

        second = self._board(db, {"since": first["cursor"]})
        self.assertEqual(second["count"], 1)
        self.assertEqual([item["id"] for item in second["items"]], [new_id])
        self.assertEqual(second["unread"], 1)
        self.assertEqual(second["cursor"], new_id)

        # An idle poll must be a quiet zero, not an empty cursor that would turn
        # the next call into a full re-read.
        third = self._board(db, {"since": second["cursor"]})
        self.assertEqual((third["unread"], third["count"], third["items"]), (0, 0, []))
        self.assertEqual(third["cursor"], new_id)
        self.assertNotIn("since_unknown", third)

    def test_a_cursor_from_the_other_engine_is_reported_not_swallowed(self):
        """The case that used to become a silent re-read of the whole board."""
        db = _fresh_db()
        ids = self._post(db, ["one", "two"])
        foreign = "CB20261026180000000001"   # shaped like an app card id
        got = self._board(db, {"since": foreign})
        self.assertTrue(got.get("since_unknown"), f"the restart was not stated: {got}")
        self.assertIn(foreign, got["note"])
        self.assertIn("CB", got["note"])     # names the id shape, not just "invalid"
        self.assertIn("msg_", got["note"])
        self.assertEqual(got["unread"], 2)
        self.assertEqual(got["count"], 2)
        self.assertEqual(got["cursor"], ids[-1])

    def test_a_legacy_epoch_cursor_is_reported_not_read_as_zero(self):
        """A float cursor is a caller mistake this engine names, not one it obeys.

        Reading it as an epoch would be the second half of the old bug: the
        previous engine emitted floats, so a stale one must not be mistaken for
        the new type silently in either direction.
        """
        db = _fresh_db()
        self._post(db, ["one", "two"])
        got = self._board(db, {"since": 1729937000.5})
        self.assertTrue(got.get("since_unknown"), got)
        self.assertIn("epoch", got["note"])
        self.assertEqual(got["unread"], 2)

    def test_what_the_limit_hides_is_counted_and_said(self):
        """``unread`` counts past the page, and the note declines to be quiet."""
        db = _fresh_db()
        advertised = self.board_tool["inputSchema"]["properties"]["limit"]["default"]
        count = advertised + 5
        ids = self._post(db, ["m%02d" % i for i in range(count)])
        got = self._board(db, {})
        self.assertEqual(got["count"], advertised)
        self.assertEqual(got["unread"], count)
        self.assertEqual(got["items"][-1]["id"], ids[-1])   # newest page, as upstream
        self.assertIn("note", got)
        self.assertIn(str(count), got["note"])
        self.assertIn("unread", got["note"])

    def test_an_empty_board_has_an_empty_cursor(self):
        got = self._board(_fresh_db(), {})
        self.assertEqual(got["cursor"], "")
        self.assertEqual((got["unread"], got["count"], got["items"]), (0, 0, []))
        self.assertNotIn("since_unknown", got)

    def test_reader_is_the_agent_that_asked(self):
        """``reader`` comes from the credential, the way ``say``'s actor does."""
        db = _fresh_db()
        self.assertEqual(self._board(db, {})["reader"], "")
        self.assertEqual(
            self._board(db, {}, {"HIVE_AGENT_ID": "agent_probe"})["reader"],
            "agent_probe",
        )

    def test_roster_names_the_agent_the_way_the_app_does(self):
        db = _fresh_db()
        self._post(db, ["one", "two"])
        got = self._board(db, {"roster": True})
        roster = got.get("roster")
        self.assertIsInstance(roster, list)
        self.assertEqual(len(roster), 1, roster)
        entry = roster[0]
        self.assertEqual(set(entry), {"agent_id", "messages", "last_seen"}, entry)
        self.assertEqual(entry["messages"], 2)


class SchemaFidelity(unittest.TestCase):
    """The offline snapshot must mirror the backend, not paraphrase it."""

    def setUp(self):
        with open(TOOLS_JSON, encoding="utf-8") as fh:
            self.mine = {t["name"]: t for t in json.load(fh)["tools"]}
        with open(SNAPSHOT, encoding="utf-8") as fh:
            self.real = {t["name"]: t for t in json.load(fh)["tools"]}

    def test_same_tool_names(self):
        self.assertEqual(sorted(self.mine), sorted(self.real))

    def test_no_parameter_the_backend_does_not_have(self):
        """Advertising a parameter that does nothing is still a false claim."""
        for name, real in self.real.items():
            mine_props = set((self.mine[name]["inputSchema"] or {}).get("properties", {}))
            real_props = set((real["inputSchema"] or {}).get("properties", {}))
            with self.subTest(tool=name):
                self.assertEqual(
                    mine_props - real_props, set(),
                    f"{name} declares parameters the backend lacks: {mine_props - real_props}",
                )

    def test_no_missing_parameters(self):
        for name, real in self.real.items():
            mine_props = set((self.mine[name]["inputSchema"] or {}).get("properties", {}))
            real_props = set((real["inputSchema"] or {}).get("properties", {}))
            with self.subTest(tool=name):
                self.assertEqual(
                    real_props - mine_props, set(),
                    f"{name} is missing backend parameters: {real_props - mine_props}",
                )

    def test_enums_match_exactly(self):
        """say.kind / say.stage were documented as free text; they are enums."""
        for name, real in self.real.items():
            mine_props = (self.mine[name]["inputSchema"] or {}).get("properties", {})
            real_props = (real["inputSchema"] or {}).get("properties", {})
            for param, spec in real_props.items():
                if param not in mine_props:
                    continue
                with self.subTest(tool=name, param=param):
                    self.assertEqual(
                        mine_props[param].get("enum"), spec.get("enum"),
                        f"{name}.{param} enum must match the backend exactly",
                    )

    def test_required_matches(self):
        for name, real in self.real.items():
            with self.subTest(tool=name):
                self.assertEqual(
                    sorted(self.mine[name]["inputSchema"].get("required", [])),
                    sorted(real["inputSchema"].get("required", [])),
                )

    def test_types_match(self):
        for name, real in self.real.items():
            mine_props = (self.mine[name]["inputSchema"] or {}).get("properties", {})
            real_props = (real["inputSchema"] or {}).get("properties", {})
            for param, spec in real_props.items():
                if param not in mine_props:
                    continue
                with self.subTest(tool=name, param=param):
                    self.assertEqual(
                        mine_props[param].get("type"), spec.get("type"),
                        f"{name}.{param} type must match",
                    )

    def test_snapshot_records_where_it_came_from(self):
        """A snapshot without provenance is a rumour."""
        with open(SNAPSHOT, encoding="utf-8") as fh:
            snap = json.load(fh)
        self.assertIn("source", snap)
        self.assertIn("hive_version", snap["source"])
        self.assertIn("endpoint", snap["source"])


class NoDrift(unittest.TestCase):
    """Every document describing the tool surface must agree with it."""

    def setUp(self):
        with open(TOOLS_JSON, encoding="utf-8") as fh:
            self.data = json.load(fh)
        self.names = [t["name"] for t in self.data["tools"]]

    def test_tools_json_is_well_formed(self):
        self.assertEqual(self.names, EXPECTED_TOOLS)
        for tool in self.data["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertRegex(tool["name"], r"^[a-z][a-z0-9_]*$")
                self.assertTrue(tool["description"].strip())

    def test_readme_documents_every_tool(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for name in self.names:
            with self.subTest(tool=name):
                self.assertIn(f"`{name}`", readme, f"README does not mention {name}")

    def test_readme_states_tool_count_correctly(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        words = {1: "one", 2: "two", 3: "three", 4: "four",
                 5: "five", 6: "six", 7: "seven", 8: "eight"}
        count = len(self.names)
        if count in words:
            self.assertIn(f"The {words[count]} tools", readme)

    def test_readme_states_when_the_app_is_needed(self):
        """Honesty rule 3, re-scoped in 0.1.7.

        The prerequisite used to be "a running app". The local engine now works
        alone, so the honest statement is the boundary: what you get without the
        app, and what the app adds. Both must be written down, because a reader
        deciding whether to install something needs the line, not the implication.
        """
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Prerequisite", readme)
        self.assertIn("apps.apple.com", readme)
        self.assertIn("without the app", readme.lower())

    def test_readme_states_a_hive_version_floor(self):
        """say/board only exist in Hive 1.4.5+; older installs must be warned."""
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(
            readme, r"1\.4\.\d",
            "README must state which Hive version this package expects",
        )

    def test_readme_states_the_response_shapes(self):
        """The answer is part of the surface, so it is part of this test.

        ``board``'s shape is the app's, which means the README has to name the
        fields a caller will read and the flag that says a cursor could not be
        placed. The two shapes that are deliberately left unequal are named too:
        a reader switching engines should meet that difference here rather than
        in the middle of a session.
        """
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Response shapes", readme)
        for token in ("`items`", "`unread`", "`count`", "`cursor`",
                      "`since_unknown`", "`wh`", "`digest`"):
            with self.subTest(token=token):
                self.assertIn(token, readme,
                              f"README does not document {token} in the answer")

    def test_server_json_matches_the_package(self):
        path = REPO_ROOT / "server.json"
        if not path.exists():
            self.skipTest("server.json not present in this tree")
        server = json.loads(path.read_text(encoding="utf-8"))
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        pkg = re.search(r'^name\s*=\s*"([^"]+)"', pyproject, re.M)
        self.assertIsNotNone(pkg)
        self.assertIn(pkg.group(1), json.dumps(server))
        # The registry manifest declares the same tool-carrying package: its
        # version must not drift from pyproject either.
        pkg_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
        self.assertEqual(server.get("version"), pkg_version)

    def test_version_agrees_across_files(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
        for rel, pattern in (
            ("src/hive_mcp/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
            ("src/hive_mcp/stdio.py", r'_FALLBACK_VERSION\s*=\s*"([^"]+)"'),
        ):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            found = re.findall(pattern, text)
            self.assertTrue(found, f"{rel} declares no version literal for {pattern}")
            for item in found:
                with self.subTest(file=rel):
                    self.assertEqual(item, version)

        # Check what the code reports at runtime too, not only the literals:
        # a source checkout beside an older installed copy once answered with
        # the installed number, and no string comparison would have caught it.
        proc = subprocess.run(
            [sys.executable, "-c", "import hive_mcp.stdio as s; print(s.__version__)"],
            capture_output=True, text=True, env=_clean_env(), timeout=60,
        )
        self.assertEqual(proc.stdout.strip(), version, proc.stderr)

    def test_changelog_lists_the_current_version(self):
        """A changelog missing its own latest entry is the loudest possible tell."""
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {version}", changelog,
                      f"CHANGELOG has no section for {version}")

    def test_changelog_history_table_has_no_unpublished_rows(self):
        """Check the table rows, not the prose.

        A blanket search for the word would also match the sentence explaining
        that the word was removed. What matters is that no released version is
        still described as not installable.
        """
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for line in changelog.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            if not re.match(r"\|\s*0\.\d", stripped):
                continue  # header / separator row
            with self.subTest(row=stripped[:40]):
                self.assertNotIn("pending", stripped.lower(),
                                 f"version table still marks a row unpublished: {stripped}")

    def test_glama_json_is_minimal_and_valid(self):
        path = REPO_ROOT / "glama.json"
        if not path.exists():
            self.skipTest("glama.json not present in this tree")
        glama = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("maintainers", glama)
        self.assertIsInstance(glama["maintainers"], list)
        self.assertTrue(glama["maintainers"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
