"""Deterministic matching, batching and append-only state; no desktop/network access."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
LOCAL_ZONE = timezone(timedelta(hours=8))


def configuration():
    result = json.loads((ROOT / "config/wechat-monitor.json").read_text("utf-8"))
    if result["destination"]["id"] != "filehelper" or result["apiSendingEnabled"]:
        raise ValueError("UNAPPROVED_DELIVERY_POLICY")
    if not result["sources"] or any(not s["id"].endswith("@chatroom") for s in result["sources"]):
        raise ValueError("INVALID_SOURCE_POLICY")
    return result


def save_new(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def stamp():
    return datetime.now(LOCAL_ZONE).isoformat(timespec="seconds")


def one_line(text):
    return " ".join(str(text).split())


def matches(text: str, keywords):
    folded = text.casefold()
    found = []
    for keyword in keywords:
        value = keyword.casefold()
        # UA1000 and 收UA match; 'Guang' does not match UA inside a Latin word.
        if value.isascii() and value.isalpha():
            hit = re.search(r"(?<![a-z])" + re.escape(value) + r"(?![a-z])", folded) is not None
        else:
            hit = value in folded
        if hit:
            found.append(keyword)
    return found


def normalize(source, item):
    identity = item.get("id")
    timestamp = item.get("sent_at_unix_ms")
    if not isinstance(identity, str) or not identity or timestamp is None:
        raise ValueError("MESSAGE_ID_OR_TIMESTAMP_MISSING")
    text = item.get("text", "")
    if not isinstance(text, str):
        raise ValueError("MESSAGE_TEXT_INVALID")
    return {"source_id": source["id"], "source_name": source["name"], "id": identity,
            "time_ms": int(timestamp), "time": item.get("time", ""),
            "sender": item.get("sender", "未知"), "type": item.get("type", ""), "text": text}


def message_key(message):
    return message["source_id"] + ":" + message["id"]


def unique_ordered(messages):
    unique = {}
    for message in messages:
        key = message_key(message)
        if key in unique and unique[key] != message:
            raise ValueError("DUPLICATE_MESSAGE_CHANGED")
        unique[key] = message
    return sorted(unique.values(), key=lambda m: (m["time_ms"], message_key(m)))


def batches(messages, max_messages=3000, max_chars=90000):
    if not 1 <= max_messages <= 3000 or max_chars < 1000:
        raise ValueError("BATCH_LIMIT_INVALID")
    current, size = [], 0
    for message in unique_ordered(messages):
        length = len(json.dumps(message, ensure_ascii=False)) + 2
        if length > max_chars:
            raise ValueError("ONE_MESSAGE_EXCEEDS_BATCH_BUDGET")
        if current and (len(current) >= max_messages or size + length > max_chars):
            yield current
            current, size = [], 0
        current.append(message)
        size += length
    if current:
        yield current


def keyword_report(messages, config):
    result = []
    for source in config["sources"]:
        records = [m for m in messages if m["source_id"] == source["id"]]
        hits = []
        counts = dict.fromkeys(config["keywords"], 0)
        for message in records:
            found = matches(message["text"], config["keywords"])
            if found:
                hits.append({**message, "keywords": found})
                for keyword in found:
                    counts[keyword] += 1
        result.append({"source": source, "messageCount": len(records), "hitCount": len(hits),
                       "keywordCounts": counts, "hits": hits})
    return result


class Journal:
    """Append-only, synchronously flushed checkpoint and send ledger."""
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "events.jsonl"

    def rows(self):
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as stream:
            # A partial/corrupt record fails closed; it is never silently discarded.
            return [json.loads(line) for line in stream if line.strip()]

    def append(self, value):
        import os
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": stamp(), **value}, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def unseen(self, messages):
        seen = {key for row in self.rows() if row.get("kind") == "seen" for key in row["keys"]}
        return [m for m in unique_ordered(messages) if message_key(m) not in seen]

    def mark_seen(self, messages):
        self.append({"kind": "seen", "keys": [message_key(m) for m in messages]})

    def reserve_delivery(self, digest, marker, minimum_seconds, now=None):
        now = datetime.now(timezone.utc) if now is None else now
        reservations = [r for r in self.rows() if r.get("kind") == "delivery_reserved"]
        if any(r["digest"] == digest or r["marker"] == marker for r in reservations):
            raise ValueError("DELIVERY_ALREADY_ATTEMPTED_DO_NOT_RETRY")
        if reservations:
            elapsed = (now - datetime.fromisoformat(reservations[-1]["at"])).total_seconds()
            if elapsed < minimum_seconds:
                raise ValueError("DELIVERY_COOLDOWN_ACTIVE")
        self.append({"kind": "delivery_reserved", "digest": digest, "marker": marker})


def delivery_text(marker, reports, summaries, config, replay=True):
    from formatting import brief_text
    result = brief_text(marker, reports, summaries, config, replay)
    if len(result) > config["maxDeliveryCharacters"]:
        raise ValueError("DELIVERY_TOO_LONG_REVIEW_REQUIRED")
    if "\n" in result or "\r" in result or "\t" in result:
        raise ValueError("KEYBOARD_CONTROL_CHARACTER_IN_DELIVERY")
    return result


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
