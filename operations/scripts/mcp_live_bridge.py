"""Local, serial MCP test bridge. No client configuration is changed."""
import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]


def parent_state():
    handle = user32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    point = POINT()
    ok = user32.GetCursorPos(ctypes.byref(point))
    return {"foreground_hwnd": handle, "foreground_pid": pid.value,
            "cursor": [point.x, point.y] if ok else None}


parser = argparse.ArgumentParser()
parser.add_argument("project", choices=["anode", "bgdesk"])
parser.add_argument("--root", required=True)
parser.add_argument("--evidence", required=True)
parser.add_argument("--run-id", required=True)
parser.add_argument("--channel", default="main")
args = parser.parse_args()
root = Path(args.root).resolve()
evidence = Path(args.evidence).resolve() / args.project
evidence.mkdir(parents=True, exist_ok=False)
runtime = root / "work" / args.run_id / args.project
runtime.mkdir(parents=True, exist_ok=False)
if args.project == "anode":
    active = json.loads((root / "config/anode-local-mcp.json").read_text(encoding="utf-8-sig"))
    executable = (root / active["executable"]).resolve(strict=True)
    if not executable.is_relative_to((root / "work").resolve()):
        raise ValueError("Active Anode executable is outside the project work directory")
    with executable.open("rb") as handle:
        actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual_hash != active["sha256"]:
        raise ValueError("Active Anode executable hash differs from its manifest")
    command = [str(executable), "--channel", args.channel, "mcp"]
else:
    command = [str(root / "work/releases/bgdesk-v2.0.0/portable/bgdesk/bgdesk.exe"), "mcp"]
assert Path(command[0]).is_file(), command[0]
environment = os.environ.copy()
environment["ANODE_AGENT_ID"] = "codex-" + args.run_id + "-" + args.project
environment["ANODE_AGENT_NAME"] = "Codex desktop recheck " + args.run_id
stderr_file = (runtime / "mcp-stderr.log").open("x", encoding="utf-8")
process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=stderr_file, text=True, encoding="utf-8", bufsize=1,
                           cwd=root, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
responses = queue.Queue()


def read_output():
    for line in process.stdout:
        try:
            message = json.loads(line)
            if "id" in message:
                responses.put(message)
        except json.JSONDecodeError:
            responses.put({"bridge_error": "Non-JSON MCP output", "line": line[:500]})
    responses.put({"bridge_error": "MCP process exited", "exit_code": process.poll()})


threading.Thread(target=read_output, daemon=True).start()
serial = threading.Lock()
counter = 0
image_counter = 0
audit = (evidence / "rpc.jsonl").open("x", encoding="utf-8")


def materialize(value):
    global image_counter
    if isinstance(value, list):
        return [materialize(v) for v in value]
    if isinstance(value, dict):
        if value.get("type") == "image" and "data" in value:
            image_counter += 1
            blob = base64.b64decode(value["data"], validate=True)
            suffix = ".png" if value.get("mimeType") == "image/png" else ".image"
            image_path = evidence / f"shot-{counter:04d}-{image_counter:03d}{suffix}"
            with image_path.open("xb") as handle:
                handle.write(blob)
            return {"type": "image_saved", "path": str(image_path), "bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest()}
        return {k: ("[redacted]" if k.lower() in {"password", "token", "leasetoken", "secret"}
                    else materialize(v)) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 200000:
        return {"omitted_large_string_length": len(value)}
    return value


def rpc(method, params, timeout=60):
    global counter
    with serial:
        if process.poll() is not None:
            raise RuntimeError(f"MCP exited: {process.returncode}")
        counter += 1
        request = {"jsonrpc": "2.0", "id": counter, "method": method, "params": params}
        before = parent_state()
        started = time.time()
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        try:
            while True:
                remaining = timeout - (time.time() - started)
                response = responses.get(timeout=max(0.001, remaining))
                if "bridge_error" in response or response.get("id") == counter:
                    break
                audit.write(json.dumps({"late_response": materialize(response)}, ensure_ascii=False) + "\n")
        except queue.Empty:
            response = {"bridge_error": "Timed out; do not replay the action", "request_id": counter}
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "request": request,
                  "response": materialize(response), "elapsed_seconds": round(time.time() - started, 3),
                  "parent_before": before, "parent_after": parent_state()}
        audit.write(json.dumps(record, ensure_ascii=False) + "\n")
        audit.flush()
        return record


initialized = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "codex", "version": "run2"}})
assert "result" in initialized["response"], initialized
process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
process.stdin.flush()
discovered = rpc("tools/list", {})
tools = discovered["response"]["result"]["tools"]
with (evidence / "tools.json").open("x", encoding="utf-8") as handle:
    json.dump(tools, handle, ensure_ascii=False, indent=2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        try:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/rpc"
            result = rpc(request["method"], request.get("params", {}), request.get("timeout", 60))
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"bridge_error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
info = {"project": args.project, "port": server.server_port, "bridge_pid": os.getpid(),
        "mcp_pid": process.pid, "server": initialized["response"]["result"]["serverInfo"],
        "tool_count": len(tools), "runtime": str(runtime), "evidence": str(evidence)}
with (runtime / "bridge.json").open("x", encoding="utf-8") as handle:
    json.dump(info, handle, ensure_ascii=False, indent=2)
print(json.dumps(info, ensure_ascii=False), flush=True)
try:
    server.serve_forever(poll_interval=0.25)
finally:
    process.stdin.close()
    audit.close()
    stderr_file.close()
