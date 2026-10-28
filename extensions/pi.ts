import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const cliPath = fileURLToPath(new URL("../scripts/sanmu.mjs", import.meta.url));

/** Register the bundled server; Pi owns its transport, tools, and session cleanup. */
export default function sanmu(pi: ExtensionAPI) {
  if (typeof pi.registerMcpServer !== "function") {
    throw new Error(
      "sanmu-skills requires Pi 1.4 or newer with native registerMcpServer(). Update Pi; no third-party MCP bridge is needed.",
    );
  }

  // The factory also runs in invocations without a session. Register here only
  // once the real session cwd is available; the built-in MCP extension connects it.
  pi.on("session_start", (_event, ctx) => {
    if (!existsSync(cliPath)) {
      throw new Error(`sanmu-skills package is incomplete: missing bundled CLI ${cliPath}`);
    }
    pi.registerMcpServer("sanmu", {
      command: process.execPath,
      args: [cliPath, "mcp"],
      cwd: ctx.cwd,
      env: {
        SANMU_HOST: "pi",
        SANMU_WORKSPACE_ROOT: ctx.cwd,
      },
      description: "Sanmu literature search, PDF/SI retrieval, Zotero, document conversion, and paper reading.",
      exposure: "codemode",
      toolExposure: { doctor_status: "direct" },
      // Server operations include 180/240/280-second subprocess budgets.
      timeout: 300,
    });
  });

  // Do not spawn a second client or unregister during shutdown. Pi's built-in
  // MCP session_shutdown handler closes stdin and terminates the process tree.
}
