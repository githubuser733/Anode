"""Execute only the 23 targets approved in chat on 2026-10-31; default is validation.

This records that specific authorization, not permission for future cleanup lists.
Windows IFileOperation uses FOFX_RECYCLEONDELETE; no permanent-delete fallback.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "artifacts/maintenance-cleanup-20261031"
MANIFEST = EVIDENCE / "cleanup-manifest.json"
APPROVED_HASH = "d8b7fcf196ea61d1903cfe3ce2da8726bfd90decad64459f9fb28e2cdefc3448"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_tree(manifest):
    actual_files, actual_dirs = set(), set()
    targets = [Path(t["path"]) for t in manifest["targets"]]
    protected = [Path(p) for p in manifest["protectedPaths"]]
    for target in targets:
        if target.resolve(strict=True) != target or ROOT not in target.parents:
            raise RuntimeError(f"Outside approved scope: {target}")
        for keep in protected:
            if target == keep or target in keep.parents or keep in target.parents:
                raise RuntimeError(f"Protected overlap: {target}")
        todo = [target]
        while todo:
            p = todo.pop()
            if p.is_symlink() or p.is_junction():
                raise RuntimeError(f"Reparse point: {p}")
            if p.is_dir():
                actual_dirs.add(str(p))
                todo.extend(p.iterdir())
            else:
                actual_files.add(str(p))
    if actual_files != {f["path"] for f in manifest["files"]}:
        raise RuntimeError("File membership changed since approval")
    if actual_dirs != set(manifest["directories"]):
        raise RuntimeError("Directory membership changed since approval")
    for f in manifest["files"]:
        p = Path(f["path"])
        if p.stat().st_size != f["bytes"] or digest(p) != f["sha256"]:
            raise RuntimeError(f"File changed since approval: {p}")
    wmi = win32com.client.GetObject("winmgmts:")
    for process in wmi.ExecQuery("SELECT Name, ExecutablePath FROM Win32_Process"):
        if str(process.Name).lower() in ("bgdesk.exe", "bgdeskw.exe"):
            raise RuntimeError("BgDesk is running")
        if process.ExecutablePath:
            exe = Path(process.ExecutablePath)
            if any(exe == t or t in exe.parents for t in targets):
                raise RuntimeError(f"Target contains running executable: {exe}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if digest(MANIFEST) != APPROVED_HASH:
        raise RuntimeError("Approved manifest hash mismatch")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (len(manifest["targets"]), manifest["fileCount"], manifest["totalBytes"]) != (23, 3965, 2184172687):
        raise RuntimeError("Approved counts mismatch")
    verify_tree(manifest)
    print("PRECHECK_OK targets=23 files=3965 bytes=2184172687", flush=True)
    if not args.execute:
        print("VALIDATION_ONLY; no files moved")
        return
    system_result = json.loads((EVIDENCE / "startup-disabled.json").read_text(encoding="utf-8-sig"))
    if system_result["disabledTasks"] != 9 or system_result["clearedValues"] != 2:
        raise RuntimeError("Startup cleanup has not been verified")
    app = win32com.client.Dispatch("Shell.Application")
    before = {item.Path for item in app.NameSpace(shellcon.CSIDL_BITBUCKET).Items()}
    # Microsoft documents 0x00080000 as recycle rather than permanent deletion.
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT | shellcon.FOF_NOCONFIRMATION
             | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    with (EVIDENCE / "recycle-execution.jsonl").open("x", encoding="utf-8") as log:
        for target in manifest["targets"]:
            op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None,
                                            pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
            op.SetOperationFlags(flags)
            item = shell.SHCreateItemFromParsingName(target["path"], None, shell.IID_IShellItem)
            op.DeleteItem(item)
            op.PerformOperations()
            if op.GetAnyOperationsAborted() or Path(target["path"]).exists():
                raise RuntimeError(f"Recycle failed or aborted: {target['path']}")
            log.write(json.dumps({"source": target["path"], "originalAbsent": True}, ensure_ascii=False) + "\n")
            log.flush()
            print("RECYCLED " + target["path"], flush=True)
    recovered = {}
    for item in app.NameSpace(shellcon.CSIDL_BITBUCKET).Items():
        if item.Path not in before:
            folder = item.ExtendedProperty("System.Recycle.DeletedFrom")
            name = item.ExtendedProperty("System.FileName")
            if folder and name:
                recovered[str(Path(folder) / name)] = item.Path
    for target in manifest["targets"]:
        if target["path"] not in recovered:
            raise RuntimeError(f"Recycle Bin entry not confirmed: {target['path']}")
    for f in manifest["files"]:
        relative = Path(f["path"]).relative_to(Path(f["target"]))
        recycled = Path(recovered[f["target"]]) / relative
        if not recycled.is_file() or recycled.stat().st_size != f["bytes"] or digest(recycled) != f["sha256"]:
            raise RuntimeError(f"Recycled content verification failed: {f['path']}")
    result = {"status": "RECYCLE_VERIFIED", "targets": 23, "fileCount": 3965,
              "totalBytes": 2184172687, "sha256VerifiedFiles": 3965,
              "recycleLocations": {t["path"]: recovered[t["path"]] for t in manifest["targets"]},
              "permanentDeletionUsed": False, "recycleBinEmptied": False}
    with (EVIDENCE / "cleanup-result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print("RECYCLE_VERIFIED targets=23 files=3965 hashes=3965; originals absent; bin retained")


if __name__ == "__main__":
    main()
