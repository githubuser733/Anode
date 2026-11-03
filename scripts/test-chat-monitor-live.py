"""Retained-evidence live test: owned child fixture, parent notifications, no real chat input."""
import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("resident_monitor", Path(__file__).with_name("watch-chat-monitor.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def lines(path):
    if not Path(path).exists():
        return []
    raw = Path(path).read_bytes().splitlines(keepends=True)
    return [json.loads(line) for line in raw if line.endswith(b"\n")]


def records(directory):
    return [item for path in sorted(Path(directory).glob("journal-*.jsonl")) for item in lines(path)]


def elapsed(end, start):
    return (datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()*1000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    base = json.loads(Path(args.config).read_text(encoding="utf-8"))
    token = json.loads(sys.stdin.readline())["leaseToken"]
    out = Path(base["testOutput"])
    out.mkdir(parents=True, exist_ok=False)
    client = SimpleNamespace(c=base, token=token)
    call = lambda arguments, **kwargs: module.Monitor.cli(client, arguments, **kwargs)
    state = call(["status", "--json"])
    assert state["state"] == "ready" and state["session"] == base["session"] and state["viewOnly"]
    assert state["lease"]["ownerAgentId"] == base["agent"]
    source = out / "published.jsonl"
    fixture_job = monitor = None
    stdout = (out / "monitor.stdout").open("x", encoding="utf-8")
    stderr = (out / "monitor.stderr").open("x", encoding="utf-8")
    try:
        fixture_job = call(["exec", "--cwd", base["root"], "--timeout", "150000", "--wait", "1000", "--json", "--",
            base["reader"], "--channel", "main", "__chat-fixture", "--out", str(source), "--wait-trigger"], leased=True)
        deadline = time.monotonic()+15
        while True:
            windows = call(["windows", "--query", "Anode keyword monitor fixture", "--json"], leased=True)["windows"]
            if len(windows) == 1:
                break
            assert len(windows) < 2, "Fixture window identity is ambiguous"
            assert time.monotonic() < deadline, "Owned fixture did not create its window"
            time.sleep(.2)
        config = {**base, "pid": windows[0]["pid"], "processName": "anode", "windowTitle": "Anode keyword monitor fixture", "listName": "消息",
            "output": str(out / "monitor"), "stateDirectory": str(out / "state"), "durationSeconds": 110,
            "segmentMs": 90000, "tailCount": 6, "intervalMs": 250}
        config_path = out / "monitor-config.json"
        with config_path.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        monitor = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("watch-chat-monitor.py")),
            "--config", str(config_path)], stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
            encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        monitor.stdin.write(json.dumps({"leaseToken": token})+"\n")
        monitor.stdin.close()
        triggered = stopped = injected = False
        deadline = time.monotonic()+120
        while monitor.poll() is None:
            assert time.monotonic() < deadline, "Live test deadline exceeded"
            progress = lines(Path(config["output"]) / "progress.jsonl")
            last = progress[-1] if progress else {}
            if not triggered and last.get("polls", 0) > 0:
                Path(str(source)+".begin").open("x").close()
                module.append(out / "test-control.jsonl", {"kind": "trigger", "atUtc": module.utc(), "polls": last["polls"]})
                triggered = True
            published = lines(source)
            if base.get("injectReaderFailure") and not injected and len(published) >= 2 and last.get("events", 0) >= 2:
                starts = [r for r in records(config["stateDirectory"]) if r["kind"] == "readerStarted"]
                assert starts, "No owned reader job is recorded for fault injection"
                job_id = starts[-1]["jobId"]
                result = call(["job", job_id, "--cancel", "--json"])
                module.append(out / "test-control.jsonl", {"kind": "faultInjected", "atUtc": module.utc(),
                    "jobId": job_id, "state": result["state"]})
                injected = True
            if not stopped and len(published) == 28 and last.get("events") == 28 and last.get("pendingActions") == 0:
                (Path(config["output"]) / "stop.flag").open("x").close()
                stopped = True
            time.sleep(.1)
        stdout.flush()
        stderr.flush()
        assert monitor.returncode == 0, "Monitor failed; full stderr: "+(out / "monitor.stderr").read_text(encoding="utf-8")
        published = lines(source)
        journal = records(config["stateDirectory"])
        events = [event for batch in journal if batch["kind"] == "batch" for event in batch["events"]]
        source_counts, event_counts = Counter(p["text"] for p in published), Counter(e["text"] for e in events)
        missed = sum((source_counts-event_counts).values())
        duplicates = sum((event_counts-source_counts).values())
        assert len(published) == 28 and missed == 0 and duplicates == 0, (len(published), len(events), missed, duplicates)
        expected_hits = sum(any(word.casefold() in p["text"].casefold() for word in base["keywords"]) for p in published)
        assert expected_hits == 26 and sum(bool(event["keywords"]) for event in events) == expected_hits
        assert any(len(e["text"]) > 5000 and "USDT" in e["keywords"] for e in events), "Long tail keyword was lost"
        source_queues = defaultdict(deque)
        for item in published:
            source_queues[item["text"]].append(item)
        published_at = {}
        log_delays = []
        for event in events:
            item = source_queues[event["text"]].popleft()
            published_at[event["id"]] = item["publishedAtUtc"]
            log_delays.append(elapsed(event["recordedAtUtc"], item["publishedAtUtc"]))
        actions = [a for r in journal if r["kind"] == "batch" for a in r["actions"]]
        receipts = {(r["id"], r["channel"]): r for r in journal if r["kind"] == "receipt"}
        notice_delays, capture_delays = [], []
        for action in actions:
            assert (action["id"], "notify") in receipts and (action["id"], "capture") in receipts
            assert receipts[action["id"], "notify"]["state"] == "requested"
            capture = receipts[action["id"], "capture"]
            assert capture["state"] == "saved" and (capture["width"], capture["height"]) == (1920, 1080)
            assert module.hashlib.sha256(Path(capture["path"]).read_bytes()).hexdigest() == capture["sha256"]
            for event_id in action["eventIds"]:
                notice_delays.append(elapsed(receipts[action["id"], "notify"]["atUtc"], published_at[event_id]))
                capture_delays.append(elapsed(capture["atUtc"], published_at[event_id]))
        notifications = [r for p in Path(config["output"]).glob("*-receipts.jsonl") for r in lines(p)]
        starts = [r for r in journal if r["kind"] == "readerStarted"]
        if base.get("injectReaderFailure"):
            assert injected and len(starts) >= 2, "Reader recovery was not exercised"
        result = {"status": "RESIDENT_MONITOR_LIVE_FIXTURE_OK", "sourceKind": "owned child fixture, not WeChat arrival",
            "published": len(published), "recorded": len(events), "keywordHits": expected_hits,
            "missed": missed, "duplicateEvents": duplicates, "readerJobs": len(starts), "faultInjected": injected,
            "publicationToLogMs": module.summary(log_delays), "publicationToNotificationRequestMs": module.summary(notice_delays),
            "publicationToCaptureMs": module.summary(capture_delays), "dataNotificationBatches": len(actions),
            "osShownEvents": sum(r["state"] == "shown" for r in notifications), "screenshots": len(actions),
            "monitorExitCode": monitor.returncode, "chatMessagesSent": 0, "sourceArrivalLatencyForWeChatMeasured": False}
        module.append(out / "verification.jsonl", result)
        print(json.dumps(result), flush=True)
    finally:
        if monitor and monitor.poll() is None:
            stop = out / "monitor" / "stop.flag"
            if not stop.exists():
                stop.open("x").close()
            monitor.wait(timeout=50)
        if fixture_job:
            call(["job", fixture_job["jobId"], "--cancel", "--json"])
        stdout.close()
        stderr.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
