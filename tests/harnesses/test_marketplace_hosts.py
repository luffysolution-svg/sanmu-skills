"""Opt-in real CLI installs in temporary user directories; no models or user auth."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import queue
import threading

ROOT = Path(__file__).resolve().parents[2]


def codex_command():
    configured = os.environ.get("SANMU_CODEX_CLI")
    if configured:
        return [configured]
    node = shutil.which("node")
    if node:
        script = Path(node).parent / "node_modules/@openai/codex/bin/codex.js"
        if script.is_file():
            return [node, str(script)]
    binary = shutil.which("codex")
    return [binary] if binary and not binary.lower().endswith(".cmd") else None


@unittest.skipUnless(os.environ.get("SANMU_TEST_HOSTS") == "1", "Set SANMU_TEST_HOSTS=1 for isolated installed-host tests")
class MarketplaceHosts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="sanmu-host-install-", ignore_cleanup_errors=True)
        cls.sandbox = Path(cls.temporary.name)
        cls.env = dict(os.environ, CODEX_HOME=str(cls.sandbox / "codex"),
                       CLAUDE_CONFIG_DIR=str(cls.sandbox / "claude"), SANMU_DATA_DIR=str(cls.sandbox / "data"),
                       SANMU_PANEL_WORKER="1", SANMU_NO_AUTO_SETUP="1", USER_CONFIG_EXA_API_KEY="SANMU_HOST_FIXTURE_KEY")
        for name in ("codex", "claude"):
            (cls.sandbox / name).mkdir()
        spec = importlib.util.spec_from_file_location("sanmu_distribution", ROOT / "distribution/build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        _, files = build.validate(ROOT)
        cls.source = cls.sandbox / "source"
        for relative in files:
            target = cls.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_cli(self, argv):
        result = subprocess.run(argv, env=self.env, cwd=self.sandbox, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def assert_installed(self, home):
        manifests = [path for path in home.rglob("plugin.json") if "skills" not in path.parts]
        roots = []
        for path in manifests:
            if path.parent.name not in {".claude-plugin", ".codex-plugin"}:
                continue
            root = path.parent.parent
            if all((root / "skills" / name / "SKILL.md").is_file()
                   for name in ("sanmu-doc2md", "sanmu-paper-reader", "sanmu-paper-search")):
                roots.append(root)
        self.assertTrue(roots, "Host did not install three complete skills")
        for root in roots:
            self.assertTrue((root / "scripts/sanmu.mjs").is_file())
            self.assertTrue((root / "mcp.json").is_file())
            self.assertTrue((root / "distribution/zcode/sanmu-paper-search/scripts/mcp-server.mjs").is_file())
        return roots

    def test_codex_marketplace_installs_complete_plugin(self):
        cli = codex_command()
        if not cli:
            self.skipTest("Codex CLI not installed")
        self.run_cli(cli + ["plugin", "marketplace", "add", str(self.source), "--json"])
        installed = self.run_cli(cli + ["plugin", "add", "sanmu-skills@sanmu-skills", "--json"])
        self.assertIn("sanmu-skills", installed)
        roots = self.assert_installed(self.sandbox / "codex")
        self.assertTrue(any(json.loads((root / "mcp.json").read_text(encoding="utf-8"))["mcpServers"].get("sanmu") for root in roots))
        process = subprocess.Popen(cli + ["app-server", "--stdio"], env=self.env, cwd=self.sandbox,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1)
        replies = queue.Queue()
        errors = []
        def read_stdout():
            for line in process.stdout:
                try:
                    replies.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        def read_stderr():
            for line in process.stderr:
                errors.append(line)
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        counter = 0
        def request(method, params):
            nonlocal counter
            counter += 1
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            for _ in range(200):
                reply = replies.get(timeout=45)
                if reply.get("id") == counter:
                    self.assertNotIn("error", reply, json.dumps(reply))
                    return reply.get("result")
            self.fail("No app-server response: " + "".join(errors)[-1000:])
        try:
            request("initialize", {"clientInfo": {"name": "sanmu-test", "version": "1"}, "capabilities": {"experimentalApi": True}})
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n")
            process.stdin.flush()
            skills = request("skills/list", {"cwds": [str(self.sandbox)], "forceReload": True})
            self.assertIn("sanmu-paper-reader", json.dumps(skills))
            thread = request("thread/start", {"cwd": str(self.sandbox), "approvalPolicy": "never", "sandbox": "read-only", "experimentalRawEvents": False})
            thread_id = thread["thread"]["id"]
            status = request("mcpServerStatus/list", {"threadId": thread_id, "detail": "toolsAndAuthOnly"})
            servers = status.get("data", [])
            sanmu = next((server for server in servers if "sanmu" in server["name"]), None)
            self.assertIsNotNone(sanmu, json.dumps(status))
            self.assertFalse(sanmu.get("toolsError"), json.dumps(sanmu))
            self.assertEqual(len(sanmu["tools"]), 24)
            # The low-level app-server call forwards caller metadata; normal agent turns attach this sandbox context automatically.
            called = request("mcpServer/tool/call", {"threadId": thread_id, "server": sanmu["name"], "tool": "open_panel", "arguments": {},
                "_meta": {"codex/sandbox-state": {"sandboxCwd": self.sandbox.as_uri()}}})
            self.assertNotIn('"isError": true', json.dumps(called))
            self.assertIn("workspaceRoot", json.dumps(called))
            result = called.get("result", called)
            self.assertEqual(result["structuredContent"]["settings"]["workspaceRoot"], str(self.sandbox))
            self.assertNotIn("panelUrl", result["structuredContent"])
            self.assertEqual(result["structuredContent"]["configStatus"]["exa_api_key"]["source"], "userConfig")
            self.assertNotIn("SANMU_HOST_FIXTURE_KEY", json.dumps(called))
            print("Codex app-server: three skills + 24 MCP tools + real open_panel call verified")
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
        print("Codex isolated marketplace install: three skills + CLI + MCP files verified")

    def test_claude_marketplace_installs_complete_plugin(self):
        cli = shutil.which("claude")
        if not cli:
            self.skipTest("Claude CLI not installed")
        installed = self.run_cli([cli, "plugin", "install", "sanmu-skills", "--marketplace", str(self.source), "--json"])
        self.assertIn("sanmu-skills", installed)
        self.assert_installed(self.sandbox / "claude")
        details = self.run_cli([cli, "plugin", "details", "sanmu-skills@sanmu-skills"])
        self.assertIn("sanmu", details.lower())
        print("Claude isolated marketplace install: three skills + CLI + MCP files verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
