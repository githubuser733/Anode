"""Explicit/bounded local monitoring workflow; does not send to WeChat."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from core import ROOT, LOCAL_ZONE, Journal, batches, configuration, delivery_text, digest, keyword_report, save_new, stamp
from clients import AlphaReader, Summarizer
from native_delivery import dispatch_lock
from formatting import brief_parts, report_markdown


def new_run(config, kind):
    run = ROOT / config["runsDirectory"] / (datetime.now(LOCAL_ZONE).strftime("%Y%m%d-%H%M%S") + "-" + kind + "-" + uuid.uuid4().hex[:6])
    run.mkdir(parents=True, exist_ok=False)
    return run


def write_outputs(run, config, records, reports, summaries, marker, created_at, replay=True):
    text = delivery_text(marker, reports, summaries, config, replay)
    markdown = report_markdown(marker, reports, summaries, config, records, created_at, replay)
    preview = "\n\n".join(brief_parts(marker, reports, summaries, config, replay)) + "\n"
    for name, content in (("delivery.txt", text), ("preview.txt", preview), ("summary.md", markdown)):
        with (run / name).open("x", encoding="utf-8") as stream:
            stream.write(content)
    task = {"marker": marker, "recipient": config["destination"], "textSha256": digest(text),
            "textCharacters": len(text), "sourceIds": [s["id"] for s in config["sources"]],
            "createdAt": created_at, "kind": "historical-test-replay" if replay else "new-message-digest",
            "nativeDispatchRequested": False, "outputFormat": "brief-plus-markdown-v1",
            "markdownFile": "summary.md", "markdownSha256": digest(markdown), "markdownAttached": False}
    save_new(run / "delivery.json", task)
    return {"briefCharacters": len(text), "markdownFile": str(run / "summary.md"), "previewFile": str(run / "preview.txt")}


def prepare(config, count=None, max_batches=10):
    count = config["historyPerGroup"] if count is None else count
    if not 1 <= count <= 30000 or not 1 <= max_batches <= 20:
        raise ValueError("TASK_BUDGET_INVALID")
    run = new_run(config, "digest")
    reader = AlphaReader(config)
    reader.verify_sources()
    before = int(time.time() * 1000) + 1
    records = []
    for source in config["sources"]:
        records.extend(reader.messages(source, count, before_ms=before))
    save_new(run / "messages.json", records)
    reports = keyword_report(records, config)
    save_new(run / "keyword-hits.json", reports)
    chunks = list(batches(records, config["maxMessagesPerBatch"], config["maxCharactersPerBatch"]))
    if not chunks:
        raise ValueError("NO_MESSAGES_TO_SUMMARIZE")
    if len(chunks) > max_batches:
        raise ValueError("MODEL_BATCH_BUDGET_EXCEEDED")
    print(json.dumps({"stage": "read_complete", "counts": [r["messageCount"] for r in reports],
                      "hits": [r["hitCount"] for r in reports], "batches": len(chunks), "run": str(run)}, ensure_ascii=False), flush=True)
    model = Summarizer(config)
    model_batches = []
    for index, chunk in enumerate(chunks):
        value, usage = model.summarize(chunk)
        item = {"index": index, "inputCount": len(chunk), "summary": value, "usage": usage}
        save_new(run / f"summary-batch-{index + 1:03d}.json", item)
        model_batches.append(item)
        print(json.dumps({"stage": "batch_summarized", "batch": index + 1, "total": len(chunks)}, ensure_ascii=False), flush=True)
    if len(model_batches) == 1:
        final = model_batches[0]["summary"]
    else:
        reduced = []
        for index, batch in enumerate(model_batches):
            for group in batch["summary"]["groups"]:
                reduced.append({"id": f"batch-{index + 1}-{group['group_id']}", "source_id": group["group_id"],
                                "text": group["summary"], "source_message_ids": group["source_ids"]})
        final, usage = model.summarize(reduced, reduction=True)
        save_new(run / "summary-reduction.json", {"summary": final, "usage": usage})
    summaries = {group["group_id"]: group["summary"] for group in final["groups"]}
    marker = "WXM-" + uuid.uuid4().hex[:10]
    output = write_outputs(run, config, records, reports, summaries, marker, stamp())
    result = {"result": "WECHAT_DIGEST_PREPARED", "run": str(run), "counts": [r["messageCount"] for r in reports],
              "hitCounts": [r["hitCount"] for r in reports], "summaryBatches": len(model_batches),
              "modelCalls": model.calls, "alphaReadCalls": reader.calls, "sendCalls": 0, "marker": marker, **output}
    save_new(run / "result.json", result)
    return result


def reformat(config, source_run):
    """Reuse an existing reviewed snapshot; no model, chat read, or send calls."""
    source_run = source_run.resolve(strict=True)
    if not source_run.is_relative_to((ROOT / config["runsDirectory"]).resolve()):
        raise ValueError("RUN_OUTSIDE_PROJECT")
    task = json.loads((source_run / "delivery.json").read_text("utf-8"))
    allowed = {s["id"] for s in config["sources"]}
    if set(task["sourceIds"]) != allowed or task["recipient"] != config["destination"]:
        raise ValueError("DELIVERY_POLICY_CHANGED")
    records = json.loads((source_run / "messages.json").read_text("utf-8"))
    if any(record["source_id"] not in allowed for record in records):
        raise ValueError("MESSAGE_SOURCE_NOT_ALLOWLISTED")
    summary_path = source_run / "summary-reduction.json"
    if not summary_path.exists():
        summary_path = source_run / "summary-batch-001.json"
    final = json.loads(summary_path.read_text("utf-8"))["summary"]
    summaries = {g["group_id"]: g["summary"] for g in final["groups"]}
    if set(summaries) != {record["source_id"] for record in records}:
        raise ValueError("SAVED_SUMMARY_GROUPS_CHANGED")
    reports = keyword_report(records, config)
    run = new_run(config, "reformatted")
    # Preserve the identity: reformatting a sent digest must not bypass the send ledger.
    output = write_outputs(run, config, records, reports, summaries, task["marker"], task["createdAt"],
                           replay=task["kind"] == "historical-test-replay")
    for name, value in (("messages.json", records), ("keyword-hits.json", reports),
                        ("summary-reduction.json", {"summary": final, "reformattedFrom": str(source_run)})):
        save_new(run / name, value)
    result = {"result": "WECHAT_DIGEST_PREPARED", "run": str(run), "reformattedFrom": str(source_run),
              "counts": [r["messageCount"] for r in reports], "hitCounts": [r["hitCount"] for r in reports],
              "modelCalls": 0, "alphaReadCalls": 0, "sendCalls": 0, "marker": task["marker"], **output}
    save_new(run / "result.json", result)
    return result


def notify(config, run, title, text):
    """Reuse the verified Anode parent-session tray helper, then stop our helper."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from anode_local_mcp import runtime
    _, command = runtime()
    status = subprocess.run(command[:-1] + ["status", "--json"], capture_output=True, text=True, encoding="utf-8",
                            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    parent = json.loads(status.stdout)["parentSession"]
    unique = uuid.uuid4().hex
    inbox, receipt = run / (unique + "-notify.jsonl"), run / (unique + "-receipt.jsonl")
    with inbox.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"id": unique, "title": title, "text": text}, ensure_ascii=False) + "\n")
    with (run / (unique + "-notify.stderr")).open("x", encoding="utf-8") as errors:
        helper = subprocess.Popen(command[:-1] + ["__chat-notify", "--inbox", str(inbox), "--receipts", str(receipt),
                                  "--parent-session", str(parent), "--owner-pid", str(os.getpid())],
                                  stdout=subprocess.DEVNULL, stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW)
        matched = []
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if receipt.exists():
                    lines = receipt.read_text("utf-8").splitlines()
                    rows = []
                    for line in lines:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue  # Helper may be appending the last line; re-read, never persist it.
                    matched = [r for r in rows if r.get("id") == unique]
                    if any(r["state"] in ("shown", "failed") for r in matched):
                        break
                if helper.poll() is not None:
                    break
                time.sleep(0.1)
        finally:
            Path(str(inbox) + ".stop").touch(exist_ok=False)
            helper.wait(timeout=5)
        if helper.returncode or any(r["state"] == "failed" for r in matched) or not matched:
            raise RuntimeError("DESKTOP_NOTIFICATION_FAILED")
        return {"state": "shown" if any(r["state"] == "shown" for r in matched) else "requested", "receipt": str(receipt)}


