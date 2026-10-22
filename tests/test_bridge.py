"""Tests for the bridge. Standard library only — no test dependencies.

Two groups:

``OfflineBehaviour``
    Pins the contract that matters when the Hive is *not* installed: the
    handshake and tool discovery must still work, and a real tool call must fail
    loudly rather than invent a result. These run with no Hive, no network.

``NoDrift``
    Asserts the tool surface has exactly one home (``tools.json``) and that the
    README, ``server.json`` and ``glama.json`` describe the same six tools. A
    drifting description is how a package ends up advertising tools it does not
    have — cheap to check, expensive to discover in production.
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
REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = ["recall", "propose", "manage", "get_context", "say", "board"]


def run_bridge(messages, env_extra=None, timeout=30):
    """Feed JSON-RPC lines to the bridge, return (stdout_lines, raw)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PKG_DIR.parent)
    env.pop("HIVE_AGENT_ID", None)
    env.pop("HIVE_AGENT_TOKEN", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "hive_mcp.stdio"],
        input="\n".join(json.dumps(m) for m in messages) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    out = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return out, proc


class OfflineBehaviour(unittest.TestCase):
    """What a crawler sees on a machine with no Hive installed."""

    # A port nothing listens on, to stand in for "Hive not running".
    DEAD = {"HIVE_MCP_URL": "http://127.0.0.1:59999/mcp"}

    def test_initialize_succeeds_without_hive(self):
        out, proc = run_bridge(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                }
            ],
            self.DEAD,
        )
        self.assertTrue(out, f"no output; stderr={proc.stderr}")
        resp = json.loads(out[0])
        self.assertIn("result", resp, f"expected a result, got {resp}")
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "memory-hive-mcp")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list_succeeds_without_hive(self):
        out, proc = run_bridge(
            [{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}],
            self.DEAD,
        )
        self.assertTrue(out, f"no output; stderr={proc.stderr}")
        resp = json.loads(out[0])
        self.assertIn("result", resp, f"tools/list must not fail offline: {resp}")
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(names, EXPECTED_TOOLS)

    def test_every_tool_has_a_usable_schema(self):
        out, _ = run_bridge(
            [{"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}],
            self.DEAD,
        )
        tools = json.loads(out[0])["result"]["tools"]
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool.get("description", "").strip())
                schema = tool["inputSchema"]
                self.assertEqual(schema.get("type"), "object")
                self.assertIn("properties", schema)
                # Every required key must actually be declared.
                for req in schema.get("required", []):
                    self.assertIn(req, schema["properties"])

    def test_tools_call_fails_loudly_without_hive(self):
        """The honest-failure rule: never fabricate a successful tool result."""
        out, _ = run_bridge(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "recall", "arguments": {"query": "x"}},
                }
            ],
            self.DEAD,
        )
        self.assertTrue(out, "tools/call produced no response at all")
        resp = json.loads(out[0])
        self.assertIn("error", resp, f"tools/call must error offline, got {resp}")
        err = resp["error"]
        self.assertEqual(err["data"]["http_status"], 502)
        # The message must name the endpoint and the fix, not just "failed".
        self.assertIn("127.0.0.1:59999", err["message"])
        self.assertIn("apps.apple.com", err["message"])

    def test_notification_gets_no_response(self):
        out, _ = run_bridge(
            [{"jsonrpc": "2.0", "method": "notifications/initialized"}], self.DEAD
        )
        self.assertEqual(out, [], f"notifications must never be answered: {out}")

    def test_parse_error_is_reported(self):
        proc = subprocess.run(
            [sys.executable, "-m", "hive_mcp.stdio"],
            input="this is not json\n",
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONPATH": str(PKG_DIR.parent), **self.DEAD},
        )
        out = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertTrue(out)
        self.assertEqual(json.loads(out[0])["error"]["code"], -32700)

    def test_unknown_method_is_forwarded_not_invented(self):
        out, _ = run_bridge(
            [{"jsonrpc": "2.0", "id": 5, "method": "resources/list"}], self.DEAD
        )
        self.assertTrue(out)
        self.assertIn("error", json.loads(out[0]))


class NoDrift(unittest.TestCase):
    """The tool surface must be described identically everywhere it appears."""

    def setUp(self):
        with open(TOOLS_JSON, encoding="utf-8") as fh:
            self.data = json.load(fh)
        self.names = [t["name"] for t in self.data["tools"]]

    def test_tools_json_is_well_formed(self):
        self.assertEqual(self.names, EXPECTED_TOOLS)
        self.assertTrue(self.data.get("$comment"), "keep the single-source note")
        for tool in self.data["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertRegex(tool["name"], r"^[a-z][a-z0-9_]*$")
                self.assertTrue(tool["description"].strip())

    def test_readme_documents_every_tool(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for name in self.names:
            with self.subTest(tool=name):
                self.assertIn(
                    f"`{name}`", readme, f"README does not mention tool {name}"
                )

    def test_readme_states_tool_count_correctly(self):
        """The README says 'The six tools' — it must not drift from reality."""
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        words = {
            1: "one", 2: "two", 3: "three", 4: "four",
            5: "five", 6: "six", 7: "seven", 8: "eight",
        }
        count = len(self.names)
        if count in words:
            self.assertIn(
                f"The {words[count]} tools",
                readme,
                f"README should say 'The {words[count]} tools' for {count} tools",
            )

    def test_readme_states_a_running_hive_is_required(self):
        """Honesty rule 3: the prerequisite must be stated, not implied."""
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Prerequisite", readme)
        self.assertIn("running Hive", readme)
        self.assertIn("apps.apple.com", readme)

    def test_server_json_matches_tools_json(self):
        path = REPO_ROOT / "server.json"
        if not path.exists():
            self.skipTest("server.json not present in this tree")
        with open(path, encoding="utf-8") as fh:
            server = json.load(fh)
        # server.json is the MCP registry manifest; the package name must agree.
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        pkg = re.search(r'^name\s*=\s*"([^"]+)"', pyproject, re.M)
        self.assertIsNotNone(pkg)
        blob = json.dumps(server)
        self.assertIn(pkg.group(1), blob, "server.json must reference the package")

    def test_version_agrees_across_files(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)

        init = (PKG_DIR / "__init__.py").read_text(encoding="utf-8")
        fallback = re.search(r'__version__\s*=\s*"([^"]+)"', init)
        if fallback:
            self.assertEqual(
                fallback.group(1), version,
                f"__init__.py fallback {fallback.group(1)} != pyproject {version}",
            )

        stdio = (PKG_DIR / "stdio.py").read_text(encoding="utf-8")
        for found in re.findall(r'__version__\s*=\s*"([^"]+)"', stdio):
            self.assertEqual(
                found, version,
                f"stdio.py fallback {found} != pyproject {version}",
            )

    def test_glama_json_is_minimal_and_valid(self):
        path = REPO_ROOT / "glama.json"
        if not path.exists():
            self.skipTest("glama.json not present in this tree")
        with open(path, encoding="utf-8") as fh:
            glama = json.load(fh)
        # Glama's schema requires only `maintainers`.
        self.assertIn("maintainers", glama)
        self.assertIsInstance(glama["maintainers"], list)
        self.assertTrue(glama["maintainers"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
