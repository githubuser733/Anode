"""Persistent Alpha read-only keyword monitor. No model, keyboard or WeChat sends."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from core import ROOT, LOCAL_ZONE, configuration, digest, matches, save_new, unique_ordered
from clients import AlphaReader
from formatting import md_text
from native_delivery import dispatch_lock
import business_rules


def now_ms():
    return int(time.time() * 1000)


def directories(config):
    paths = {k: (ROOT / config["resident"][k]).resolve() for k in ("stateDirectory", "runsDirectory", "reportsDirectory")}
    for path in paths.values():
        if not path.is_relative_to((ROOT / "artifacts").resolve()):
            raise ValueError("RESIDENT_PATH_OUTSIDE_PROJECT_ARTIFACTS")
    return paths


def append(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at_ms": now_ms(), **data}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def last_row(path):
    if not path.exists():
        return None
    # Only the last bounded tail is needed; do not reread a growing history each poll.
    with path.open("rb") as stream:
        stream.seek(0, 2)
        length = stream.tell()
        stream.seek(max(0, length - 65536))
        lines = stream.read().splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)
        except (ValueError, UnicodeError):
            continue
    return None


def enabled(config):
    row = last_row(directories(config)["stateDirectory"] / "control.jsonl")
    return bool(row and row.get("enabled"))


def control(config, value):
    append(directories(config)["stateDirectory"] / "control.jsonl", {"enabled": value, "reason": "explicit local control"})
    return {"enabled": value}


class Store:
    """Transactional progress + dedup + notification outbox. No destructive SQL."""
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "monitor.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS checkpoints(seq INTEGER PRIMARY KEY, source TEXT NOT NULL, cutoff INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS checkpoint_source ON checkpoints(source,seq);
          CREATE TABLE IF NOT EXISTS messages(source TEXT, id TEXT, fingerprint TEXT NOT NULL,
            first_seen INTEGER NOT NULL, hit INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(source,id));
          CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY, created INTEGER NOT NULL, report TEXT NOT NULL, text TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS notice_items(source TEXT, message_id TEXT, notice_id TEXT, PRIMARY KEY(source,message_id));
          CREATE TABLE IF NOT EXISTS receipts(seq INTEGER PRIMARY KEY, notice_id TEXT NOT NULL, at_ms INTEGER NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def meta(self, key):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def initialize(self, config, at):
        policy = {"sources": config["sources"], "keywords": config["keywords"]}
        prior = self.meta("policy")
        if prior is not None and prior != policy:
            raise ValueError("RESIDENT_POLICY_CHANGED_REQUIRES_REVIEW")
        if prior is None:
            with self.db:
                self.db.execute("INSERT INTO metadata VALUES (?,?)", ("policy", json.dumps(policy, ensure_ascii=False)))
                self.db.execute("INSERT INTO metadata VALUES (?,?)", ("activation", json.dumps(at)))
                for source in config["sources"]:
                    self.db.execute("INSERT INTO checkpoints(source,cutoff) VALUES (?,?)", (source["id"], at))

    def bind_account(self, account):
        value = digest(str(account))
        prior = self.meta("account")
        if prior is not None and prior != value:
            raise ValueError("ALPHA_ACCOUNT_CHANGED_REQUIRES_REVIEW")
        if prior is None:
            with self.db:
                self.db.execute("INSERT INTO metadata VALUES (?,?)", ("account", json.dumps(value)))

    def cursor(self, source):
        row = self.db.execute("SELECT cutoff FROM checkpoints WHERE source=? ORDER BY seq DESC LIMIT 1", (source,)).fetchone()
        return row[0]

    def commit_poll(self, source, records, cutoff, at, keywords):
        activation = self.meta("activation")
        new_count = hits = 0
        with self.db:
            for record in unique_ordered(records):
                if record["source_id"] != source:
                    raise ValueError("ALPHA_RETURNED_WRONG_GROUP")
                if record["time_ms"] < activation:
                    continue  # First activation never alerts old history.
                if record["time_ms"] >= cutoff:
                    raise ValueError("ALPHA_RETURNED_MESSAGE_OUTSIDE_CUTOFF")
                body = json.dumps(record, ensure_ascii=False, sort_keys=True)
                fingerprint = digest(body)
                old = self.db.execute("SELECT fingerprint FROM messages WHERE source=? AND id=?", (source, record["id"])).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise ValueError("MESSAGE_CHANGED_REQUIRES_REVIEW")
                    continue
                hit = bool(matches(record["text"], keywords))
                self.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (source, record["id"], fingerprint, at, int(hit), body))
                new_count += 1
                hits += int(hit)
            if cutoff > self.cursor(source):
                self.db.execute("INSERT INTO checkpoints(source,cutoff) VALUES (?,?)", (source, cutoff))
        return new_count, hits

    def pending(self, rules=None):
        clause=''
        if rules is not None:
            self.db.create_function('business_alert',1,lambda body:int(business_rules.alert_allowed(business_rules.evaluate(json.loads(body)['text'],rules))))
            clause=' AND business_alert(m.body)=1'
        return self.db.execute("""SELECT m.* FROM messages m WHERE hit=1 AND NOT EXISTS
          (SELECT 1 FROM notice_items n WHERE n.source=m.source AND n.message_id=m.id)
          """+clause+" ORDER BY first_seen,source,id LIMIT 500").fetchall()

    def last_notice(self):
        return self.db.execute("SELECT COALESCE(MAX(created),0) FROM notices").fetchone()[0]

    def reserve_notice(self, identity, at, report, text, rows):
        with self.db:
            self.db.execute("INSERT INTO notices VALUES (?,?,?,?)", (identity, at, str(report), text))
            for row in rows:
                self.db.execute("INSERT INTO notice_items VALUES (?,?,?)", (row["source"], row["id"], identity))

    def receipt(self, identity, at, state, detail):
        with self.db:
            self.db.execute("INSERT INTO receipts(notice_id,at_ms,state,detail) VALUES (?,?,?,?)", (identity, at, state, json.dumps(detail, ensure_ascii=False)))

    def stats(self, rules=None):
        allowed=''
        if rules is not None:
            self.db.create_function('business_alert',1,lambda body:int(business_rules.alert_allowed(business_rules.evaluate(json.loads(body)['text'],rules))))
            allowed=' AND business_alert(m.body)=1'
        return {"newMessages": self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                "keywordHits": self.db.execute("SELECT COUNT(*) FROM messages WHERE hit=1").fetchone()[0],
                "pendingHits": self.db.execute("SELECT COUNT(*) FROM messages m WHERE hit=1 AND NOT EXISTS (SELECT 1 FROM notice_items n WHERE n.source=m.source AND n.message_id=m.id)"+allowed).fetchone()[0],
                "notificationsShown": self.db.execute("SELECT COUNT(*) FROM receipts WHERE state='shown'").fetchone()[0],
                "notificationsUncertain": self.db.execute("SELECT COUNT(*) FROM notices n WHERE NOT EXISTS (SELECT 1 FROM receipts r WHERE r.notice_id=n.id AND r.state='shown')").fetchone()[0]}


def render_notice(config, rows, identity, at, simulated=False):
    records = [json.loads(row["body"]) for row in rows]
    by_group = [(source, [r for r in records if r["source_id"] == source["id"]]) for source in config["sources"]]
    caption = "【本地模拟测试】" if simulated else ""
    text = caption + "；".join(f"{s['name']}：{len(group)}条" for s, group in by_group if group)
    text += "。命中收／UA，详细摘录已保存为 Markdown。"
    title = "微信群关键词提醒" + ("（测试）" if simulated else "")
    lines = ["# " + title, "", "本地模拟输入，没有向群发送测试消息。" if simulated else "本轮新发现的关键词命中；恢复后补读的内容可能早于提醒时间。",
             "", "提醒时间：" + datetime.fromtimestamp(at / 1000, LOCAL_ZONE).isoformat(timespec="seconds"), ""]
    for source, group in by_group:
        if not group:
            continue
        lines += ["## " + md_text(source["name"]), "", f"命中 {len(group)} 条。", ""]
        for item in sorted(group, key=lambda r: (r["time_ms"], r["id"])):
            lines += [f"- **{md_text(item['time'])} · {md_text(item['sender'])}**：{md_text(item['text'])}", ""]
    lines += ["报告标识：" + identity, ""]
    return title, text, "\n".join(lines)


class Engine:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def poll(self, reader, cutoff):
        new_count = hits = 0
        for source in self.config["sources"]:
            cursor = self.store.cursor(source["id"])
            if cutoff < cursor:
                raise ValueError("SYSTEM_CLOCK_BEFORE_CHECKPOINT")
            upper = min(cutoff, cursor + self.config["resident"]["catchupWindowSeconds"] * 1000)
            lower = max(self.store.meta("activation") - 1, cursor - self.config["overlapSeconds"] * 1000)
            records = reader.messages(source, self.config["maxPollMessagesPerGroup"], after_ms=lower,
                                      before_ms=upper, require_complete=True)
            added, matched = self.store.commit_poll(source["id"], records, upper, cutoff, self.config["keywords"])
            new_count += added
            hits += matched
        return {"added": new_count, "hits": hits}

    def flush(self, notifier, at, *, force=False, simulated=False):
        rows = self.store.pending(business_rules.load())
        if not rows:
            return None
        if not force and (at - rows[0]["first_seen"] < self.config["resident"]["aggregateSeconds"] * 1000 or
                          at - self.store.last_notice() < self.config["resident"]["notificationMinimumIntervalSeconds"] * 1000):
            return None
        # Availability is checked BEFORE reserving. An offline Anode leaves pending hits intact.
        notifier.ready()
        identity = "WKR-" + uuid.uuid4().hex[:12]
        title, text, markdown = render_notice(self.config, rows, identity, at, simulated)
        report = directories(self.config)["reportsDirectory"] / (datetime.fromtimestamp(at / 1000, LOCAL_ZONE).strftime("%Y%m%d-%H%M%S-") + identity + ".md")
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as stream:
            stream.write(markdown)
        self.store.reserve_notice(identity, at, report, text, rows)
        try:
            receipt = notifier.show(title, text)
            state = "shown" if receipt.get("state") == "shown" else "uncertain"
            self.store.receipt(identity, at, state, receipt)
            return {"id": identity, "state": state, "report": str(report), "hitCount": len(rows)}
        except Exception as error:
            # Unknown OS popup outcomes are never blindly replayed after a restart.
            self.store.receipt(identity, at, "uncertain", {"error": safe_error(error)})
            raise


def safe_error(error):
    # Avoid copying response bodies or credentials into persistent service logs.
    if isinstance(error, (ValueError, RuntimeError)):
        value = str(error)
        if value and all(c.isupper() or c.isdigit() or c == "_" for c in value):
            return value[:150]
    return type(error).__name__


class DesktopNotifier:
    def __init__(self, config, run):
        self.config, self.run = config, run

    def ready(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from anode_local_mcp import runtime
        _, command = runtime()
        value = subprocess.run(command[:-1] + ["status", "--json"], capture_output=True, text=True, encoding="utf-8",
                               timeout=15, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        state = json.loads(value.stdout)
        if state.get("state") != "ready":
            raise RuntimeError("NOTIFIER_ANODE_NOT_READY")
        return state

    def show(self, title, text):
        from pipeline import notify
        return notify(self.config, self.run, title, text)


def owner_busy(config):
    try:
        with dispatch_lock(directories(config)["stateDirectory"] / "owner"):
            return False
    except OSError:
        return True


def status(config):
    paths = directories(config)
    health = last_row(paths["stateDirectory"] / "health.jsonl")
    busy = owner_busy(config)
    value = {"enabled": enabled(config), "running": busy, "healthy": bool(busy and health and health.get("state") == "healthy" and now_ms() - health["at_ms"] <= config["resident"]["healthStaleSeconds"] * 1000),
             "health": health, "sendCalls": 0, "modelCalls": 0, "sources": config["sources"], "keywords": config["keywords"]}
    if (paths["stateDirectory"] / "monitor.sqlite3").exists():
        with closing(Store(paths["stateDirectory"])) as store:
            value["counts"] = store.stats(business_rules.load())
            value["activation_ms"] = store.meta("activation")
            value["checkpoints"] = {s["id"]: store.cursor(s["id"]) for s in config["sources"]} if store.meta("policy") else {}
    return value


def serve(config):
    paths = directories(config)
    if not enabled(config):
        return {"result": "RESIDENT_DISABLED"}
    run = paths["runsDirectory"] / (datetime.now(LOCAL_ZONE).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    run.mkdir(parents=True)
    with dispatch_lock(paths["stateDirectory"] / "owner"), closing(Store(paths["stateDirectory"])) as store:
        store.initialize(config, now_ms())
        engine = Engine(config, store)
        notifier = DesktopNotifier(config, run)
        reader = None
        failures = 0
        save_new(run / "started.json", {"pid": os.getpid(), "at_ms": now_ms(), "readOnly": True})
        while enabled(config):
            started = time.monotonic()
            try:
                if reader is None:
                    reader = AlphaReader(config)
                    reader.verify_sources()
                    store.bind_account(reader.account)
                polled = engine.poll(reader, now_ms())
                receipt = engine.flush(notifier, now_ms())
                failures = 0
                health = {"state": "healthy", "pid": os.getpid(), "run": str(run), **store.stats(business_rules.load()), **polled,
                          "readCallsThisProcess": reader.calls, "sendCalls": 0, "modelCalls": 0}
                if receipt:
                    append(run / "notifications.jsonl", receipt)
                delay = config["pollSeconds"]
            except Exception as error:
                failures += 1
                reader = None  # Re-read credentials and account identity on recovery.
                health = {"state": "degraded", "pid": os.getpid(), "run": str(run), "error": safe_error(error),
                          "consecutiveFailures": failures, **store.stats(), "sendCalls": 0, "modelCalls": 0}
                append(run / "errors.jsonl", health)
                delay = min(config["resident"]["maximumBackoffSeconds"], config["pollSeconds"] * 2 ** min(failures - 1, 6))
            append(paths["stateDirectory"] / "health.jsonl", health)
            end = started + delay
            while time.monotonic() < end and enabled(config):
                time.sleep(min(0.5, max(0, end - time.monotonic())))
        append(paths["stateDirectory"] / "health.jsonl", {"state": "stopped", "pid": os.getpid(), "run": str(run), **store.stats()})
    return {"result": "RESIDENT_STOPPED", "run": str(run)}


def notification_test(config):
    test = json.loads(json.dumps(config))
    base = "artifacts/wechat-resident-tests/live-" + uuid.uuid4().hex[:8]
    for key, folder in (("stateDirectory", "state"), ("runsDirectory", "runs"), ("reportsDirectory", "reports")):
        test["resident"][key] = base + "/" + folder
    paths = directories(test)
    run = paths["runsDirectory"]
    run.mkdir(parents=True)
    at = now_ms()
    with closing(Store(paths["stateDirectory"])) as store:
        store.initialize(test, at - 2000)
        for index, source in enumerate(test["sources"]):
            item = {"id": "local-test-" + str(index), "source_id": source["id"], "source_name": source["name"],
                    "time_ms": at - 1000, "time": "本地测试", "sender": "测试程序", "type": "文字", "text": "【模拟】收 UA，验证关键词提醒。"}
            store.commit_poll(source["id"], [item], at, at, test["keywords"])
        result = Engine(test, store).flush(DesktopNotifier(test, run), at, force=True, simulated=True)
        save_new(run / "result.json", {**result, "simulated": True, "alphaReadCalls": 0, "sendCalls": 0, "modelCalls": 0})
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "status", "enable", "disable", "test-notification"])
    args = parser.parse_args()
    cfg = configuration()
    if cfg["pollSeconds"] < 1:
        raise ValueError("POLL_INTERVAL_TOO_SHORT")
    if args.command == "serve":
        result = serve(cfg)
    elif args.command == "status":
        result = status(cfg)
    elif args.command == "test-notification":
        result = notification_test(cfg)
    else:
        result = control(cfg, args.command == "enable")
    # Scheduled Windows PowerShell may decode pipes with the system code page.
    # ASCII JSON keeps its syntax intact; consumers still recover Chinese names.
    print(json.dumps(result, ensure_ascii=True), flush=True)
