#!/usr/bin/env python3
"""Serve the real panel and bridge its host RPC to an isolated stdio MCP process."""

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class RpcClient:
    def __init__(self, plugin_root, data_dir, workspace):
        self.plugin_root = Path(plugin_root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.workspace = Path(workspace).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, SANMU_PLUGIN_ROOT=str(self.plugin_root),
                   ZCODE_PLUGIN_DATA=str(self.data_dir), ZCODE_WORKSPACE_ROOT=str(self.workspace),
                   PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        self.process = subprocess.Popen(
            ["node", str(self.plugin_root / "scripts/mcp-server.mjs")],
            cwd=self.workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self.responses = queue.Queue()
        self.lock = threading.Lock()
        self.counter = 0
        self.stderr_lines = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "sanmu-isolated-test", "version": "1"}})

    def _read_stdout(self):
        for line in self.process.stdout:
            try:
                self.responses.put(json.loads(line))
            except json.JSONDecodeError:
                self.responses.put({"error": {"message": "MCP emitted non-JSON stdout"}})
        self.responses.put({"error": {"message": "MCP process closed stdout"}})

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr_lines.append(line)

    def request(self, method, params=None, timeout=300):
        with self.lock:
            self.counter += 1
            rpc_id = self.counter
            payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"MCP request timed out: {method}")
                response = self.responses.get(timeout=remaining)
                if "id" in response and response["id"] != rpc_id:
                    continue
                if "error" in response:
                    raise RuntimeError(response["error"].get("message", "MCP error"))
                return response.get("result", {})

    def call(self, name, arguments=None, timeout=300):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)


HOST_PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Sanmu isolated panel test</title><style>
html,body{height:100%;margin:0;background:#f7f8fb}iframe{display:block;width:100%;height:100%;border:0}
</style></head><body><iframe id="panel" title="Sanmu paper search" src="/panel"></iframe>
<script>
const frame = document.getElementById("panel");
window.addEventListener("message", async (event) => {
  if (event.source !== frame.contentWindow || event.origin !== location.origin) return;
  const req = event.data;
  if (!req || req.jsonrpc !== "2.0" || req.id === undefined) return;
  let response;
  try {
    let result;
    if (req.method === "ui/initialize") {
      result = {protocolVersion:"2026-01-26",hostCapabilities:{},hostContext:{theme:"light",locale:"zh-CN"}};
    } else if (req.method === "tools/call") {
      const res = await fetch("/rpc", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(req.params)});
      result = await res.json();
    } else if (["ui/open-link","ui/message","ui/update-model-context"].includes(req.method)) {
      result = {};
    } else {
      throw new Error("Unsupported test-host method: " + req.method);
    }
    response = {jsonrpc:"2.0",id:req.id,result};
  } catch (error) {
    response = {jsonrpc:"2.0",id:req.id,error:{code:-32603,message:String(error.message || error)}};
  }
  frame.contentWindow.postMessage(response, location.origin);
});
</script></body></html>'''


def serve(client, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def respond(self, status, content, kind):
            body = content.encode("utf-8") if isinstance(content, str) else content
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                self.respond(200, HOST_PAGE, "text/html; charset=utf-8")
            elif path == "/panel":
                self.respond(200, (client.plugin_root / "assets/panel.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/standalone":
                panel = (client.plugin_root / "assets/panel.html").read_text(encoding="utf-8")
                bridge = HOST_PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
                bridge = bridge.replace('const frame = document.getElementById("panel");', 'const frame = {contentWindow: window};')
                bridge = bridge.replace('if (!req || req.jsonrpc !== "2.0" || req.id === undefined) return;',
                                        'if (!req || !req.method || req.jsonrpc !== "2.0" || req.id === undefined) return; event.stopImmediatePropagation();')
                panel = panel.replace("<head>", "<head><script>" + bridge + "</script>", 1)
                self.respond(200, panel, "text/html; charset=utf-8")
            else:
                self.respond(404, "Not found", "text/plain")

        def do_POST(self):
            if urlsplit(self.path).path != "/rpc":
                return self.respond(404, "Not found", "text/plain")
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2 * 1024 * 1024:
                    return self.respond(413, "Request too large", "text/plain")
                params = json.loads(self.rfile.read(size))
                result = client.call(params["name"], params.get("arguments", {}))
                self.respond(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as error:
                self.respond(200, json.dumps({"isError": True, "structuredContent": {
                    "error": {"message": str(error)}}}), "application/json; charset=utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"GUI test host ready: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    serve(RpcClient(options.plugin_root, options.data_dir, options.workspace), options.port)
