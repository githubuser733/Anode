"""Resident, local-only keyword monitor. A lease token is supplied on stdin, never saved.

All durable data is append-only. Old journals, partial crash records and screenshots
are retained. Notifications run in the parent; UIA and capture remain in Anode's child.
"""
import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
import uuid


class ConfigurationError(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def append(path, value, durable=True):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=True) + "\n")
        handle.flush()
        if durable:
            os.fsync(handle.fileno())


def nodes(frame):
    return [n for n in frame.get("nodes", []) if n.get("role") == "ListItem"]


def changes(previous, current, expanded=False):
    before = [n.get("text", "") for n in previous]
    after = [n.get("text", "") for n in current]
    if not previous:
        return [], False  # A first observation is a baseline, never an old-history alert.
    for size in range(min(len(before), len(after)), 0, -1):
        ends = range(len(after), size-1, -1) if expanded else (size,)
        for end in ends:
            if before[-size:] == after[end-size:end]:
                return current[end:], False
    return [], bool(current)


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.previous, self.pending = [], {}
        self.initialized = False
        self.source, self.sequence = None, -1
        self.recovered_partial_records = 0
        self.owner = (self.directory / "owner.lock").open("a+b")
        if self.owner.tell() == 0:
            self.owner.write(b"\0")
            self.owner.flush()
        self.owner.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            raise RuntimeError("This monitor supports Windows only")
        files = sorted(self.directory.glob("journal-*.jsonl"))
        for path in files:
            with path.open("rb") as handle:
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        self.recovered_partial_records += 1
                        continue  # Preserve the interrupted tail in its original file.
                    self.apply(json.loads(raw))
        ordinal = max([int(p.stem.split("-")[-1]) for p in files] or [0])+1
        self.path = self.directory / f"journal-{ordinal:06d}.jsonl"
        self.file = self.path.open("x", encoding="utf-8")
        self.record({"kind": "opened", "atUtc": utc(), "recoveredPartialRecords": self.recovered_partial_records})

    def apply(self, record):
        if record["kind"] == "batch":
            self.initialized = True
            self.previous = record["nodes"]
            self.source, self.sequence = record["source"], record["sequence"]
            for action in record["actions"]:
                self.pending[action["id"]] = {**action, "channels": list(action["channels"])}
        elif record["kind"] == "action":
            action = record["action"]
            self.pending[action["id"]] = {**action, "channels": list(action["channels"])}
        elif record["kind"] == "receipt":
            pending = self.pending.get(record["id"])
            if pending and record["channel"] in pending["channels"]:
                pending["channels"].remove(record["channel"])
                if not pending["channels"]:
                    del self.pending[record["id"]]

    def record(self, value):
        with self.lock:
            self.file.write(json.dumps(value, ensure_ascii=True) + "\n")
            self.file.flush()
            os.fsync(self.file.fileno())
            self.apply(value)

    def close(self):
        self.file.close()
        self.owner.close()  # Releases the OS lock; the lock file is retained.


class BinaryLines:
    def __init__(self, path):
        self.path, self.offset, self.pending = Path(path), 0, b""

    def read(self):
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
            self.offset = handle.tell()
        parts = (self.pending+data).split(b"\n")
        self.pending = parts.pop()
        return [json.loads(p) for p in parts if p.strip()]


