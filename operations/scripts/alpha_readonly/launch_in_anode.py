"""Open the read-only companion in an already-ready Anode, from any workspace."""
import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import uuid

from server import ROOT, ViewerError, config, make_client

sys.path.insert(0, str(ROOT / "scripts"))
from anode_local_mcp import runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--show-errors", action="store_true")
    args = parser.parse_args()
    try:
        cfg = config()
        make_client(cfg).read("/api/accounts")
        _, mcp_command = runtime()
        cli = mcp_command[:-1] + ["--agent", "alpha-readonly-" + uuid.uuid4().hex]
        result = subprocess.run(cli + ["status", "--json"], capture_output=True, text=True,
                                encoding="utf-8", timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        state = json.loads(result.stdout)
        if result.returncode or state.get("state") != "ready" or not state.get("agentReady"):
            raise ViewerError("ANODE_NOT_READY")
        if args.preflight:
            print(json.dumps({"result": "ALPHA_ANODE_PREFLIGHT_OK", "session": state["session"],
                              "backendReadable": True, "processStarted": False}))
            return 0
        if state.get("lease", {}).get("ownerAgentId"):
            raise ViewerError("ANODE_BUSY")
        pythonw = str(Path(cfg["pythonExecutable"]).with_name("pythonw.exe"))
        # This verified integrated CLI variant sets startSeat=false atomically.
        # It refuses a stopped, interactive, or different parent's seat.
        lease = subprocess.run(cli + ["__chat-lease", "acquire", "--ttl", "30", "--wait", "0"],
                               capture_output=True, text=True, encoding="utf-8", timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        if lease.returncode:
            print(lease.stderr.strip(), file=sys.stderr)
            raise ViewerError("ANODE_LEASE_UNAVAILABLE")
        token = json.loads(lease.stdout)["leaseToken"]
        leased_cli = cli + ["--lease", token]
        try:
            result = subprocess.run(leased_cli + ["run", pythonw, "-B", str(Path(__file__).with_name("server.py")),
                                                 "--open", "--show-errors"], cwd=ROOT, capture_output=True,
                                    text=True, encoding="utf-8", timeout=25,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                print(result.stderr.strip(), file=sys.stderr)
                raise ViewerError("ANODE_LAUNCH_FAILED")
        finally:
            released = subprocess.run(leased_cli + ["lease", "release"], capture_output=True, text=True,
                                      encoding="utf-8", timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if released.returncode:
                print(released.stderr.strip(), file=sys.stderr)
                raise ViewerError("ANODE_LEASE_RELEASE_FAILED")
        print("ALPHA_READONLY_ANODE_LAUNCH_REQUESTED")
        return 0
    except Exception as error:
        code = error.code if isinstance(error, ViewerError) else "ANODE_LOCAL_ENTRY_FAILED"
        print(code, file=sys.stderr)
        if args.show_errors:
            ctypes.windll.user32.MessageBoxW(None, "只读副窗口未启动：" + code + "\n请保持原 Alpha 打开，并确认 Anode 已启动、登录且没有被其他操作占用。", "Alpha 只读副窗口", 16)
        return 1


if __name__ == "__main__":
    sys.exit(main())