def _watch_locked(config, duration):
    if not 1 <= duration <= 3600 or config["pollSeconds"] < 5:
        raise ValueError("WATCH_BUDGET_INVALID")
    run = new_run(config, "watch")
    reader = AlphaReader(config)
    reader.verify_sources()
    journal = Journal(ROOT / config["stateDirectory"])
    # A fresh run establishes the current position; old messages are never new alerts.
    start_ms = int(time.time() * 1000)
    baseline = []
    for source in config["sources"]:
        baseline.extend(reader.messages(source, 1, before_ms=start_ms + 1))
    journal.mark_seen(baseline)
    lower = start_ms - 1
    stop = run / "STOP"
    deadline = time.monotonic() + duration
    polls = new_count = hit_count = 0
    while time.monotonic() < deadline and not stop.exists():
        records = []
        cutoff = int(time.time() * 1000) + 1
        for source in config["sources"]:
            records.extend(reader.messages(source, config["maxPollMessagesPerGroup"], after_ms=lower,
                                           before_ms=cutoff, require_complete=True))
        unseen = journal.unseen(records)
        if unseen:
            reports = keyword_report(unseen, config)
            hits = sum(item["hitCount"] for item in reports)
            # Save complete event before advancing the checkpoint, allowing inspection/recovery.
            save_new(run / f"poll-{polls + 1:04d}.json", {"records": unseen, "reports": reports})
            journal.append({"kind": "pending_alert", "run": str(run), "poll": polls + 1, "hits": hits})
            journal.mark_seen(unseen)
            new_count += len(unseen)
            hit_count += hits
            # No immediate sends or popups per message: aggregate at the end of this bounded watch.
        lower = max(start_ms - 1, cutoff - config["overlapSeconds"] * 1000)
        polls += 1
        remaining = min(config["pollSeconds"], max(0, deadline - time.monotonic()))
        until = time.monotonic() + remaining
        while time.monotonic() < until and not stop.exists():
            time.sleep(min(0.5, max(0, until - time.monotonic())))
    result = {"result": "WECHAT_BOUNDED_WATCH_OK", "run": str(run), "polls": polls, "newMessages": new_count,
              "keywordHits": hit_count, "sourceIds": [s["id"] for s in config["sources"]],
              "sendCalls": 0, "modelCalls": 0, "stoppedByFile": stop.exists(),
              "lateArrivalCoverageSeconds": config["overlapSeconds"], "unboundedLateArrivalGuaranteed": False}
    if hit_count:
        result["desktopNotification"] = notify(config, run, "微信群关键词提醒",
            f"本轮两个指定群共新增 {new_count} 条消息，其中 {hit_count} 条命中收／UA。结果已保存；未向群发送。")
    save_new(run / "result.json", result)
    return result


