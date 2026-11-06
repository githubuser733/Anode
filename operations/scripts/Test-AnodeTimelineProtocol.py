"""Check MCP discovery without starting a daemon or a Windows child session."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

parser = argparse.ArgumentParser()
parser.add_argument("--exe", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
exe = Path(args.exe).resolve(strict=True)
output = Path(args.out).resolve()
output.mkdir(parents=True, exist_ok=False)
environment = os.environ.copy()
environment["ANODE_CHANNEL"] = "timeline-validation"
process = subprocess.Popen([str(exe), "--channel", "timeline-validation", "mcp"], stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=environment,
    creationflags=subprocess.CREATE_NO_WINDOW)
lines = queue.Queue()

def read_lines():
    for line in process.stdout:
        lines.put(line)

threading.Thread(target=read_lines, daemon=True).start()

def request(identifier, method, params):
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
    process.stdin.flush()
    response = json.loads(lines.get(timeout=15))
    assert response.get("id") == identifier and "result" in response, response
    return response["result"]

try:
    initialize = request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "Timeline protocol check", "version": "1"}})
    process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    process.stdin.flush()
    tools = request(2, "tools/list", {})["tools"]
    baseline = Path(__file__).resolve().parents[1] / "artifacts/mcp/project-mcp-20261031-113941-aec59fe8/anode/tools.json"
    expected = json.loads(baseline.read_text(encoding="utf-8-sig"))
    assert tools == expected, "MCP discovery schema differs from the verified original release."
    with (output / "tools.json").open("x", encoding="utf-8") as handle:
        json.dump(tools, handle, ensure_ascii=False, indent=2)
    result = {"status": "ANODE_TIMELINE_MCP_DISCOVERY_OK", "tools": len(tools),
        "server": initialize["serverInfo"], "schemaUnchanged": True, "desktopToolCalls": 0}
    with (output / "result.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result))
finally:
    process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)
