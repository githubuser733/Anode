"""Read-only evidence for repeated Chat On Steroids ChatGPT login tabs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

USER_DATA = Path("C:/Users/Zhang/AppData/Roaming/chat-on-steroids")
ROOT = Path(__file__).resolve().parents[2]


def inspect(input_id):
    manifest = json.loads((USER_DATA / "extension/manifest.json").read_text("utf-8"))
    requests = json.loads((USER_DATA / "state/session-input.json").read_text("utf-8"))
    matches = [r for r in requests if r.get("id") == input_id]
    if len(matches) != 1:
        raise ValueError("REQUEST_NOT_UNIQUE_OR_NO_LONGER_RETAINED")
    request = matches[0]
    connections = []
    for line_number, line in enumerate((USER_DATA / "app.log").read_text("utf-8").splitlines(), 1):
        match = re.search(r"bridge: browser extension ([0-9.]+) connected", line)
        if match:
            connections.append({"time": line.split()[0], "version": match[1], "line": line_number})
    latest = connections[-1] if connections else None
    return {"observedAt": datetime.now(timezone.utc).isoformat(), "inputId": input_id,
            "requestCreatedAt": datetime.fromtimestamp(request["createdAt"] / 1000, timezone.utc).isoformat(),
            "requestState": request.get("state"), "cancelledByUser": request.get("cancelledByUser", False),
            "transportIntent": request.get("transportIntent"), "automation": request.get("automation"),
            "conversationEstablished": bool(request.get("conversationId")),
            "otherNonterminalInputs": sum(r.get("state") not in {"cancelled", "failed", "completed", "sent"} for r in requests),
            "installedExtensionVersion": manifest["version"], "latestLoggedConnection": latest,
            "versionMismatch": bool(latest and latest["version"] != manifest["version"]),
            "limitations": ["Connection version comes from the latest app log entry, not a live extension API.",
                            "This command never opens, closes or reloads tabs and never submits queued prompts."],
            "requestTextRecorded": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-id", default="06ae2c3e-b507-4127-b764-e2c5caef25dd")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect(args.input_id)
    if args.output:
        target = args.output.resolve()
        if not target.is_relative_to((ROOT / "artifacts").resolve()):
            raise ValueError("EVIDENCE_MUST_STAY_IN_PROJECT_ARTIFACTS")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
