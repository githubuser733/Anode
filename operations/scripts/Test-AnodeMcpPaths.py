"""Read-only timing comparison. Creates new evidence; never starts/stops a seat."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import random
import shutil
import statistics
import subprocess
import threading
import time
import urllib.request
import uuid

import tiktoken


ROOT = Path(__file__).resolve().parent.parent


def save(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


class DirectMcp:
    def __init__(self, exe, channel):
        self.counter = 0
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [str(exe), "--channel", channel, "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", text=True, bufsize=1, cwd=ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.errors = []
        threading.Thread(target=self.read_stdout, daemon=True).start()
        threading.Thread(target=self.read_stderr, daemon=True).start()

    def read_stdout(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                self.messages.put({"error": "Non-JSON response", "line": line})
        self.messages.put({"error": "MCP stdout closed"})

    def read_stderr(self):
        self.errors.extend(self.process.stderr)

    def send(self, item):
        self.process.stdin.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def rpc(self, method, params):
        self.counter += 1
        self.send({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params})
        deadline = time.perf_counter() + 20
        while True:
            item = self.messages.get(timeout=max(0.001, deadline - time.perf_counter()))
            if "error" in item:
                raise RuntimeError(item)
            if item.get("id") == self.counter:
                return item["result"]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()  # Only this test-owned MCP process, not the desktop.
                self.process.wait(timeout=5)


def normalized(result):
    if result.get("isError"):
        raise RuntimeError(result)
    status = json.loads(next(c["text"] for c in result["content"] if c["type"] == "text"))
    return {"state": status.get("state"), "agentReady": status.get("agentReady"),
            "session": status.get("session"), "seatPid": status.get("seat", {}).get("pid"),
            "screen": status.get("seat", {}).get("screen")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-port", required=True, type=int)
    parser.add_argument("--samples", type=int, default=12)
    args = parser.parse_args()
    if not 3 <= args.samples <= 30:
        raise ValueError("Use 3 to 30 samples")
    run = ROOT / "artifacts" / ("anode-mcp-ab-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run.mkdir()
    config = json.loads((ROOT / "config" / "verified-desktop.json").read_text(encoding="utf-8-sig"))
    exe = ROOT / config["anodeExecutable"]
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not exe.is_file() or not shell:
        raise RuntimeError("Verified executable or PowerShell missing")
    started = time.perf_counter()
    direct = DirectMcp(exe, config["anodeChannel"])
    try:
        initialized = direct.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "readonly-path-benchmark", "version": "1"}})
        direct.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        catalog = direct.rpc("tools/list", {})["tools"]
        init_ms = (time.perf_counter() - started) * 1000
        definition = next(t for t in catalog if t["name"] == "seat_status")
        if not definition.get("annotations", {}).get("readOnlyHint"):
            raise RuntimeError("seat_status not advertised read-only")
        save(run / "tools.json", catalog)
        params = {"name": "seat_status", "arguments": {}}

        def http_call():
            data = json.dumps({"method": "tools/call", "params": params, "timeout": 20}).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{args.bridge_port}/rpc", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=25) as response:
                record = json.load(response)
            if "error" in record.get("response", {}) or record.get("bridge_error"):
                raise RuntimeError(record)
            return record["response"]["result"]

        def shell_call():
            result = subprocess.run(
                [shell, "-NoProfile", "-File", str(ROOT / "scripts" / "internal" / "live_call.ps1"),
                 "-Port", str(args.bridge_port), "-Tool", "seat_status", "-TimeoutSeconds", "20"],
                capture_output=True, encoding="utf-8-sig", errors="replace", timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW, cwd=ROOT,
            )
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return json.loads(result.stdout)

        calls = {"direct_stdio": lambda: direct.rpc("tools/call", params),
                 "bridge_http": http_call, "bridge_fresh_powershell": shell_call}
        baseline = normalized(calls["direct_stdio"]())
        if baseline["state"] != "ready":
            raise RuntimeError("Anode not ready; no seat will be started")
        for call in calls.values():
            if normalized(call()) != baseline:
                raise RuntimeError("Warmup endpoints differ")
        rows = []
        rng = random.Random(20261104)
        for round_id in range(args.samples):
            order = list(calls)
            rng.shuffle(order)
            for name in order:
                before = time.perf_counter()
                status = normalized(calls[name]())
                elapsed = (time.perf_counter() - before) * 1000
                if status != baseline:
                    raise RuntimeError("Endpoint state changed")
                rows.append({"round": round_id + 1, "path": name, "elapsed_ms": round(elapsed, 3),
                             "status": status})
        encoding = tiktoken.get_encoding("o200k_base")
        compact = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
        summary = {"status": "PASS", "evidence": str(run), "samples_per_path": args.samples,
                   "server": initialized["serverInfo"], "tool_count": len(catalog),
                   "direct_initialize_and_list_ms": round(init_ms, 3), "stable_desktop": baseline,
                   "shell": shell, "model_time_included": False, "catalog_chars": len(compact),
                   "catalog_o200k_base_tokens": len(encoding.encode(compact)),
                   "token_note": "Offline tokenizer count of raw catalog, NOT actual model billing or injected tokens.",
                   "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "timings": {}}
        for name in calls:
            values = [r["elapsed_ms"] for r in rows if r["path"] == name]
            summary["timings"][name] = {"median_ms": round(statistics.median(values), 3),
                                        "min_ms": min(values), "max_ms": max(values)}
        save(run / "samples.json", rows)
        save(run / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        print("ANODE_MCP_PATHS_BENCHMARK_OK", flush=True)
    finally:
        direct.close()


if __name__ == "__main__":
    main()