class ProcessWait:
    """Read-only wait handle for an owned exec worker; pinned across PID reuse."""
    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only.
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def exited(self):
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 258):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return result == 0

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Monitor:
    def __init__(self, config, token):
        self.c, self.token = config, token
        self.out = Path(config["output"])
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "screenshots").mkdir()
        self.journal = Journal(config["stateDirectory"])
        self.stop = threading.Event()
        self.queues = {name: queue.Queue() for name in ("notify", "capture")}
        self.threads = []
        self.helper = None
        self.job = None
        self.worker_wait = None
        self.polls, self.events, self.hits = 0, 0, 0
        self.read_times, self.intervals, self.receive_lags = (deque(maxlen=10000) for _ in range(3))
        self.last_read_start = None
        self.last_sequence = 0
        self.pending_catchup = False
        self.health_state = None
        self.captures = self.capture_bytes = 0
        self.stopped_reason = "durationComplete"

    def cli(self, args, leased=False, parse=True):
        prefix = [self.c["anode"], "--channel", "main", "--agent", self.c["agent"]]
        if leased:
            prefix += ["--lease", self.token]
        try:
            result = subprocess.run(prefix+args, capture_output=True, encoding="utf-8", text=True,
                timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError("CLI timeout: "+args[0]) from None
        decoded = None
        if parse:
            try:
                decoded = json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        if result.returncode and args[0] in ("exec", "job") and isinstance(decoded, dict) and "jobId" in decoded:
            return decoded  # The CLI returns the child exit code; this is still a valid job-status response.
        if result.returncode:
            error = (args[0]+": "+(result.stderr or result.stdout)).replace(self.token, "[lease]")[:3000]
            raise (ConfigurationError if result.returncode == 2 else RuntimeError)(error)
        return decoded if parse and decoded is not None else json.loads(result.stdout) if parse else result.stdout

    def renew(self):
        self.cli(["lease", "renew", "--ttl", "120"], leased=True, parse=False)

    def release(self):
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            state = self.cli(["status", "--json"])
            lease = state.get("lease", {})
            if lease.get("ownerAgentId") != self.c["agent"]:
                return  # The seat ended or ownership already moved; do not touch another owner's lease.
            if lease.get("operationRunning"):
                time.sleep(.1)
                continue
            try:
                self.cli(["lease", "release"], leased=True, parse=False)
                return
            except RuntimeError as error:
                if "in-flight desktop operation" not in str(error):
                    raise
                time.sleep(.1)
        raise RuntimeError("Own desktop operations did not drain before lease release")

    def ready(self):
        state = self.cli(["status", "--json"])
        if state.get("state") != "ready" or state.get("session") != self.c["session"]:
            raise RuntimeError("Verified child session unavailable")
        if not state.get("viewOnly"):
            self.stopped_reason = "humanControl"
            self.stop.set()
            raise RuntimeError("Human control: monitor paused")
        if state.get("lease", {}).get("ownerAgentId") != self.c["agent"]:
            self.stopped_reason = "leaseLost"
            self.stop.set()
            raise RuntimeError("Desktop lease changed")
        return state

    def enqueue(self, action):
        for channel in action["channels"]:
            self.queues[channel].put(action)

    def health(self, state, message):
        if state == self.health_state:
            return
        self.health_state = state
        action = {"id": uuid.uuid4().hex, "title": "Anode 监控状态", "text": message,
            "atUtc": utc(), "channels": ["notify"]}
        self.journal.record({"kind": "action", "atUtc": utc(), "health": state, "action": action})
        self.enqueue(action)

    def start_helper(self):
        identifier = uuid.uuid4().hex
        self.inbox = self.out / ("notify-"+identifier+".jsonl")
        self.receipts = self.out / ("notify-"+identifier+"-receipts.jsonl")
        self.inbox.open("x", encoding="utf-8").close()
        self.receipt_reader = BinaryLines(self.receipts)
        self.helper = subprocess.Popen([self.c["reader"], "--channel", "main", "__chat-notify",
            "--inbox", str(self.inbox), "--receipts", str(self.receipts),
            "--parent-session", str(self.c["parentSession"]), "--owner-pid", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=(self.out / ("notify-"+identifier+".stderr")).open("x"),
            creationflags=subprocess.CREATE_NO_WINDOW)

    def notify(self, action):
        if self.helper is None or self.helper.poll() is not None:
            self.start_helper()
        append(self.inbox, {k: action[k] for k in ("id", "title", "text")})
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            for receipt in self.receipt_reader.read():
                self.journal.record({"kind": "notificationEvent", **receipt})
                if receipt.get("id") == action["id"]:
                    if receipt["state"] == "requested":
                        return {"state": "requested", "atUtc": receipt["atUtc"]}
                    if receipt["state"] == "failed":
                        raise RuntimeError("Desktop notification failed: "+str(receipt.get("error")))
            if self.helper.poll() is not None:
                raise RuntimeError("Notification helper stopped; inspect its retained stderr")
            time.sleep(.05)
        with Path(str(self.inbox)+".stop").open("x"):
            pass
        self.helper.wait(timeout=3)
        raise RuntimeError("Desktop notification request was not acknowledged")

    def capture(self, action):
        if self.captures >= self.c.get("maxScreenshots", 1000) or self.capture_bytes >= self.c.get("maxScreenshotBytes", 1024**3):
            return {"state": "skippedCaptureBudget", "atUtc": utc()}
        path = self.out / "screenshots" / (action["id"]+"-"+uuid.uuid4().hex+".png")
        self.cli(["shot", str(path), "--width", "1920"], leased=True, parse=False)
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Invalid screenshot; original file retained")
        width, height = struct.unpack("!II", data[16:24])
        self.captures += 1
        self.capture_bytes += len(data)
        return {"state": "saved", "atUtc": utc(), "path": str(path), "width": width, "height": height,
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def action_worker(self, channel):
        while not self.stop.is_set():
            try:
                action = self.queues[channel].get(timeout=.2)
            except queue.Empty:
                continue
            attempt = 0
            while not self.stop.is_set():
                try:
                    result = self.notify(action) if channel == "notify" else self.capture(action)
                    self.journal.record({"kind": "receipt", "id": action["id"], "channel": channel, **result})
                    break
                except Exception as error:
                    attempt += 1
                    self.journal.record({"kind": "actionError", "id": action["id"], "channel": channel,
                        "atUtc": utc(), "attempt": attempt, "error": str(error).replace(self.token, "[lease]")})
                    self.stop.wait(min(2**min(attempt, 5), 30))

    def start_reader(self):
        if self.c.get("processName"):
            windows = self.cli(["windows", "--query", self.c["windowTitle"], "--json"], leased=True)["windows"]
            matches = [w for w in windows if w.get("title") == self.c["windowTitle"]
                and w.get("process", "").casefold() == self.c["processName"].casefold()]
            if len(matches) != 1:
                raise RuntimeError("The monitored window is absent or ambiguous")
            self.c["pid"] = matches[0]["pid"]
        self.segment = uuid.uuid4().hex
        self.stream = self.out / ("frames-"+self.segment+".jsonl")
        lifetime = self.c.get("segmentMs", 300000)
        arguments = [self.c["reader"], "--channel", "main", "__chat-read", "--pid", str(self.c["pid"]),
            "--window-title", self.c["windowTitle"], "--list-name", self.c["listName"],
            "--max-elements", str(self.c.get("tailCount", 6)), "--max-chars", "20000",
            "--watch-ms", str(lifetime), "--interval-ms", str(self.c.get("intervalMs", 250)), "--stream", str(self.stream)]
        # An uncertain launch is never replayed. The run's agent and job list retain recovery identity.
        before = {item["jobId"] for item in self.cli(["jobs", "--json"])["jobs"]}
        try:
            self.job = self.cli(["exec", "--cwd", self.c["root"], "--timeout", str(lifetime+30000),
                "--wait", "1000", "--json", "--"]+arguments, leased=True)
        except Exception:
            try:
                recovered = [item for item in self.cli(["jobs", "--json"])["jobs"] if item["jobId"] not in before]
            except Exception:
                self.stopped_reason = "readerLaunchUncertain"
                self.stop.set()
                raise RuntimeError("Reader launch and recovery query were uncertain; not replayed.") from None
            if len(recovered) != 1:
                self.stopped_reason = "readerLaunchUncertain"
                self.stop.set()
                raise RuntimeError("Reader launch was uncertain; not replayed. Inspect this agent's jobs.") from None
            self.job = {**recovered[0], "cursor": "0"}
        self.journal.record({"kind": "readerStarted", "segment": self.segment, "jobId": self.job["jobId"], "atUtc": utc()})
        if self.job.get("finished") and self.job.get("exitCode") != 0:
            raise RuntimeError("Reader exited during startup: "+self.job.get("stderr", ""))
        if not self.job.get("workerPid"):
            self.job.update(self.cli(["job", self.job["jobId"], "--wait", "0", "--json"]))
        try:
            self.worker_wait = ProcessWait(self.job["workerPid"])
            self.journal.record({"kind": "workerWaitReady", "workerPid": self.job["workerPid"], "atUtc": utc()})
        except (OSError, KeyError) as error:
            self.journal.record({"kind": "workerWaitUnavailable", "error": str(error), "atUtc": utc()})
        self.reader = BinaryLines(self.stream)
        self.last_frame_mono = time.monotonic()
        self.last_read_start = None
        self.last_sequence = 0
        self.pending_catchup = False

    def stop_reader(self):
        if self.job is None:
            if self.worker_wait:
                self.worker_wait.close()
                self.worker_wait = None
            return
        path = Path(str(self.stream)+".stop")
        if not path.exists():
            path.open("x").close()
        self.cli(["job", self.job["jobId"], "--cancel", "--json"])
        self.job = None
        if self.worker_wait:
            self.worker_wait.close()
            self.worker_wait = None

    def accept(self, frame):
        if frame.get("kind") == "error":
            raise RuntimeError("Reader reported: "+str(frame.get("error")))
        if frame.get("kind") == "stopped":
            self.job = None
            if self.worker_wait:
                self.worker_wait.close()
                self.worker_wait = None
            return
        if frame.get("session") != self.c["session"] or frame.get("windowTitle") != self.c["windowTitle"] or frame.get("processId") != self.c["pid"]:
            raise RuntimeError("Reader identity changed")
        sequence = frame["sequence"]
        if self.segment == self.journal.source and sequence <= self.journal.sequence:
            self.last_sequence = max(self.last_sequence, sequence)
            self.last_frame_mono = time.monotonic()
            return
        if sequence != self.last_sequence+1:
            self.health("gap", "采集序列出现缺口，正在补读，请检查日志")
        self.last_sequence = sequence
        self.last_frame_mono = time.monotonic()
        self.polls += 1
        self.read_times.append(frame["elapsedMs"])
        if self.last_read_start is not None:
            self.intervals.append(frame["readStartedMs"]-self.last_read_start)
        self.last_read_start = frame["readStartedMs"]
        received = utc()
        lag = (datetime.fromisoformat(received)-datetime.fromisoformat(frame["observedAtUtc"])).total_seconds()*1000
        self.receive_lags.append(lag)
        append(self.out / "polls.jsonl", {"sequence": sequence, "segment": self.segment,
            "observedAtUtc": frame["observedAtUtc"], "receivedAtUtc": received,
            "readMs": frame["elapsedMs"], "receiveLagMs": lag}, durable=False)
        if frame["kind"] != "snapshot":
            return
        current = nodes(frame)
        if self.journal.initialized and self.journal.previous and not current and frame.get("catchup"):
            raise RuntimeError("The exposed message window became empty; preserving the previous checkpoint")
        if not frame.get("windowComplete") or any(not n.get("textInfo", {}).get("complete") for n in current):
            self.health("partial", "读取窗口或字段不完整，正在补读；不能保证关键词完整")
            gap = True
            new = []
        else:
            if not self.journal.initialized:
                new, gap = [], False
            elif not self.journal.previous:
                new, gap = current, False
            elif not current:
                new, gap = [], True
            else:
                new, gap = changes(self.journal.previous, current, expanded=frame.get("catchup", False))
        if gap and not frame.get("catchup"):
            if not self.pending_catchup:
                with Path(str(self.stream)+".requests").open("a", encoding="ascii") as handle:
                    handle.write("catchup\n")
                    handle.flush()
                self.pending_catchup = True
            return
        if gap:
            self.health("gap", "扩大读取后仍无连续锚点；可能有漏消息，保留现有文本供核查")
            new = []  # Ambiguous history must not be turned into old-message alerts.
        else:
            self.health("running", "监控正在运行："+self.c["windowTitle"])
        self.pending_catchup = False
        events, hits = [], []
        for index, node in enumerate(new):
            text = node.get("text", "")
            keywords = [w for w in self.c["keywords"] if w.casefold() in text.casefold()]
            event = {"id": f"{self.segment}:{sequence}:{index}", "text": text, "textInfo": node.get("textInfo"),
                "keywords": keywords, "observedAtUtc": frame["observedAtUtc"], "recordedAtUtc": utc()}
            events.append(event)
            if keywords:
                hits.append(event)
        actions = []
        if hits:
            actions.append({"id": uuid.uuid4().hex, "title": self.c["windowTitle"]+f"：{len(hits)} 条命中",
                "text": "["+", ".join(hits[-1]["keywords"])+"] "+hits[-1]["text"][:200],
                "atUtc": utc(), "eventIds": [h["id"] for h in hits],
                "channels": ["notify", "capture"]})
        self.journal.record({"kind": "batch", "atUtc": utc(), "source": self.segment, "sequence": sequence,
            "nodes": current, "events": events, "actions": actions, "coverageGap": gap})
        self.events += len(events)
        self.hits += len(hits)
        for action in actions:
            self.enqueue(action)

    def run(self):
        started = time.monotonic()
        state = self.ready()
        if state["parentSession"] != self.c["parentSession"]:
            raise RuntimeError("Parent session changed")
        for channel in self.queues:
            thread = threading.Thread(target=self.action_worker, args=(channel,), daemon=True)
            self.threads.append(thread)
            thread.start()
        for action in list(self.journal.pending.values()):
            self.enqueue(action)
        next_status = next_renew = next_progress = 0
        failures = 0
        while not self.stop.is_set():
            if (self.out / "stop.flag").exists():
                self.stopped_reason = "requestedStop"
                break
            if self.c.get("durationSeconds", 0) and time.monotonic()-started >= self.c["durationSeconds"]:
                break
            try:
                now = time.monotonic()
                if now >= next_status:
                    self.ready()
                    next_status = now+2
                if now >= next_renew:
                    self.renew()
                    next_renew = now+30
                if self.job is None:
                    self.start_reader()
                for frame in self.reader.read():
                    self.accept(frame)
                if self.job is not None and self.worker_wait is not None and self.worker_wait.exited():
                    raise RuntimeError("Owned reader process exited; recovering from the last committed text window")
                if self.job is not None and now-self.last_frame_mono > 12:
                    status = self.cli(["job", self.job["jobId"], "--after", self.job["cursor"], "--wait", "0", "--json"])
                    raise RuntimeError("Reader heartbeat expired: "+status.get("state", "unknown")+" "+status.get("stderr", ""))
                failures = 0
            except Exception as error:
                if isinstance(error, ConfigurationError):
                    self.stopped_reason = "configurationError"
                    self.stop.set()
                self.journal.record({"kind": "readerError", "atUtc": utc(), "error": str(error).replace(self.token, "[lease]")})
                self.health("unavailable", "监控暂不可用，正在等待恢复；请检查微信和 Anode")
                try:
                    self.stop_reader()
                except Exception as cleanup_error:
                    self.journal.record({"kind": "cleanupError", "atUtc": utc(), "error": str(cleanup_error).replace(self.token, "[lease]")})
                failures += 1
                self.stop.wait(min(2**min(failures, 5), 30))
            if time.monotonic() >= next_progress:
                append(self.out / "progress.jsonl", {"atUtc": utc(), "polls": self.polls,
                    "events": self.events, "hits": self.hits, "pendingActions": len(self.journal.pending)}, durable=False)
                next_progress = time.monotonic()+1
            self.stop.wait(.03)
        return {"durationSeconds": time.monotonic()-started, "polls": self.polls, "events": self.events, "hits": self.hits,
            "stopReason": self.stopped_reason, "captures": self.captures,
            "readMs": summary(self.read_times), "intervalMs": summary(self.intervals),
            "receiveLagMs": summary(self.receive_lags), "pendingActions": len(self.journal.pending),
            "statisticsWindow": 10000,
            "realMessageArrivalLatencyMeasured": False}

    def close(self):
        self.stop.set()
        try:
            self.stop_reader()
        finally:
            for thread in self.threads:
                thread.join()
            if self.helper and self.helper.poll() is None:
                Path(str(self.inbox)+".stop").open("x").close()
                self.helper.wait(timeout=5)
            self.release()
            self.journal.close()


def summary(values):
    if not values:
        return None
    values = sorted(values)
    return {"count": len(values), "mean": sum(values)/len(values), "p95": values[max(0, (95*len(values)+99)//100-1)],
        "min": values[0], "max": values[-1]}


def self_test(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    n = lambda text: {"role": "ListItem", "text": text}
    before = [n("old"), n("anchor")]
    assert changes(before, [n("anchor"), n("same"), n("same")])[0] == [n("same"), n("same")]
    assert changes(before, [n("earlier"), *before, n("new")], expanded=True) == ([n("new")], False)
    assert changes(before, [n("different")])[1]
    assert changes([], before) == ([], False)
    journal = Journal(out / "state")
    action = {"id": "action1", "channels": ["notify", "capture"], "title": "test", "text": "test"}
    journal.record({"kind": "batch", "source": "s", "sequence": 1, "nodes": before, "events": [], "actions": [action]})
    journal.record({"kind": "receipt", "id": "action1", "channel": "notify"})
    old = journal.path
    journal.close()
    child = subprocess.Popen([sys.executable, "-B", "-c", "import sys; sys.stdin.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    waited = ProcessWait(child.pid)
    assert not waited.exited()
    child.stdin.write(b"x")
    child.stdin.close()
    child.wait(timeout=5)
    assert waited.exited()
    waited.close()
    with old.open("ab") as handle:
        handle.write(b'{"interrupted":')
    original = old.read_bytes()
    journal = Journal(out / "state")
    assert journal.previous == before and journal.pending["action1"]["channels"] == ["capture"]
    assert journal.recovered_partial_records == 1 and old.read_bytes() == original
    journal.record({"kind": "receipt", "id": "action1", "channel": "capture"})
    journal.close()
    journal = Journal(out / "state")
    assert journal.pending == {} and journal.previous == before
    journal.close()
    path = out / "frames.jsonl"
    path.write_bytes(b'{"a":')
    reader = BinaryLines(path)
    assert reader.read() == []
    with path.open("ab") as handle:
        handle.write(b'1}\n')
    assert reader.read() == [{"a": 1}]
    monitor = Monitor({"output": str(out / "logic"), "stateDirectory": str(out / "logic-state"),
        "session": 6, "pid": 1, "windowTitle": "test", "keywords": ["收"], "anode": "unused", "agent": "test",
        "root": str(out), "reader": "unused", "listName": "消息"}, "test-token")
    commands = []
    monitor.cli = lambda args, **kwargs: commands.append((args, kwargs))
    monitor.renew()
    assert commands == [(["lease", "renew", "--ttl", "120"], {"leased": True, "parse": False})]
    monitor.segment = "test"
    monitor.stream = out / "logic" / "stream"
    rich = lambda text: {**n(text), "textInfo": {"complete": True}}
    old_nodes = [rich("old"), rich("anchor")]
    monitor.journal.record({"kind": "batch", "source": "before", "sequence": 1, "nodes": old_nodes, "events": [], "actions": []})
    base = {"kind": "snapshot", "session": 6, "processId": 1, "windowTitle": "test", "elapsedMs": 1,
        "windowComplete": True, "observedAtUtc": utc(), "readStartedMs": 0}
    new_nodes = [rich(f"收 {i}") for i in range(20)]
    monitor.accept({**base, "sequence": 1, "nodes": new_nodes[-6:]})
    assert monitor.pending_catchup and monitor.journal.previous == old_nodes
    monitor.accept({**base, "sequence": 2, "catchup": True, "nodes": [rich("earlier"), *old_nodes, *new_nodes]})
    assert monitor.events == 20 and monitor.hits == 20
    assert len([p for p in monitor.journal.pending.values() if "capture" in p["channels"]]) == 1
    monitor.accept({**base, "sequence": 2, "catchup": True, "nodes": [*old_nodes, *new_nodes]})
    assert monitor.events == 20, "A replayed committed source sequence must not notify twice"
    previous = list(monitor.journal.previous)
    monitor.accept({**base, "sequence": 3, "nodes": []})
    assert monitor.journal.previous == previous and monitor.pending_catchup
    monitor.journal.close()
    empty = Monitor({**monitor.c, "output": str(out / "empty"), "stateDirectory": str(out / "empty-state")}, "test-token")
    empty.segment = "empty-test"
    empty.stream = out / "empty" / "stream"
    empty.accept({**base, "sequence": 1, "nodes": []})
    empty.accept({**base, "sequence": 2, "nodes": [rich("收 first message")]})
    assert empty.events == 1 and empty.hits == 1, "The first arrival after an empty baseline must alert"
    empty.journal.close()
    from unittest.mock import patch
    from types import SimpleNamespace
    status = {"jobId": "owned", "finished": True, "exitCode": 1, "state": "completed"}
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=1, stdout=json.dumps(status), stderr="")):
        assert Monitor.cli(monitor, ["job", "owned", "--cancel", "--json"]) == status
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=2, stdout="", stderr="Unknown option")):
        try:
            Monitor.cli(monitor, ["lease", "renew", "--bad-option"])
            raise AssertionError("Unsupported configuration must not retry as a transient failure")
        except ConfigurationError:
            pass
    release_states = iter([{"lease": {"ownerAgentId": "test", "operationRunning": True}},
        {"lease": {"ownerAgentId": "test", "operationRunning": False}}])
    release_calls = []
    monitor.cli = lambda args, **kwargs: next(release_states) if args[0] == "status" else release_calls.append(args)
    monitor.release()
    assert release_calls == [["lease", "release"]]
    launch_queries = []
    def uncertain(args, **kwargs):
        if args[0] == "jobs":
            launch_queries.append(args)
            if len(launch_queries) == 1:
                return {"jobs": []}
        raise RuntimeError("simulated lost response")
    monitor.cli = uncertain
    try:
        monitor.start_reader()
        raise AssertionError("An uncertain launch must not be replayed")
    except RuntimeError:
        assert monitor.stop.is_set() and monitor.stopped_reason == "readerLaunchUncertain"
    print("RESIDENT_MONITOR_SELF_TEST_OK overlap, duplicates, expanded catchup, durable outbox and retained crash tails")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.self_test:
        if not args.out:
            parser.error("--self-test requires a new absolute --out directory")
        self_test(args.out)
        return
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    token = json.loads(sys.stdin.readline())["leaseToken"]
    monitor = Monitor(config, token)
    try:
        result = monitor.run()
    finally:
        monitor.close()
    result["shutdownCompleted"] = True
    result["pendingActions"] = len(monitor.journal.pending)
    append(monitor.out / "summary.jsonl", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
