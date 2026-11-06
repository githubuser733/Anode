"""Opt-in Windows adaptation of diagnosing-bugs/scripts/hitl-loop.template.sh.

HITL step: type nihao without committing in the owned parent fixture.
Capture: WM_IME composition/candidate/focus events, no text or key codes.
No main-desktop input injection, hooks, WeChat actions, or seat restart.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/wechat_monitor"))
from native_delivery import Anode

BASE = ROOT / "artifacts/anode-ime-interruption-20261104-8c8dc268"
CSC = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")


def save_new(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)


def events(path):
    if not path.exists():
        return []
    lines = path.read_text("utf-8").splitlines()
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            # The writer may currently be appending its final line.
            break
    return result


def verdict(rows, typed_ok):
    starts = [i for i, r in enumerate(rows) if r["kind"] == "probe_start"]
    ends = [i for i, r in enumerate(rows) if r["kind"] == "probe_end"]
    if not starts or not ends or not typed_ok:
        return "INCONCLUSIVE_MISSING_PROBE_OR_CHILD_INPUT"
    a, b = starts[0], ends[0]
    if b <= a or not rows[a]["composing"] or rows[a]["compositionBytes"] <= 0 or not rows[a]["focused"]:
        return "INCONCLUSIVE_NO_STARTING_COMPOSITION"
    during = rows[a:b+1]
    if any(r["kind"] in {"key_down", "mouse_down"} for r in during):
        return "INCONCLUSIVE_PARENT_INPUT_DURING_PROBE"
    if any(r["kind"] == "composition_change" and r.get("flags", 0) & 0x0800 for r in during):
        return "RED_UNEXPECTED_COMMIT"
    if any(r["kind"] in {"composition_end", "candidate_close"} for r in during):
        return "RED_COMPOSITION_OR_CANDIDATE_ENDED"
    if any(r["kind"] == "focus_lost" or (r["kind"] == "sample" and not r["focused"]) for r in during):
        return "RED_PARENT_FOCUS_LOST"
    if any(r["kind"] == "sample" and (not r["composing"] or r["compositionBytes"] <= 0) for r in during):
        return "RED_COMPOSITION_DISAPPEARED"
    return "GREEN_COMPOSITION_PRESERVED_THIS_RUN"


def selftest():
    start = dict(kind="probe_start", composing=True, compositionBytes=10, focused=True)
    end = dict(start, kind="probe_end")
    assert verdict([start, end], True).startswith("GREEN")
    assert verdict([start, dict(start, kind="composition_end"), end], True).startswith("RED_COMPOSITION")
    assert verdict([start, dict(start, kind="candidate_close"), end], True).startswith("RED_COMPOSITION")
    assert verdict([start, dict(start, kind="composition_change", flags=0x0800), end], True) == "RED_UNEXPECTED_COMMIT"
    assert verdict([start, dict(start, kind="focus_lost"), end], True) == "RED_PARENT_FOCUS_LOST"
    assert verdict([start, dict(start, kind="sample", compositionBytes=0), end], True) == "RED_COMPOSITION_DISAPPEARED"
    assert verdict([start, dict(start, kind="key_down"), end], True).startswith("INCONCLUSIVE")
    assert verdict([start, end], False).startswith("INCONCLUSIVE")
    assert verdict([dict(start, composing=False), end], True).startswith("INCONCLUSIVE")
    assert verdict([], True).startswith("INCONCLUSIVE")
    print("VERDICT_SELFTEST_OK cases=10 (synthetic traces, not a live reproduction)", flush=True)


class DiagnosticAnode(Anode):
    def tool(self, name, arguments=None):
        allowed = {"seat_status", "seat_lease", "seat_run", "seat_windows", "seat_observe", "seat_element", "seat_click", "seat_type", "seat_window"}
        if name not in allowed:
            raise ValueError("DIAGNOSTIC_TOOL_NOT_ALLOWED")
        value = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if value.get("isError"):
            raise RuntimeError(str(value)[:1500])
        if "structuredContent" in value:
            return value["structuredContent"]
        return json.loads(next(c["text"] for c in value["content"] if c["type"] == "text"))


def wait_for(path, kind, deadline, parent=None):
    while time.monotonic() < deadline:
        rows = events(path)
        if any(r["kind"] == kind for r in rows):
            return rows
        if any(r["kind"] == "closed" for r in rows) or (parent and parent.poll() is not None):
            raise RuntimeError("PARENT_FIXTURE_CLOSED")
        time.sleep(0.1)
    raise TimeoutError("WAITING_FOR_" + kind.upper())


def run(live, wait_seconds):
    run_dir = BASE / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    exe = run_dir / "ImeProbe.exe"
    result = subprocess.run([str(CSC), "/nologo", "/target:exe", "/platform:x64", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Web.Extensions.dll", "/out:" + str(exe), str(Path(__file__).with_name("ImeProbe.cs"))], capture_output=True, text=True)
    save_new(run_dir / "compile.json", dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr))
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    query = subprocess.run([str(exe), "--selftest"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    save_new(run_dir / "interop-selftest.json", dict(returncode=query.returncode, stdout=query.stdout, stderr=query.stderr))
    if query.returncode:
        raise RuntimeError(query.stdout + query.stderr)
    print("FIXTURE_COMPILE_OK " + query.stdout.strip(), flush=True)
    print("EVIDENCE=" + str(run_dir), flush=True)
    if not live:
        print("LIVE_IME_REPRODUCTION_UNTESTED", flush=True)
        return 0
    anode, parent, child_id = None, None, None
    answer = {"verdict": "INCONCLUSIVE_NOT_RUN", "liveReproduction": True, "inputTextRecorded": False}
    try:
        anode = DiagnosticAnode(run_dir)
        status = anode.tool("seat_status")
        save_new(run_dir / "status-before.json", status)
        if status["state"] != "ready" or status["session"] == status["parentSession"]:
            raise RuntimeError("EXISTING_READY_SEAT_REQUIRED")
        anode.tool("seat_lease", {"action": "acquire", "ttlSeconds": 600, "waitSeconds": 0})
        anode.lease_acquired = True
        manifest = json.loads((ROOT / "config/anode-local-mcp.json").read_text("utf-8"))
        launched = anode.tool("seat_run", {"path": str(ROOT / manifest["executable"]), "args": ["__desktop-fixture"]})
        if launched["session"] != status["session"]:
            raise RuntimeError("CHILD_FIXTURE_WRONG_SESSION")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            windows = anode.tool("seat_windows", {"pid": launched["pid"]})["windows"]
            if len(windows) == 1:
                child_id = windows[0]["windowId"]
                break
            time.sleep(0.2)
        if child_id is None:
            raise RuntimeError("OWNED_CHILD_FIXTURE_MISSING")
        def observe():
            return anode.tool("seat_observe", {"windowId": child_id, "includeScreenshot": False, "maxTextChars": 2000})
        def editor(observation):
            fields = [e for e in observation["elements"] if e.get("automationId") == "DraftNote"]
            if len(fields) != 1:
                raise RuntimeError("CHILD_DRAFT_FIELD_NOT_UNIQUE")
            return fields[0]
        initial = observe()
        anode.tool("seat_element", {"snapshotId": initial["snapshotId"], "elementId": editor(initial)["id"], "action": "set_value", "value": ""})
        prepared = observe()
        if editor(prepared).get("text") != "":
            raise RuntimeError("CHILD_FIELD_NOT_EMPTY")
        parent = subprocess.Popen([str(exe), str(run_dir), str(status["parentSession"])], creationflags=subprocess.CREATE_NO_WINDOW)
        path = run_dir / "parent-events.jsonl"
        print("WAITING_FOR_USER: 点击 Anode 中文输入隔离测试，在框中输入 nihao，保留候选框，停手约 15 秒。", flush=True)
        wait_for(path, "ready", time.monotonic() + wait_seconds, parent)
        # Freshly observe the owned target before injecting; never target user apps.
        target = editor(observe())
        (run_dir / "start.flag").open("x").close()
        wait_for(path, "probe_start", time.monotonic() + 3, parent)
        box = target["bounds"]
        anode.tool("seat_click", {"x": round(box["x"] + box["width"] / 2), "y": round(box["y"] + box["height"] / 2)})
        text = "Anode中文隔离测试abc123；" * 36
        typed = anode.tool("seat_type", {"text": text, "perCharMs": 15})
        save_new(run_dir / "child-type-result.json", typed)
        (run_dir / "finish.flag").open("x").close()
        rows = wait_for(path, "probe_end", time.monotonic() + 3, parent)
        typed_ok = editor(observe()).get("text") == text
        answer.update(verdict=verdict(rows, typed_ok), childTextVerified=typed_ok, childCharacters=len(text), viewerVisible=status["viewerVisible"], viewOnly=status["viewOnly"])
    except Exception as error:
        answer.update(verdict="INCONCLUSIVE", error=str(error))
    finally:
        # Only close disposable fixtures owned by this invocation; never kill processes.
        if parent and parent.poll() is None:
            (run_dir / "close.flag").open("x").close()
            try:
                parent.wait(timeout=5)
            except subprocess.TimeoutExpired:
                answer["cleanupParent"] = "UNFINISHED_NORMAL_CLOSE_TIMEOUT"
        if anode:
            try:
                if child_id:
                    anode.tool("seat_window", {"windowId": child_id, "action": "close"})
            except Exception as error:
                answer["cleanupChild"] = str(error)
            try:
                anode.close()
            except Exception as error:
                answer["cleanupMcp"] = str(error)
        save_new(run_dir / "result.json", answer)
        print(json.dumps(answer, ensure_ascii=False), flush=True)
    return 0 if answer["verdict"].startswith("GREEN") else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Open owned fixtures; requires user to begin real Chinese composition.")
    parser.add_argument("--check", action="store_true", help="Compile, query native IMM state and check verdict logic; no visible windows.")
    parser.add_argument("--wait-seconds", type=int, default=180)
    args = parser.parse_args()
    if not (args.live or args.check):
        parser.error("Choose --check or --live")
    if not 10 <= args.wait_seconds <= 300:
        parser.error("--wait-seconds must be 10..300")
    selftest()
    raise SystemExit(run(args.live, args.wait_seconds))
