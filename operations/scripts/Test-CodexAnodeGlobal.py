"""Inspect real Codex requests against a local fixed-response stub; no real model call."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import subprocess
import threading
import time
import uuid
import tomllib

import tiktoken


ROOT = Path(__file__).resolve().parent.parent


def save(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def save_text(path, value):
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-exe", required=True, type=Path)
    parser.add_argument("--model-cache", required=True, type=Path)
    parser.add_argument("--user-config", required=True, type=Path)
    args = parser.parse_args()
    config_before = hashlib.sha256(args.user_config.read_bytes()).hexdigest()
    run = ROOT / "artifacts" / ("codex-anode-global-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run.mkdir()
    (run / "cwd").mkdir()
    (run / "logs").mkdir()
    cache = json.loads(args.model_cache.read_text(encoding="utf-8-sig"))
    model = next(m for m in cache["models"] if m["slug"] == "gpt-6-astra")
    save(run / "model-catalog.json", {"models": [model]})
    user_settings = tomllib.loads(args.user_config.read_text(encoding="utf-8-sig"))
    config = user_settings["mcp_servers"]["anode"]
    anode = Path(config["command"])
    if not anode.is_file() or not args.codex_exe.is_file():
        raise RuntimeError("Executable missing")
    encoding = tiktoken.get_encoding("o200k_base")
    captures = []
    errors = []
    active_case = [""]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = json.dumps({"models": [model]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                if self.headers.get("Authorization"):
                    raise RuntimeError("Unexpected authorization on local diagnostic request; not recorded")
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if self.headers.get("Content-Encoding"):
                    raise RuntimeError("Unexpected compressed diagnostic request")
                request = json.loads(raw)
                captures.append({"case": active_case[0], "path": self.path, "request": request})
                output = {"id": "msg_local_capture", "type": "message", "role": "assistant",
                          "status": "completed", "phase": "final_answer",
                          "content": [{"type": "output_text", "text": "LOCAL_CAPTURE_OK", "annotations": []}]}
                case_count = sum(c["case"] == active_case[0] for c in captures)
                if case_count == 1:
                    script = "const found = ALL_TOOLS.filter(t => t.name.includes('__anode__')); text({probe_count: found.length, probe_names: found.map(t => t.name), probe_extended_text: JSON.stringify(found.find(t => t.name.endsWith('__seat_observe'))).includes('maxNameChars')}); const selected = found.find(t => t.name.endsWith('__seat_status')); if (selected) { text({probe_selected_definition: selected}); const started = Date.now(); const r = await tools[selected.name]({}); const status = JSON.parse(r.content.find(c => c.type === 'text').text); text({probe_state: status.state, probe_session: status.session, probe_call_ms: Date.now()-started}); }"
                    output = {"id": "ctc_local_discovery", "type": "custom_tool_call", "call_id": "call_local_discovery",
                              "name": "exec", "namespace": "functions", "input": script}
                events = [
                    {"type": "response.created", "sequence_number": 0,
                     "response": {"id": "resp_local_capture", "object": "response", "created_at": int(time.time()),
                                  "status": "in_progress", "model": "gpt-6-astra", "output": []}},
                    {"type": "response.output_item.done", "sequence_number": 1, "output_index": 0, "item": output},
                    {"type": "response.completed", "sequence_number": 2,
                     "response": {"id": "resp_local_capture", "object": "response", "created_at": int(time.time()),
                                  "status": "completed", "model": "gpt-6-astra", "output": [output]}},
                ]
                body = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                errors.append(str(exc))
                self.send_error(500, "Local capture failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    try:
        for case in ("project_directory", "user_directory", "isolated_strict_anode"):
            active_case[0] = case
            settings = {
                "model_provider": "local_capture",
                "model_providers.local_capture.name": "Local fixed-response diagnostic",
                "model_providers.local_capture.base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "model_providers.local_capture.wire_api": "responses",
                "model_providers.local_capture.requires_openai_auth": False,
                "model_providers.local_capture.supports_websockets": False,
                "model_providers.local_capture.request_max_retries": 0,
                "model_providers.local_capture.stream_max_retries": 0,
                "model_providers.local_capture.stream_idle_timeout_ms": 10000,
                "model_catalog_json": str(run / "model-catalog.json"),
                "log_dir": str(run / "logs"),
                "features.hooks": False, "features.apps": False, "features.plugins": False,
                "features.multi_agent": False, "features.enable_request_compression": False,
            }
            if case == "isolated_strict_anode":
                settings["mcp_servers.anode.command"] = config["command"]
                settings["mcp_servers.anode.args"] = config["args"]
            else:
                for server_name in user_settings.get("mcp_servers", {}):
                    if server_name != "anode":
                        settings[f"mcp_servers.{server_name}.enabled"] = False
            settings["mcp_servers.anode.required"] = True
            command = [str(args.codex_exe), "exec", "--ephemeral",
                       "--skip-git-repo-check", "--json", "--color", "never",
                       "--sandbox", "read-only", "--cd", str(ROOT if case == "project_directory" else Path.home()), "--model", "gpt-6-astra"]
            # Real global loading follows the client's normal mode. The separate strict
            # case validates the exact Anode entry without unrelated legacy MCP fields.
            if case == "isolated_strict_anode":
                command.extend(["--ignore-user-config", "--strict-config"])
            for key, value in settings.items():
                command.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
            command.append("Reply with exactly LOCAL_CAPTURE_OK. Do not call tools.")
            save(run / (case + "-settings.json"), settings)
            before = time.perf_counter()
            process = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                                     timeout=60, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
            elapsed = (time.perf_counter() - before) * 1000
            save_text(run / (case + "-stdout.jsonl"), process.stdout)
            save_text(run / (case + "-stderr.log"), process.stderr)
            current = [c for c in captures if c["case"] == case]
            save(run / (case + "-requests.json"), current)
            if process.returncode or not current or errors:
                raise RuntimeError({"case": case, "exit": process.returncode, "errors": errors,
                                    "stdout": process.stdout, "stderr": process.stderr})
            if "LOCAL_CAPTURE_OK" not in process.stdout:
                raise RuntimeError("Fixed response not accepted")
            request = current[0]["request"]
            input_text = compact(request.get("input", []))
            tools_text = compact(request.get("tools", []))
            instructions = request.get("instructions", "") or ""
            all_text = instructions + input_text + tools_text
            measure = lambda s: len(encoding.encode(s, disallowed_special=()))
            def strings(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, list):
                    for part in value:
                        yield from strings(part)
                elif isinstance(value, dict):
                    for part in value.values():
                        yield from strings(part)
            returned = "\n".join(strings(current[-1]["request"].get("input", [])))
            count_matches = re.findall(r'"probe_count"\s*:\s*(\d+)', returned)
            actual_count = int(count_matches[-1]) if count_matches else None
            expected_count = 41
            if actual_count != expected_count:
                raise RuntimeError({"case": case, "expected_registry_count": expected_count,
                                    "actual_registry_count": actual_count, "request_count": len(current),
                                    "stdout": process.stdout, "stderr": process.stderr})
            if not re.search(r'"probe_state"\s*:\s*"ready"', returned):
                raise RuntimeError("Native registered seat_status did not return ready")
            if not re.search(r'"probe_extended_text"\s*:\s*true', returned):
                raise RuntimeError("The native tool registry lacks the merged text options")
            result = {"merged_text_schema": True, "registry_count": actual_count, "native_status_ready": True, "case": case, "client_wall_ms": round(elapsed, 3), "request_count": len(current),
                      "request_top_keys": list(request), "tools_o200k_tokens": measure(tools_text),
                      "input_o200k_tokens": measure(input_text), "instructions_o200k_tokens": measure(instructions),
                      "combined_o200k_tokens": measure(all_text),
                      "anode_probe_mentions": all_text.count("anode_probe"),
                      "seat_click_mentions": all_text.count("seat_click"),
                      "gamepad_attach_mentions": all_text.count("gamepad_attach"),
                      "tool_types": [t.get("type") for t in request.get("tools", [])]}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        config_after = hashlib.sha256(args.user_config.read_bytes()).hexdigest()
        if config_after != config_before:
            raise RuntimeError("User config changed during test")
        summary = {"status": "PASS", "evidence": str(run), "results": results,
                   "cli_version": subprocess.check_output([str(args.codex_exe), "--version"], text=True).strip(),
                   "model_metadata": {k: model.get(k) for k in ("slug", "supports_search_tool", "tool_mode")},
                   "user_config_unchanged": True, "real_model_calls": 0,
                   "accountSwitchTested": False, "note": "Real Codex loads the existing user-global Anode registration in two working directories. Fixed local responses trigger discovery and read-only seat_status; no real model inference or account switch."}
        save(run / "summary.json", summary)
        print("CODEX_ANODE_GLOBAL_PROBE_OK " + str(run), flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
