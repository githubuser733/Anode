"""Bounded, read-only Alpha message-limit probe for one explicitly named group.

Does not generate summaries, change preferences, capture keys or send messages.
The output contains counts and schemas, never message bodies or the API token.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import urllib.parse

from server import ROOT, config, credentials


def inspect(group):
    installed = json.loads((ROOT / "config/alpha-shared.json").read_text("utf-8"))
    actual_hash = hashlib.sha256(Path(installed["asar"]).read_bytes()).hexdigest()
    if actual_hash != installed["asarSha256"]:
        raise RuntimeError("ALPHA_VERSION_CHANGED_REVIEW_REQUIRED")
    port, token = credentials(Path(config()["alphaUserData"]))

    def request(path, data=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=35)
        body = None if data is None else json.dumps(data).encode("utf-8")
        try:
            connection.request("GET" if data is None else "POST", path, body,
                               {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            response = connection.getresponse()
            value = json.load(response)
            if response.status != 200 or "error" in value:
                raise RuntimeError("READ_PROBE_HTTP_OR_PROTOCOL_ERROR_" + str(response.status))
            return value
        finally:
            connection.close()

    listed = request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in listed["result"]["tools"]}

    def read_tool(name, arguments):
        if name not in {"wechat.chat.resolve_session", "wechat.chat.get_messages"}:
            raise RuntimeError("TOOL_NOT_ALLOWED")
        if tools[name].get("annotations", {}).get("readOnlyHint") is not True:
            raise RuntimeError("READ_ONLY_CONTRACT_CHANGED")
        value = request("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": name, "arguments": arguments}})
        if value["result"].get("isError"):
            raise RuntimeError("READ_ONLY_TOOL_FAILED")
        return value["result"]["structuredContent"]

    account = request("/api/v1/accounts")["selected_account_id"]
    resolved = read_tool("wechat.chat.resolve_session", {"account": account, "query": group})
    matches = [item for item in resolved["items"] if item.get("display_name") == group]
    if len(matches) != 1 or not matches[0]["id"].endswith("@chatroom"):
        raise RuntimeError("EXACT_UNIQUE_GROUP_REQUIRED")
    session = matches[0]["id"]

    http_results = []
    for limit in (50, 100):
        params = {"account_id": account, "username": session, "limit": limit, "order": "asc"}
        page = request("/api/v1/messages?" + urllib.parse.urlencode(params))
        count = len(page["messages"])
        if count != limit:
            raise RuntimeError("NOT_ENOUGH_HISTORY_TO_VERIFY_REQUESTED_LIMIT_" + str(limit))
        http_results.append({"requestedLimit": limit, "returnedCount": count,
                             "hasMore": page.get("hasMore"), "nextOffset": page.get("nextOffset")})

    mcp_results = []
    for limit in (None, 100):
        arguments = {"account": account, "username": session, "order": "asc", "defer_media": True}
        if limit is not None:
            arguments["limit"] = limit
        page = read_tool("wechat.chat.get_messages", arguments)
        count = len(page["items"])
        if count != (20 if limit is None else limit):
            raise RuntimeError("MCP_LIMIT_CONTRACT_CHANGED")
        mcp_results.append({"requestedLimit": "omitted" if limit is None else limit,
                            "effectiveLimit": page.get("limit"), "returnedCount": count})

    return {"result": "ALPHA_SUMMARY_LIMIT_READONLY_PROBE_OK", "alphaVersion": installed["version"],
            "asarSha256": actual_hash, "groupName": group, "groupId": session,
            "http": http_results, "mcp": mcp_results,
            "mcpLimitSchema": tools["wechat.chat.get_messages"]["inputSchema"]["properties"]["limit"],
            "mcpToolNames": sorted(tools),
            "sendCalls": 0, "modelCalls": 0, "settingsChanged": False, "messageBodiesRecorded": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("Output exists; choose a new evidence filename.")
    report = inspect(args.group)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
