"""Read-only monitor assessment. Never enables push, captures keys or sends messages."""
import argparse
import http.client
import json
from pathlib import Path
import urllib.parse

from server import config, credentials


def inspect():
    port, token = credentials(Path(config()["alphaUserData"]))
    def call(route, data=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=155)
        body = None if data is None else json.dumps(data).encode("utf-8")
        connection.request("GET" if data is None else "POST", route, body=body,
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            response = connection.getresponse()
            value = json.load(response)
            if response.status != 200:
                raise RuntimeError("Read-only probe HTTP " + str(response.status))
            return value
        finally:
            connection.close()

    push = call("/api/v1/push/status")
    listed = call("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = listed["result"]["tools"]
    names = {tool["name"] for tool in tools}
    reader = next(tool for tool in tools if tool["name"] == "wechat.chat.get_messages")
    assert reader["annotations"]["readOnlyHint"] is True
    accounts = call("/api/v1/accounts")
    account = accounts["selected_account_id"]
    sessions = call("/api/v1/sessions?" + urllib.parse.urlencode({"account_id": account, "limit": 1}))
    session = sessions["items"][0]["id"]
    result = call("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "wechat.chat.get_messages", "arguments": {"account": account, "username": session,
                                                             "limit": 1, "defer_media": True}}})
    assert "error" not in result, "MCP protocol error"
    assert not result["result"].get("isError"), "MCP read tool returned an error"
    structured = result["result"].get("structuredContent")
    assert isinstance(structured, dict), "MCP read returned no structured data"
    return {"result": "ALPHA_MONITOR_READONLY_PROBE_OK", "push": push,
            "mcpToolCount": len(tools), "mcpReadCallSucceeded": True,
            "mcpReadResultKeys": sorted(structured),
            "sendToolAdvertised": "wechat.chat.send_text" in names,
            "sendStatusToolAdvertised": "wechat.chat.get_send_status" in names,
            "pushSettingChanged": False, "sendCalls": 0, "keyCaptureCalls": 0,
            "chatContentRecorded": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect()
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
