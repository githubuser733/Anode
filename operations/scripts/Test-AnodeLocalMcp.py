"""Read-only stdio and user-global registration checks from two working directories."""
import argparse
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tomllib
import uuid

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "anode_local_mcp.py"


def save(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def probe(command, cwd):
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               creationflags=subprocess.CREATE_NO_WINDOW)
    replies = queue.Queue()
    errors = []
    def receive():
        for line in process.stdout:
            replies.put(json.loads(line))
    threading.Thread(target=receive, daemon=True).start()
    threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True).start()
    def request(identifier, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier,
                                       "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while True:
            response = replies.get(timeout=25)
            if response.get("id") == identifier:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response["result"]
    try:
        initialized = request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                               "clientInfo": {"name": "local-entry-check", "version": "1"}})
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        tools = request(2, "tools/list", {})["tools"]
        by_name = {tool["name"]: tool for tool in tools}
        assert len(tools) == 41
        assert "maxNameChars" in by_name["seat_observe"]["inputSchema"]["properties"]
        assert "textSource" in by_name["seat_element"]["inputSchema"]["properties"]
        assert "afterSequence" in by_name["seat_window"]["inputSchema"]["properties"]
        called = request(3, "tools/call", {"name": "seat_status", "arguments": {}})
        assert not called.get("isError"), called
        status = json.loads(next(c["text"] for c in called["content"] if c["type"] == "text"))
        return {"cwd": str(cwd), "server": initialized["serverInfo"], "tools": len(tools),
                "extendedTextTools": True, "state": status["state"], "session": status.get("session"),
                "seatPid": status.get("seat", {}).get("pid"), "channel": status.get("channel")}
    finally:
        process.stdin.close()
        # The launcher and its MCP child must both exit normally on EOF.
        process.wait(timeout=15)
        if process.returncode:
            raise RuntimeError({"exitCode": process.returncode, "stderr": "".join(errors)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-exe", type=Path)
    parser.add_argument("--user-config", type=Path, default=Path.home() / ".codex" / "config.toml")
    parser.add_argument("--entry-only", action="store_true")
    args = parser.parse_args()
    out = ROOT / "artifacts" / ("anode-local-mcp-check-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    out.mkdir()
    before = args.user_config.read_bytes()
    cfg = tomllib.loads(before.decode("utf-8-sig"))
    expected = [sys.executable, "-B", "-u", str(LAUNCHER)]
    results = []
    for cwd in (ROOT, Path.home()):
        if not args.entry_only:
            if args.codex_exe is None:
                parser.error("--codex-exe is required for registration verification")
            registered = subprocess.run([str(args.codex_exe), "mcp", "get", "anode", "--json"],
                                        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                                        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            details = json.loads(registered.stdout)
            assert details["enabled"] is True
            transport = details["transport"]
            actual = [transport["command"], *transport["args"]]
            assert actual == expected, actual
            assert transport["type"] == "stdio" and not transport.get("env"), transport
            assert cfg["mcp_servers"]["anode"]["command"] == expected[0]
        else:
            actual = expected
        results.append(probe(actual, cwd))
    assert results[0]["state"] == results[1]["state"]
    assert results[0]["seatPid"] == results[1]["seatPid"]
    assert args.user_config.read_bytes() == before, "This read-only check changed Codex configuration"
    summary = {"status": "ANODE_LOCAL_MCP_CHECK_OK", "entryOnly": args.entry_only,
               "userConfig": str(args.user_config), "userConfigSha256": hashlib.sha256(before).hexdigest(),
               "authentication": "none", "accountSwitchTested": False, "desktopStartedOrStopped": False,
               "results": results}
    save(out / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    print("EVIDENCE=" + str(out))


if __name__ == "__main__":
    main()
