"""Explicitly authorized, once-only HTTP/MCP send experiment to filehelper.

This is separate from the read-only monitor, never activates sending or connects
WeChat, and never retries an uncertain send. Not part of the unit test suite.
"""
import argparse
import http.client
import json
from pathlib import Path
import time
import uuid
import urllib.parse

from core import ROOT, configuration, save_new, stamp
from clients import AlphaReader


def experiment(directory):
    directory = directory.resolve()
    if not directory.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("EVIDENCE_MUST_STAY_IN_PROJECT")
    directory.mkdir(parents=True, exist_ok=False)
    reader = AlphaReader(configuration())
    reader.verify_sources()
    marker = "WXAPI-" + uuid.uuid4().hex[:10]
    plan = [
        {"channel": "HTTP", "text": f"【Alpha HTTP API 发送测试 {marker}-HTTP】仅验证接口能否发送到文件传输助手，不向群发送。", "idempotency_key": uuid.uuid4().hex},
        {"channel": "MCP", "text": f"【Alpha MCP 发送测试 {marker}-MCP】仅验证 MCP 能否发送到文件传输助手，不向群发送。", "idempotency_key": uuid.uuid4().hex},
    ]
    save_new(directory / "approved-plan.json", {"authorization": "User explicitly requested one API and one MCP send test in this task.",
                                                "at": stamp(), "recipient": "filehelper", "plan": plan})

    def request(path, data=None):
        if path not in ("/api/v1/messages/send-text", "/mcp") and not path.startswith("/api/v1/messages/send-text/status?"):
            raise ValueError("TEST_ROUTE_DENIED")
        connection = http.client.HTTPConnection("127.0.0.1", reader.port, timeout=45)
        try:
            connection.request("GET" if data is None else "POST", path,
                               None if data is None else json.dumps(data).encode("utf-8"),
                               {"Authorization": "Bearer " + reader.token, "Content-Type": "application/json"})
            response = connection.getresponse()
            return {"httpStatus": response.status, "body": json.load(response)}
        finally:
            connection.close()

    results = []
    for item in plan:
        payload = {"account": reader.account, "username": "filehelper", "text": item["text"],
                   "wait_seconds": 5, "idempotency_key": item["idempotency_key"]}
        save_new(directory / (item["channel"] + "-submitted.json"), {"at": stamp(), "payload": payload})
        if item["channel"] == "HTTP":
            response = request("/api/v1/messages/send-text", payload)
            outcome = response["body"]
        else:
            response = request("/mcp", {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {
                "name": "wechat.chat.send_text", "arguments": payload}})
            rpc = response["body"].get("result", {})
            outcome = rpc.get("structuredContent")
            if outcome is None:
                content = next((c["text"] for c in rpc.get("content", []) if c.get("type") == "text"), "{}")
                try:
                    outcome = json.loads(content)
                except json.JSONDecodeError:
                    outcome = {"rawText": content}
        save_new(directory / (item["channel"] + "-response.json"), response)
        record = {"channel": item["channel"], "httpStatus": response["httpStatus"], "outcome": outcome}
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if outcome.get("state") in ("sending", "unknown"):
            run_id = outcome.get("run_id")
            if run_id:
                record["statusCheck"] = request("/api/v1/messages/send-text/status?" + urllib.parse.urlencode({"run_id": run_id, "account": reader.account}))
                save_new(directory / (item["channel"] + "-status.json"), record["statusCheck"])
            # Do not submit the next channel while an earlier send is uncertain.
            break
        if response["httpStatus"] >= 500 or "error" in response["body"] and response["httpStatus"] == 200:
            break
        if outcome.get("state") == "delivered":
            time.sleep(10)
    records = reader.messages(configuration()["destination"], 30)
    visible = [item["channel"] for item in plan if any(m["text"] == item["text"] for m in records)]
    report = {"result": "ALPHA_SEND_CAPABILITY_EXPERIMENT_RECORDED", "marker": marker, "recipient": "filehelper",
              "requestsSubmitted": len(results), "results": results, "readbackMatchedChannels": visible,
              "featureActivatedByTest": False, "connectionEstablishedByTest": False, "automaticRetries": 0}
    save_new(directory / "result.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="A new, non-existing evidence directory.")
    parser.add_argument("--user-approved-api-and-mcp-test", action="store_true", required=True)
    args = parser.parse_args()
    experiment(args.directory)
