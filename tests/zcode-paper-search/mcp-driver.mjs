import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

// A line-oriented MCP client. Supply isolated workspace/plugin-data paths through env.
export function startMcpServer(serverPath, { env = {}, cwd, nodeArgs = [], timeoutMs = 30000 } = {}) {
  const child = spawn(process.execPath, [...nodeArgs, serverPath], {
    cwd, env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"], windowsHide: true
  });
  const pending = new Map();
  let nextId = 1;
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  createInterface({ input: child.stdout }).on("line", (line) => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    const waiter = pending.get(message.id);
    if (!waiter) return;
    clearTimeout(waiter.timer);
    pending.delete(message.id);
    waiter.resolve(message);
  });
  const fail = (error) => {
    for (const waiter of pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    pending.clear();
  };
  child.on("error", fail);
  child.on("exit", (code) => fail(new Error(`MCP exited (${code}): ${stderr.slice(-1000)}`)));
  const request = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`MCP request timed out: ${method}`));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
  return {
    child,
    request,
    async call(name, args = {}) {
      const response = await request("tools/call", { name, arguments: args });
      if (response.error) throw new Error(response.error.message);
      return response.result;
    },
    notify(method, params = {}) {
      child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
    },
    async close() {
      if (child.exitCode !== null) return;
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.stdin.end();
      const timer = setTimeout(() => child.kill(), 1000);
      await exited;
      clearTimeout(timer);
    }
  };
}
