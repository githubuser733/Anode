"""Create an initial profile without replacing existing files or printing credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parent.parent


def refresh_trae_config(config, runtime, profile):
    """Read main preferences each launch; preserve replaced child files as backups."""
    records = []
    for relative in config.get("refreshConfigFiles", []):
        source = Path(config["sourceProfile"]) / relative
        if not source.is_file():
            continue
        data = source.read_bytes()
        if relative == "User/settings.json":
            settings = json.loads(data.decode("utf-8-sig"))
            settings.update(config.get("settingsOverrides", {}))
            data = (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        target = profile / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == data:
            continue
        if target.exists():
            original = target.read_bytes()
            backup = runtime / "config-backups" / (uuid.uuid4().hex + "-" + target.name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open("xb") as stream:
                stream.write(original)
            if backup.read_bytes() != original:
                raise RuntimeError("Configuration backup verification failed")
        temporary = target.with_name(target.name + ".new-" + uuid.uuid4().hex)
        with temporary.open("xb") as stream:
            stream.write(data)
        temporary.replace(target)
        records.append({"source": str(source), "target": str(target), "sha256": hashlib.sha256(data).hexdigest()})
    if records:
        history = runtime / "config-sync-history"
        history.mkdir(exist_ok=True)
        with (history / (uuid.uuid4().hex + ".json")).open("x", encoding="utf-8") as stream:
            json.dump(records, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("app", choices=("trae", "opencode"))
    args = parser.parse_args()
    config = json.loads((ROOT / "config/multiopen-apps.json").read_text(encoding="utf-8"))["apps"][args.app]
    runtime = ROOT / config["runtime"]
    marker = runtime / "profile-owner.json"
    profile = runtime / ("profile" if args.app == "trae" else "desktop")
    owner = {"app": args.app, "runtime": str(runtime), "profile": str(profile), "source": config["sourceProfile"]}
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if any(previous.get(k) != v for k, v in owner.items()):
            raise RuntimeError("Profile ownership mismatch")
        if not profile.is_dir():
            raise RuntimeError("Owned profile missing")
        if args.app == "trae":
            refresh_trae_config(config, runtime, profile)
        print(json.dumps({"app": args.app, "profilePrepared": True, "existing": True}))
        return
    if runtime.exists():
        raise RuntimeError("Unowned/incomplete runtime exists; no files will be overwritten")
    runtime.mkdir(parents=True)
    profile.mkdir()
    source = Path(config["sourceProfile"])
    copied = []
    for relative in config["seedFiles"]:
        src = source / relative
        if not src.is_file():
            continue
        dst = profile / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        with dst.open("xb") as stream:
            stream.write(data)
        if hashlib.sha256(dst.read_bytes()).digest() != hashlib.sha256(data).digest():
            raise RuntimeError("Snapshot copy verification failed")
        copied.append(relative)
    for relative in config.get("seedDatabases", []):
        src = source / relative
        if not src.is_file():
            continue
        dst = profile / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("xb"):
            pass
        reader = sqlite3.connect(src.as_uri() + "?mode=ro", uri=True, timeout=5)
        writer = sqlite3.connect(dst, timeout=5)
        try:
            reader.backup(writer)
            if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot database failed quick_check")
        finally:
            writer.close()
            reader.close()
        copied.append(relative)
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({**owner, "initialSnapshotFiles": copied}, stream, ensure_ascii=False, indent=2)
    if args.app == "trae":
        refresh_trae_config(config, runtime, profile)
    print(json.dumps({"app": args.app, "profilePrepared": True, "existing": False, "snapshotFiles": len(copied)}))


if __name__ == "__main__":
    main()
