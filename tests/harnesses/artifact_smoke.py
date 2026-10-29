"""Run the extracted release bundle using a prepared private runtime."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    args = parser.parse_args()
    args.archive = args.archive.resolve()
    args.runtime = args.runtime.resolve()
    with tempfile.TemporaryDirectory(prefix="sanmu-artifact-") as temporary:
        sandbox = Path(temporary)
        package = sandbox / "三木 package"
        workspace = sandbox / "workspace"
        workspace.mkdir()
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(package)
        python = args.runtime / "python" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        fixture = workspace / "paper.pdf"
        subprocess.run([str(python), "-c", "import fitz,sys; d=fitz.open(); p=d.new_page(); p.insert_text((72,72),'Portable research fixture. Formula x squared.'); d.save(sys.argv[1])", str(fixture)], check=True)
        env = dict(os.environ, SANMU_PLUGIN_ROOT=str(package), SANMU_DATA_DIR=str(args.runtime), SANMU_WORKSPACE_ROOT=str(workspace),
                   SANMU_PANEL_WORKER="1", SANMU_NO_AUTO_SETUP="1", SANMU_TEST_NO_BROWSER="1")
        process = subprocess.Popen(["node", str(package / "scripts/sanmu.mjs"), "mcp"], cwd=workspace, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        responses = queue.Queue()
        stderr = []
        def read_stdout():
            for line in process.stdout:
                responses.put(json.loads(line))
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=lambda: stderr.extend(process.stderr), daemon=True).start()
        count = 0
        def rpc(method, params):
            nonlocal count
            count += 1
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": count, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            response = responses.get(timeout=180)
            assert response["id"] == count, response
            assert "error" not in response, response
            result = response["result"]
            assert not result.get("isError"), result
            return result
        def call(name, data):
            return rpc("tools/call", {"name": name, "arguments": data})
        try:
            rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "artifact-smoke", "version": "1"}})
            tools = rpc("tools/list", {})["tools"]
            assert len(tools) == 24
            doctor = call("manage_doc2md", {"action": "doctor"})
            assert doctor["structuredContent"]["doc2mdDoctor"]["status"] == "pass", doctor
            converted = call("run_doc2md", {"mode": "research", "ocrEngine": "local_fast", "pdfPath": str(fixture), "saveDir": str(workspace / "output"), "outputFolderName": "portable fixture", "metadata": {"title": "Portable fixture", "year": 2026}})
            job = converted["structuredContent"]["activeDoc2mdJob"]
            assert job["status"] == "completed", job
            assert job["stats"]["mathPassed"] is True, job
            assert Path(job["stats"]["primaryMarkdownPath"]).is_file()
            call("manage_doc2md", {"action": "validate", "jobId": job["jobId"]})
            inventory = workspace / "inventory.json"
            result = subprocess.run(["node", str(package / "scripts/sanmu.mjs"), "reader", "evidence", "inventory", str(workspace / "output"), "--out", str(inventory)],
                                    cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            assert result.returncode == 0, result.stderr
            assert inventory.is_file()
            print(json.dumps({"archive": args.archive.name, "mcp_tools": len(tools), "doc2md_doctor": "pass", "local_fast_conversion": "pass", "independent_validation": "pass", "reader_inventory": "pass"}, ensure_ascii=False))
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
