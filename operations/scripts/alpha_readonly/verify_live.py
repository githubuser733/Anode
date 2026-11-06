"""Read-only evidence: original API and companion return identical message records."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from server import config, make_client, APP_ID, PROJECT_ID


def verify():
    cfg = config()
    direct = make_client(cfg)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def proxy(route):
        request = urllib.request.Request(f"http://127.0.0.1:{cfg['viewerPort']}{route}", headers={"X-Alpha-Viewer": "1"})
        with opener.open(request, timeout=155) as response:
            return json.load(response)
    identity = proxy("/health")
    assert identity["app"] == APP_ID and identity["project"] == PROJECT_ID
    a, b = direct.read("/api/accounts"), proxy("/api/accounts")
    assert [x["id"] for x in a["accounts"]] == [x["id"] for x in b["accounts"]]
    account = a["selected_account_id"]
    params = {"account_id": account, "limit": 20, "offset": 0}
    sessions = proxy("/api/sessions?" + urllib.parse.urlencode(params))
    assert sessions["items"]
    second = None
    if sessions["hasMore"]:
        assert sessions["nextOffset"] > 0
        params["offset"] = sessions["nextOffset"]
        second = proxy("/api/sessions?" + urllib.parse.urlencode(params))
        assert second["items"] and second["offset"] == params["offset"]
    # Ascending history avoids new arrivals changing the head between two reads.
    route = "/api/messages?" + urllib.parse.urlencode({"account_id": account, "username": sessions["items"][0]["id"], "limit": 20, "offset": 0, "order": "asc"})
    original, mirrored = direct.read(route), proxy(route)
    assert original["messages"] == mirrored["messages"]
    digest = hashlib.sha256(json.dumps(original["messages"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    search = proxy("/api/search?" + urllib.parse.urlencode({"account_id": account, "query": "Alpha", "page_size": 2}))
    assert isinstance(search["items"], list)
    denied = {}
    for route in ["/mcp", "/api/messages/send-text", "/api/v1/exports"]:
        try:
            proxy(route)
            raise AssertionError("Forbidden route was accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 404
            denied[route] = error.code
    return {"result": "ALPHA_READONLY_LIVE_OK", "accountCount": len(a["accounts"]),
            "sessionPageCounts": [len(sessions["items"]), len(second["items"]) if second else 0],
            "identicalMessageCount": len(original["messages"]), "messageSha256": digest,
            "searchResultCount": len(search["items"]), "deniedRoutes": denied,
            "dataAccess": "original Alpha HTTP API only", "messagesSent": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify()
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