def watch(config, duration):
    # Keep concurrent launches from reading/checkpointing the same sources twice.
    with dispatch_lock(ROOT / config["stateDirectory"] / "watch-owner"):
        return _watch_locked(config, duration)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--count", type=int)
    p.add_argument("--max-batches", type=int, default=10)
    p = sub.add_parser("watch")
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--summarize-new", action="store_true", help="If new messages arrive, prepare a recent-history digest; never sends it.")
    p = sub.add_parser("notify")
    p.add_argument("run", type=Path)
    p = sub.add_parser("reformat", help="Reformat a saved digest as a short notice and Markdown; does not send.")
    p.add_argument("run", type=Path)
    args = parser.parse_args()
    config = configuration()
    if args.command == "prepare":
        result = prepare(config, args.count, args.max_batches)
    elif args.command == "reformat":
        result = reformat(config, args.run)
    elif args.command == "watch":
        result = watch(config, args.seconds)
        if args.summarize_new and result["newMessages"]:
            result["digest"] = prepare(config)
            save_new(Path(result["run"]) / "watch-with-summary.json", result)
    else:
        run = args.run.resolve(strict=True)
        if not run.is_relative_to((ROOT / config["runsDirectory"]).resolve()):
            raise ValueError("RUN_OUTSIDE_PROJECT")
        data = json.loads((run / "result.json").read_text("utf-8"))
        if data["result"] != "WECHAT_DIGEST_PREPARED":
            raise ValueError("DIGEST_NOT_READY")
        result = notify(config, run, "微信群关键词与摘要测试", "两群历史记录回放：命中 " + "/".join(map(str, data["hitCounts"])) + " 条。摘要已生成，待投递文件传输助手。")
        save_new(run / "desktop-notification.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
