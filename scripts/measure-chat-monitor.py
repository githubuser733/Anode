"""Finite, local-only chat monitoring experiment through Anode CLI/exec.

The lease token is received on stdin and never written to evidence. No chat input
or external notification is sent. Source text is untrusted data, used only for matching.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def items(snapshot):
    return [n for n in snapshot.get("nodes", []) if n.get("role") == "ListItem"]


def overlap(previous, current):
    for size in range(min(len(previous), len(current)), 0, -1):
        if previous[-size:] == current[:size]:
            return size
    return 0


class Tracker:
    def __init__(self):
        self.previous = []

    def commit(self, nodes):
        self.previous = list(nodes)

    def compare(self, nodes):
        # WeChat recycles the same UIA runtime IDs for different rows. Ordered
        # text overlap is evidence of continuity; elementKey is diagnostic only.
        before = [n.get("text", "") for n in self.previous]
        after = [n.get("text", "") for n in nodes]
        common = overlap(before, after)
        if common:
            return [("new", n) for n in nodes[common:]], False, "textOverlap"
        if not self.previous:
            return [("new", n) for n in nodes], False, "initialEmpty"
        return [("unresolved", n) for n in nodes], bool(nodes), "overlapLost"


def metrics(values):
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(values), "min": min(values), "mean": statistics.mean(values),
        "median": statistics.median(values), "p95": ordered[max(0, math.ceil(0.95*len(ordered))-1)],
        "max": max(values)}


def self_test():
    def node(key, text):
        return {"elementKey": key, "text": text, "role": "ListItem"}
    t = Tracker()
    original = [node("a", "old"), node("b", "收")]
    t.commit(original)
    assert t.compare(original)[0] == [], "Baseline/history must not trigger alerts"
    repeated = [node("b", "收"), node("c", "same"), node("d", "same")]
    changes, gap, mode = t.compare(repeated)
    assert [kind for kind, _ in changes] == ["new", "new"] and not gap
    t.commit(repeated)
    changed = [node("b", "收更改"), node("c", "same"), node("d", "same")]
    assert t.compare(changed)[1] is True, "A changed history must not be guessed to be an edit"
    t.commit(changed)
    reset = [node("e", "收更改"), node("f", "same"), node("g", "same")]
    assert t.compare(reset)[0] == [] and t.compare(reset)[2] == "textOverlap"
    assert t.compare([node("z", "different")])[1] is True
    t.commit([node("a", "old"), node("b", "anchor")])
    recycled = [node("a", "anchor"), node("b", "new")]
    assert t.compare(recycled) == ([("new", recycled[1])], False, "textOverlap")
    assert t.compare([node("a", "other"), node("b", "different")])[1] is True
    assert overlap(["x", "x"], ["x", "x", "new"]) == 2
    assert "usdt" in "出USDT".casefold()
    print("CHAT_MONITOR_SELF_TEST_OK baseline, duplicate text, recycled IDs, changed history and gap detection")


class Experiment:
    def __init__(self, config, token):
        self.c = config
        self.token = token
        self.out = Path(config["output"]).resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "snapshots").mkdir()
        (self.out / "screenshots").mkdir()
        self.files = {name: (self.out / (name + ".jsonl")).open("x", encoding="utf-8")
            for name in ("polls", "events", "captures", "progress", "errors", "benchmarks")}
        self.screenshots = 0
        self.screenshot_bytes = 0
        self.rows = []
        self.event_count = self.new_count = self.updated_count = self.hits = self.gaps = self.errors = 0
        self.shot_lags = []
        self.start_gaps = []
        self.read_times = []
        self.loop_times = []
        self.traversal_times = []
        self.match_times = []
        self.partial_polls = 0
        self.identity_modes = {}

    def write(self, name, value):
        f = self.files[name]
        f.write(json.dumps(value, ensure_ascii=False) + "\n")
        f.flush()

    def save(self, name, value):
        with (self.out / name).open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)

    def cli(self, args, leased=True, json_result=True, timeout=45):
        prefix = [self.c["anode"], "--channel", "main", "--agent", self.c["agent"]]
        if leased:
            prefix += ["--lease", self.token]
        try:
            result = subprocess.run(prefix + args, capture_output=True, text=True, encoding="utf-8",
                timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError("CLI timeout during " + args[0]) from None
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).replace(self.token, "[lease]")
            raise RuntimeError(args[0] + " failed: " + detail[:4000])
        return json.loads(result.stdout) if json_result else result.stdout

    def ready(self):
        state = self.cli(["status", "--json"], leased=False)
        if state.get("state") != "ready" or state.get("session") != self.c["session"] or not state.get("viewOnly"):
            raise RuntimeError("Seat changed, stopped, or human control was taken")
        if state.get("lease", {}).get("ownerAgentId") != self.c["agent"]:
            raise RuntimeError("Experiment no longer owns the desktop lease")
        return state

    def read(self, reader, tail, limit, budget):
        start = time.perf_counter()
        arguments = ["exec", "--cwd", self.c["root"], "--timeout", "30000", "--wait", "10000", "--json", "--",
            reader, "--channel", "main", "__chat-read", "--pid", str(self.c["pid"]),
            "--window-title", self.c["windowTitle"], "--list-name", self.c["listName"],
            "--max-elements", str(limit), "--max-chars", "20000", "--budget-ms", str(budget)]
        if tail:
            arguments += ["--tail"]
        job = self.cli(arguments)
        stdout, stderr = job.get("stdout", ""), job.get("stderr", "")
        truncated = job.get("outputTruncated", False)
        while not job.get("finished") or job.get("hasMoreOutput"):
            job = self.cli(["job", job["jobId"], "--after", job["cursor"], "--wait", "10000", "--max-chars", "20000", "--json"], leased=False)
            stdout += job.get("stdout", "")
            stderr += job.get("stderr", "")
            truncated |= job.get("outputTruncated", False)
            if time.perf_counter() - start > 40:
                self.cli(["job", job["jobId"], "--cancel", "--json"], leased=False)
                raise RuntimeError("Owned reader job exceeded experiment deadline")
        if job.get("exitCode") != 0 or truncated:
            raise RuntimeError("Reader failed or job output was clipped: " + stderr[:3000])
        payload = json.loads(stdout.lstrip("\ufeff"))
        if payload.get("session") != self.c["session"] or payload.get("windowTitle") != self.c["windowTitle"]:
            raise RuntimeError("Reader returned the wrong session/window")
        return payload, (time.perf_counter() - start)*1000, job.get("elapsedMs")

    def capture(self, cycle, detected_at, detected_mono):
        if self.screenshots >= 100 or self.screenshot_bytes >= 100*1024*1024:
            self.write("captures", {"cycle": cycle, "atUtc": utc(), "skipped": "experimentScreenshotBudget"})
            return None
        path = self.out / "screenshots" / (f"{cycle:04d}.png")
        requested = utc()
        self.cli(["shot", str(path), "--width", "1920"], json_result=False)
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Screenshot was not a PNG")
        width, height = struct.unpack("!II", data[16:24])
        self.screenshots += 1
        self.screenshot_bytes += len(data)
        lag = (time.perf_counter()-detected_mono)*1000
        self.shot_lags.append(lag)
        record = {"cycle": cycle, "detectedAtUtc": detected_at, "requestedAtUtc": requested,
            "completedAtUtc": utc(), "path": str(path), "width": width, "height": height,
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "detectionToCaptureMs": lag}
        self.write("captures", record)
        return record

    def run(self):
        self.save("config.json", {**self.c, "leaseTokenStored": False})
        self.ready()
        for i in range(self.c.get("baselineSamples", 3)):
            payload, wall, worker = self.read(self.c["baselineReader"], False, 500, 8000)
            self.save(f"snapshots/baseline-{i:02d}.json", payload)
            self.write("benchmarks", {"mode": "originalFull", "atUtc": utc(), "wallMs": wall,
                "workerMs": worker, "traversalMs": payload["elapsedMs"], "nodes": len(payload["nodes"]), "truncated": payload["truncated"]})
        seed, seed_wall, _ = self.read(self.c["reader"], True, self.c["tailCount"], 8000)
        if not seed.get("windowComplete"):
            raise RuntimeError("Initial tail window did not complete")
        self.save("snapshots/seed.json", seed)
        tracker = Tracker()
        tracker.commit(items(seed))
        started_utc, started = utc(), time.perf_counter()
        previous_start = None
        cycle = 0
        failures = 0
        stop_reason = "durationComplete"
        self.write("progress", {"state": "monitoring", "startedAtUtc": started_utc, "baselineItems": len(items(seed)), "seedWallMs": seed_wall})
        print(json.dumps({"state": "monitoring", "startedAtUtc": started_utc, "output": str(self.out)}, ensure_ascii=False), flush=True)
        while time.perf_counter()-started < self.c["durationSeconds"]:
            if (self.out / "stop.flag").exists():
                stop_reason = "requestedStop"
                break
            cycle += 1
            cycle_start, cycle_utc = time.perf_counter(), utc()
            if previous_start is not None:
                self.start_gaps.append((cycle_start-previous_start)*1000)
            previous_start = cycle_start
            try:
                self.ready()
                payload, wall, worker = self.read(self.c["reader"], True, self.c["tailCount"], 8000)
                self.save(f"snapshots/{cycle:04d}.json", payload)
                current = items(payload)
                changes, gap, mode = tracker.compare(current)
                catchup_ms = 0
                if gap:
                    larger, catchup_ms, _ = self.read(self.c["reader"], True, 200, 20000)
                    self.save(f"snapshots/{cycle:04d}-catchup.json", larger)
                    current = items(larger)
                    changes, gap, mode = tracker.compare(current)
                    payload = larger
                self.gaps += int(gap)
                self.identity_modes[mode] = self.identity_modes.get(mode, 0)+1
                partial = not payload.get("windowComplete") or any(not n.get("textInfo", {}).get("complete") for n in current)
                self.partial_polls += int(partial)
                detected_mono, detected_utc = time.perf_counter(), utc()
                match_start = time.perf_counter()
                batch_hits = 0
                for kind, node in changes:
                    text = node.get("text", "")
                    keywords = [word for word in self.c["keywords"] if word.casefold() in text.casefold()]
                    batch_hits += int(bool(keywords))
                    self.event_count += 1
                    self.new_count += int(kind == "new")
                    self.updated_count += int(kind == "updated")
                    self.hits += int(bool(keywords))
                    self.write("events", {"cycle": cycle, "detectedAtUtc": detected_utc, "kind": kind,
                        "identityMode": mode, "coverageGap": gap, "partial": partial,
                        "matchedKeywords": keywords, "source": "UIA.Name", "elementKey": node.get("elementKey"),
                        "textInfo": node.get("textInfo"), "textSha256": digest(text), "text": text})
                match_ms = (time.perf_counter()-match_start)*1000
                self.match_times.append(match_ms)
                capture = self.capture(cycle, detected_utc, detected_mono) if changes else None
                tracker.commit(current)
                loop_ms = (time.perf_counter()-cycle_start)*1000
                poll = {"cycle": cycle, "startedAtUtc": cycle_utc, "readCompletedAtUtc": detected_utc,
                    "wallReadMs": wall, "workerMs": worker, "traversalMs": payload["elapsedMs"],
                    "catchupMs": catchup_ms, "matchAndLogMs": match_ms, "cycleMs": loop_ms,
                    "nodes": len(current), "newTextItems": len(changes), "keywordHits": batch_hits,
                    "windowComplete": payload.get("windowComplete"), "olderItemsOmitted": payload.get("olderItemsOmitted"),
                    "partial": partial, "coverageGap": gap, "identityMode": mode,
                    "screenshot": capture["path"] if capture else None}
                self.write("polls", poll)
                self.rows.append(poll)
                self.read_times.append(wall)
                self.traversal_times.append(payload["elapsedMs"])
                self.loop_times.append(loop_ms)
                failures = 0
            except Exception as error:
                self.errors += 1
                failures += 1
                self.write("errors", {"cycle": cycle, "atUtc": utc(), "error": str(error).replace(self.token, "[lease]")})
                if failures >= 3:
                    stop_reason = "threeConsecutiveErrors"
                    break
                time.sleep(1)
            self.write("progress", {"cycle": cycle, "atUtc": utc(), "elapsedSeconds": time.perf_counter()-started,
                "successfulPolls": len(self.rows), "events": self.event_count, "newItems": self.new_count,
                "keywordHits": self.hits, "screenshots": self.screenshots, "errors": self.errors})
        duration = time.perf_counter()-started
        result = {"status": "CHAT_MONITOR_EXPERIMENT_FINISHED", "stopReason": stop_reason,
            "startedAtUtc": started_utc, "endedAtUtc": utc(), "durationSeconds": duration,
            "cyclesAttempted": cycle, "successfulPolls": len(self.rows),
            "pollsPerSecond": len(self.rows)/duration, "pollsPerMinute": len(self.rows)/duration*60,
            "cycleStartIntervalMs": metrics(self.start_gaps), "cycleWorkMs": metrics(self.loop_times),
            "wallReadMs": metrics(self.read_times), "traversalMs": metrics(self.traversal_times),
            "matchAndLogMs": metrics(self.match_times), "detectionToScreenshotMs": metrics(self.shot_lags),
            "newItems": self.new_count, "updatedItems": self.updated_count, "loggedEvents": self.event_count,
            "keywordHitItems": self.hits, "screenshots": self.screenshots, "screenshotBytes": self.screenshot_bytes,
            "coverageGaps": self.gaps, "partialPolls": self.partial_polls, "errors": self.errors,
            "identityModes": self.identity_modes, "sourceArrivalLatencyMeasured": False,
            "chatMessagesSent": 0, "llmCallsInMonitoringLoop": 0}
        self.save("summary.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    def close(self):
        try:
            self.cli(["lease", "release"], json_result=False)
        except Exception as error:
            self.write("errors", {"atUtc": utc(), "stage": "release", "error": str(error).replace(self.token, "[lease]")})
        for f in self.files.values():
            f.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.config:
        parser.error("--config is required")
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    token = json.loads(sys.stdin.readline())["leaseToken"]
    experiment = Experiment(config, token)
    try:
        experiment.run()
    finally:
        experiment.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
