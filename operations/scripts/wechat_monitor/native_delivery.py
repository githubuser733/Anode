"""Explicit filehelper delivery using verified Anode MCP and native WeChat UI.

Never calls Alpha send endpoints. A persisted reservation prevents uncertain retries.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import msvcrt
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from core import ROOT, Journal, configuration, digest, save_new
from clients import AlphaReader


class Anode:
    def __init__(self, run):
        self.lease_acquired = False
        self.errors = (run / ("anode-" + uuid.uuid4().hex + ".stderr")).open("x", encoding="utf-8")
        self.process = subprocess.Popen([sys.executable, "-B", "-u", str(ROOT / "scripts/anode_local_mcp.py")],
                                        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
                                        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        self.replies = queue.Queue()
        self.identifier = 0
        def receive():
            try:
                for line in self.process.stdout:
                    self.replies.put(json.loads(line))
            except Exception as error:
                self.replies.put({"readerError": type(error).__name__})
        threading.Thread(target=receive, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "wechat-native-delivery", "version": "1"}})
        self.process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.process.stdin.flush()

    def request(self, method, params):
        self.identifier += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.identifier, "method": method, "params": params}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            value = self.replies.get(timeout=max(0.1, deadline - time.monotonic()))
            if "readerError" in value:
                raise RuntimeError("ANODE_PROTOCOL_READ_FAILED")
            if value.get("id") == self.identifier:
                if "error" in value:
                    raise RuntimeError("ANODE_PROTOCOL_ERROR_" + str(value["error"].get("code")))
                return value["result"]
        raise TimeoutError("ANODE_RESULT_UNKNOWN_DO_NOT_RETRY")

    def tool(self, name, arguments=None):
        allowed = {"seat_status", "seat_lease", "seat_windows", "seat_observe", "seat_click", "seat_type"}
        if name not in allowed:
            raise ValueError("ANODE_TOOL_NOT_ALLOWLISTED")
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            text = next((c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"), "")
            raise RuntimeError("ANODE_TOOL_ERROR: " + text[:600])
        if "structuredContent" in result:
            return result["structuredContent"]
        text = next((c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def close(self):
        try:
            if self.lease_acquired:
                self.tool("seat_lease", {"action": "release"})
        finally:
            self.process.stdin.close()
            self.process.wait(timeout=15)
            self.errors.close()


@contextmanager
def dispatch_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "dispatch.lock"
    if not path.exists():
        try:
            with path.open("xb") as stream:
                stream.write(b"1")
        except FileExistsError:
            pass
    with path.open("r+b") as stream:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def checked_editor(observation, expected_text):
    elements = observation["elements"]
    titles = [e for e in elements if e.get("automationId", "").endswith("current_chat_name_label")]
    if len(titles) != 1 or titles[0].get("name") != "文件传输助手":
        raise ValueError("RECIPIENT_TITLE_NOT_FILEHELPER")
    editors = [e for e in elements if e.get("automationId") == "chat_input_field" and e.get("role") == "Edit"]
    if len(editors) != 1 or editors[0].get("name") != "文件传输助手":
        raise ValueError("RECIPIENT_EDITOR_NOT_FILEHELPER")
    editor = editors[0]
    if editor.get("textInfo", {}).get("complete") is not True or editor.get("text") != expected_text:
        raise ValueError("EXISTING_DRAFT_OR_COMPOSED_TEXT_MISMATCH")
    buttons = [e for e in elements if e.get("role") == "Button" and e.get("name") == "发送(S)"]
    if len(buttons) != 1:
        raise ValueError("SEND_BUTTON_NOT_UNIQUE")
    return editor, buttons[0]


def observe(anode, window_id):
    observation = anode.tool("seat_observe", {"windowId": window_id, "includeScreenshot": False,
        "maxElements": 500, "maxDepth": 20, "maxTextChars": 20000, "maxElementTextChars": 8000, "maxNameChars": 8000})
    if not isinstance(observation.get("elements"), list):
        raise ValueError("ANODE_OBSERVATION_MISSING")
    return observation


def click(anode, element):
    box = element["bounds"]
    if element.get("offscreen") or not element.get("enabled") or box["width"] < 1 or box["height"] < 1:
        raise ValueError("CONTROL_NOT_VISIBLE_ENABLED")
    anode.tool("seat_click", {"x": round(box["x"] + box["width"] / 2), "y": round(box["y"] + box["height"] / 2)})


def open_filehelper(anode, window_id):
    current = observe(anode, window_id)
    try:
        checked_editor(current, "")
        return current
    except ValueError as error:
        if "DRAFT" in str(error):
            raise
    search = [e for e in current["elements"] if e.get("role") == "Edit" and e.get("name") == "搜索"]
    if len(search) != 1 or search[0].get("text"):
        raise ValueError("SEARCH_FIELD_NOT_EMPTY_OR_UNIQUE")
    click(anode, search[0])
    anode.tool("seat_type", {"text": "文件传输助手", "perCharMs": 15})
    for _ in range(8):
        current = observe(anode, window_id)
        results = [e for e in current["elements"] if e.get("automationId") == "search_item_function_文件传输助手"]
        if len(results) == 1:
            click(anode, results[0])
            break
        time.sleep(0.25)
    else:
        raise ValueError("FILEHELPER_SEARCH_RESULT_NOT_UNIQUE")
    for _ in range(8):
        current = observe(anode, window_id)
        try:
            checked_editor(current, "")
            return current
        except ValueError as error:
            if "DRAFT" in str(error):
                raise
        time.sleep(0.25)
    raise ValueError("FILEHELPER_NOT_OPEN")


def validated_delivery(run, config):
    if not run.is_relative_to((ROOT / config["runsDirectory"]).resolve()):
        raise ValueError("RUN_OUTSIDE_PROJECT")
    task = json.loads((run / "delivery.json").read_text("utf-8"))
    text = (run / "delivery.txt").read_text("utf-8")
    if task.get("outputFormat") != "brief-plus-markdown-v1":
        raise ValueError("DELIVERY_REFORMAT_REQUIRED")
    if task["recipient"] != config["destination"] or set(task["sourceIds"]) != {s["id"] for s in config["sources"]}:
        raise ValueError("DELIVERY_POLICY_CHANGED")
    limit = min(config.get("maxBriefCharacters", 360), config["maxDeliveryCharacters"])
    if task["textSha256"] != digest(text) or not 1 <= len(text) <= limit or any(ord(c) < 32 for c in text):
        raise ValueError("DELIVERY_TEXT_CHANGED_OR_UNSAFE")
    if task.get("markdownFile") != "summary.md" or task.get("markdownSha256") != digest((run / "summary.md").read_text("utf-8")):
        raise ValueError("DELIVERY_REPORT_CHANGED")
    return task, text


def dispatch(run):
    config = configuration()
    run = run.resolve(strict=True)
    task, text = validated_delivery(run, config)
    state = ROOT / config["stateDirectory"]
    journal = Journal(state)
    anode = None
    attempt = {"marker": task["marker"], "destination": "filehelper", "channel": "Anode-native-WeChat-UI",
               "sendClicked": False, "uiVerified": False, "alphaReadbackVerified": False}
    with dispatch_lock(state):
        try:
            anode = Anode(run)
            status = anode.tool("seat_status")
            if status.get("state") != "ready" or not status.get("agentReady"):
                raise ValueError("ANODE_NOT_READY_WILL_NOT_START_SEAT")
            if status.get("lease", {}).get("ownerAgentId"):
                raise ValueError("ANODE_IN_USE")
            anode.tool("seat_lease", {"action": "acquire", "waitSeconds": 0, "ttlSeconds": 120})
            anode.lease_acquired = True
            windows = anode.tool("seat_windows", {"query": "微信"})["windows"]
            candidates = [w for w in windows if w.get("process") in ("Weixin", "WeChat") and w.get("title") == "微信"]
            if len(candidates) != 1:
                raise ValueError("WECHAT_WINDOW_NOT_UNIQUE_IN_ANODE")
            window = candidates[0]
            current = open_filehelper(anode, window["windowId"])
            editor, _ = checked_editor(current, "")
            journal.reserve_delivery(task["textSha256"], task["marker"], config["deliveryMinimumIntervalSeconds"])
            click(anode, editor)
            anode.tool("seat_type", {"text": text, "perCharMs": 15})
            current = observe(anode, window["windowId"])
            _, send = checked_editor(current, text)
            # Persist before clicking: a lost result is never permission to click again.
            journal.append({"kind": "send_click_pending", "marker": task["marker"], "recipient": "filehelper"})
            click(anode, send)
            attempt["sendClicked"] = True
            for _ in range(8):
                current = observe(anode, window["windowId"])
                checked_editor(current, "")
                visible = [e for e in current["elements"] if e.get("role") == "ListItem" and task["marker"] in e.get("name", "")]
                if any(text in e["name"] for e in visible):
                    attempt["uiVerified"] = True
                    break
                time.sleep(0.5)
            if not attempt["uiVerified"]:
                raise RuntimeError("SEND_OUTCOME_UNKNOWN_CHECK_FILEHELPER_DO_NOT_RETRY")
            reader = AlphaReader(config)
            for _ in range(10):
                messages = reader.messages(config["destination"], 20)
                if any(m["text"] == text for m in messages):
                    attempt["alphaReadbackVerified"] = True
                    break
                time.sleep(2)
            attempt["result"] = "WECHAT_NATIVE_SEND_VERIFIED" if attempt["alphaReadbackVerified"] else "WECHAT_NATIVE_SEND_UI_VERIFIED_ONLY"
            journal.append({"kind": "delivery_confirmed", "marker": task["marker"], **attempt})
            return attempt
        except Exception as error:
            attempt["error"] = type(error).__name__ + ": " + str(error)
            journal.append({"kind": "delivery_attempt_stopped", **attempt})
            raise
        finally:
            save_new(run / ("native-attempt-" + uuid.uuid4().hex[:8] + ".json"), attempt)
            if anode is not None:
                anode.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--send-reviewed", action="store_true", required=True,
                        help="Explicitly send this reviewed local draft to filehelper once.")
    args = parser.parse_args()
    print(json.dumps(dispatch(args.run), ensure_ascii=False))
