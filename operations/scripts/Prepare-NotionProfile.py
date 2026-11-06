"""Seed an owned Notion profile once, only after all Notion processes exit."""
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def main():
    spec = json.loads((ROOT / "config/notion-concurrent.json").read_text(encoding="utf-8"))
    source = Path(spec["sourceProfile"])
    runtime = ROOT / spec["runtime"]
    profile = runtime / "profile"
    marker = runtime / "profile-owner.json"
    owner = {"app": "notion", "runtime": str(runtime), "profile": str(profile),
             "source": str(source), "version": spec["verifiedProductVersion"], "packageSha256": spec["packageSha256"]}
    if marker.is_file():
        actual = json.loads(marker.read_text(encoding="utf-8"))
        if any(actual.get(key) != value for key, value in owner.items()) or not profile.is_dir():
            raise RuntimeError("Notion profile ownership mismatch")
        print(json.dumps({"status": "NOTION_PROFILE_READY", "existing": True, "profile": str(profile)}))
        return
    if runtime.exists():
        raise RuntimeError("Unowned/incomplete Notion runtime exists; no file was overwritten")
    result = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq Notion.exe", "/FO", "CSV", "/NH"],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if any(row and row[0].casefold() == "notion.exe" for row in csv.reader(io.StringIO(result.stdout.decode("mbcs")))):
        raise RuntimeError("NOTION_PROFILE_SOURCE_BUSY: exit Notion once to take the initial login snapshot; later launches reuse the owned profile")
    files = ["Local State", "state.json", "Preferences", "Partitions/notion/Preferences"]
    databases = ["notion.db", "Network/Cookies", "Partitions/notion/Network/Cookies"]
    if not (source / "Local State").is_file() or not (source / "Partitions/notion/Network/Cookies").is_file():
        raise RuntimeError("Expected host Notion login files are missing")
    # Preflight every database before creating the destination. Never bypass a live file lock.
    for relative in databases:
        path = source / relative
        if path.is_file():
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Source database integrity check failed: " + relative)
    runtime.mkdir(parents=True)
    profile.mkdir()
    copied = []
    for relative in files:
        src = source / relative
        if not src.is_file():
            continue
        if src.is_symlink():
            raise RuntimeError("Profile snapshot does not follow file links")
        dst = profile / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        with dst.open("xb") as stream:
            stream.write(data)
        if hashlib.sha256(dst.read_bytes()).digest() != hashlib.sha256(data).digest():
            raise RuntimeError("Notion snapshot hash mismatch")
        copied.append(relative)
    for relative in databases:
        src = source / relative
        if not src.is_file():
            continue
        dst = profile / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("xb"):
            pass
        reader = sqlite3.connect(src.as_uri() + "?mode=ro", uri=True, timeout=2)
        writer = sqlite3.connect(dst, timeout=2)
        try:
            reader.backup(writer)
            if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Notion snapshot database integrity check failed")
        finally:
            writer.close()
            reader.close()
        copied.append(relative)
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({**owner, "initialSnapshotFiles": copied}, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "NOTION_PROFILE_READY", "existing": False, "profile": str(profile), "snapshotFiles": len(copied)}))


if __name__ == "__main__":
    main()
