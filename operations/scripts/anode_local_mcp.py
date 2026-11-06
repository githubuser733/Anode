"""Stable, account-independent stdio entry for the verified local Anode build."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "anode-local-mcp.json"


def runtime():
    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    executable = (ROOT / config["executable"]).resolve(strict=True)
    if not executable.is_relative_to((ROOT / "work").resolve()):
        raise ValueError("The configured Anode executable must remain inside this project's work directory.")
    with executable.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != config["sha256"]:
        raise ValueError("Anode build hash changed; verify the new build before updating the active manifest.")
    channel = config["channel"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", channel):
        raise ValueError("Invalid Anode channel in the active manifest.")
    return config, [str(executable), "--channel", channel, "mcp"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify paths/hash without starting any process.")
    args = parser.parse_args()
    config, command = runtime()
    if args.check:
        print(json.dumps({"status": "ANODE_LOCAL_MCP_ENTRY_OK", "command": command,
                          "commit": config["commit"], "authentication": "none",
                          "processStarted": False}, ensure_ascii=False))
        return 0
    # Inherit the MCP client's pipes directly. Do not parse or buffer protocol messages,
    # print a banner, start a desktop, or read any cloud account credentials here.
    return subprocess.run(command, cwd=ROOT, stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                          stderr=sys.stderr.buffer, creationflags=subprocess.CREATE_NO_WINDOW).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"Anode local MCP: {error}", file=sys.stderr)
        raise SystemExit(1)
