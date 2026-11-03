"""Check the additive text protocol against a fixed baseline; retain all evidence."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

parser = argparse.ArgumentParser()
parser.add_argument("--exe", required=True)
parser.add_argument("--baseline-exe", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
output = Path(args.out).resolve()
output.mkdir(parents=True, exist_ok=False)


def exchange(executable, invalid_calls=()):
    environment = os.environ.copy()
    environment["ANODE_CHANNEL"] = "text-protocol-check"
    process = subprocess.Popen(
        [str(Path(executable).resolve(strict=True)), "--channel", "text-protocol-check", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    replies = queue.Queue()
    threading.Thread(target=lambda: [replies.put(line) for line in process.stdout], daemon=True).start()

    def request(identifier, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        result = json.loads(replies.get(timeout=15))
        assert result.get("id") == identifier and "result" in result, result
        return result["result"]

    try:
        request(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "text protocol contract check", "version": "1"}})
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        tools = request(2, "tools/list", {})["tools"]
        rejected = []
        for identifier, (name, arguments) in enumerate(invalid_calls, 3):
            response = request(identifier, "tools/call", {"name": name, "arguments": arguments})
            assert response.get("isError") is True, (name, arguments, response)
            rejected.append({"tool": name, "arguments": arguments, "response": response})
        return tools, rejected
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        errors = process.stderr.read()
        assert process.returncode == 0, (process.returncode, errors)


invalid_calls = [
    ("seat_observe", {"windowId": "w_fixture", "maxNameChars": 0}),
    ("seat_observe", {"windowId": "w_fixture", "maxElementTextChars": 20001}),
    ("seat_element", {"snapshotId": "s_fixture", "elementId": "e0", "action": "read_text", "maxChars": 20001}),
    ("seat_element", {"snapshotId": "s_fixture", "elementId": "e0", "action": "invoke", "textSource": "name"}),
    ("seat_window", {"windowId": "w_fixture", "action": "read_clipboard"}),
    ("seat_window", {"windowId": "w_fixture", "action": "read_clipboard", "afterSequence": "-1"}),
]
original, _ = exchange(args.baseline_exe)
candidate, rejected = exchange(args.exe, invalid_calls)
old = {tool["name"]: tool for tool in original}
new = {tool["name"]: tool for tool in candidate}
assert old.keys() == new.keys() and len(new) == 41, "Tool names/count changed unexpectedly"
expected_changes = {"seat_observe", "seat_element", "seat_window"}
changed = {name for name in old if old[name] != new[name]}
assert changed == expected_changes, changed


def compatible(before, after, location):
    for key, value in before.items():
        if key == "description":
            continue
        assert key in after, (location, key, "removed")
        if key == "properties":
            for field, schema in value.items():
                assert field in after[key], (location, field, "removed property")
                compatible(schema, after[key][field], location + "." + field)
        elif key == "enum":
            assert set(value).issubset(after[key]), (location, "removed enum values")
        else:
            assert value == after[key], (location, key, value, after[key])


for name in expected_changes:
    compatible(old[name]["inputSchema"], new[name]["inputSchema"], name)
assert set(new["seat_observe"]["inputSchema"]["properties"]) - set(old["seat_observe"]["inputSchema"]["properties"]) == {
    "maxNameChars", "maxElementTextChars", "screenshotOnTextIssue"}
for filename, value in (("baseline-tools.json", original), ("candidate-tools.json", candidate), ("invalid-requests.json", rejected)):
    with (output / filename).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
result = {"status": "ANODE_TEXT_PROTOCOL_OK", "tools": len(new), "unchangedTools": len(new) - len(changed),
    "extendedTools": sorted(changed), "oldArgumentsPreserved": True, "invalidCallsRejected": len(rejected),
    "seatStarted": False, "desktopInputSent": False}
with (output / "result.json").open("x", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2)
print(json.dumps(result))
