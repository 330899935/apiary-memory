"""Tests for the bridge. Standard library only — no test dependencies.

Four groups, each pinning a contract that has already been broken once:

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
        self.assertEqual(result["serverInfo"]["name"], "memory-hive-mcp")
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
