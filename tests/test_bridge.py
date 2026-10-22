"""Tests for the bridge. Standard library only — no test dependencies.

Four groups, each pinning a contract that has already been broken once:

``OfflineBehaviour``
    What a crawler sees on a machine with no Hive. The handshake and tool
    discovery must work; a real tool call must fail loudly, never invent a result.

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

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "src" / "hive_mcp"
TOOLS_JSON = PKG_DIR / "tools.json"
SNAPSHOT = Path(__file__).resolve().parent / "backend_tools_snapshot.json"
REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = ["recall", "propose", "manage", "get_context", "say", "board"]

# A port nothing listens on: stands in for "Hive not installed".
DEAD = "http://127.0.0.1:59999/mcp"


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

    def test_tools_call_fails_loudly_without_hive(self):
        """The honest-failure rule: never fabricate a successful tool result."""
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "x"}},
            }],
            {"HIVE_MCP_URL": DEAD},
        )
        self.assertIn("error", resp, f"tools/call must error offline, got {resp}")
        err = resp["error"]
        self.assertEqual(err["data"]["http_status"], 502)
        # The message must name the endpoint AND the fix, not just say "failed".
        self.assertIn("127.0.0.1:59999", err["message"])
        self.assertIn("apps.apple.com", err["message"])

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
            {"HIVE_MCP_URL": DEAD},
        )
        self.assertIn("error", resp)
        self.assertIn("apps.apple.com", resp["error"]["message"])


class ProxyEnvironment(unittest.TestCase):
    """Loopback traffic must not depend on whether a proxy is configured."""

    # A proxy that will refuse and yield HTTPError(502) rather than
    # Connection refused — the path that used to lose the actionable hint.
    PROXY = {"http_proxy": "http://127.0.0.1:59998", "HTTP_PROXY": "http://127.0.0.1:59998"}

    def _message(self, env_extra):
        resp = one(
            [{
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "x"}},
            }],
            {"HIVE_MCP_URL": DEAD, **env_extra},
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

    def test_readme_states_a_running_hive_is_required(self):
        """Honesty rule 3: the prerequisite must be stated, not implied."""
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Prerequisite", readme)
        self.assertIn("running Hive", readme)
        self.assertIn("apps.apple.com", readme)

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
        for rel in ("src/hive_mcp/stdio.py", "src/hive_mcp/__init__.py"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for found in re.findall(r'__version__\s*=\s*"([^"]+)"', text):
                with self.subTest(file=rel):
                    self.assertEqual(found, version)

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
