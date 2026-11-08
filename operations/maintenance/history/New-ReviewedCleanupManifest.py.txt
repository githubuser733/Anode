"""Read-only inventory of specifically selected obsolete outputs; never deletes files."""
import csv
import hashlib
import json
import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent
output = root / "artifacts/maintenance-cleanup-20261031"
groups = {
    "BgDesk retired files": [
        "work/bgdesk-taskbar-20261031", "work/bgdesk-viewer-fix-20261031",
        "work/releases/bgdesk-v2.0.0", "work/sources/bgdesk-v2.0.0",
        "work/project-bgdesk-grokbot-20261031", "work/project-bgdesk-scheduled-grokbot-20261031",
        "work/recheck-20261031-0206/bgdesk",
        "scripts/Start-BgDesk-WithSessionBootstrap.ps1", "scripts/Start-BgDesk-ViewerFix.ps1",
        "scripts/Start-BgDesk-Scheduled.ps1", "scripts/internal/Run-BgDeskSession.ps1",
        "scripts/Verify-BgDeskViewerFix.ps1", "launchers/BgDesk-Agent.lnk", "launchers/BgDesk-Start-Fixed.lnk",
    ],
    "Superseded Anode build outputs": [
        "work/anode-timeline-20261031/draft", "work/anode-timeline-20261031/native-debug-001",
        "work/anode-timeline-20261031/native-debug-002", "work/anode-timeline-20261031/publish-001",
        "work/anode-timeline-20261031/publish-002", "work/anode-timeline-20261031/publish-003",
        "work/anode-timeline-20261031/source/src/Anode/bin", "work/anode-timeline-20261031/source/src/Anode/obj",
    ],
    "Already extracted SDK download": ["work/deps/dotnet-sdk-10.0.403-win-x64.zip"],
}
protected = [
    "work/anode-timeline-20261031/publish-004", "work/anode-timeline-20261031/original-source.zip",
    "work/deps/dotnet-10.0.403", "work/deps/dotnet-home", "work/deps/nuget-packages",
    "work/releases/anode-v0.11.3", "work/recheck-20261031-shared", "work/grokbot-multiopen",
    "work/anode-timeline-session8-20261031", "artifacts", "config",
]
targets, files, directories = [], [], []
def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()

for group, paths in groups.items():
    for relative in paths:
        lexical = root / relative
        absolute = lexical.resolve(strict=True)
        assert absolute == lexical and absolute != root and root in absolute.parents, absolute
        assert not any(absolute == root / p or root / p in absolute.parents for p in protected), absolute
        assert not any(absolute == Path(t["path"]) or absolute in Path(t["path"]).parents or Path(t["path"]) in absolute.parents for t in targets)
        members = []
        todo = [absolute]
        while todo:
            item = todo.pop()
            assert not item.is_symlink() and not item.is_junction(), f"Reparse point: {item}"
            assert not any("backup" in part.lower() or "checkpoint" in part.lower() for part in item.relative_to(root).parts), f"Recovery material: {item}"
            if item.is_dir():
                directories.append(str(item)); todo.extend(item.iterdir())
            else:
                stat = item.stat()
                entry = {"path": str(item), "bytes": stat.st_size, "mtimeNs": stat.st_mtime_ns, "sha256": sha256(item), "target": str(absolute)}
                members.append(entry); files.append(entry)
        targets.append({"group": group, "path": str(absolute), "kind": "directory" if absolute.is_dir() else "file", "files": len(members), "bytes": sum(f["bytes"] for f in members)})

manifest = {"scope": str(root), "status": "AWAITING_USER_PASSPHRASE", "mode": "RecycleBinOnly", "targets": targets,
            "fileCount": len(files), "totalBytes": sum(f["bytes"] for f in files), "files": sorted(files, key=lambda x: x["path"]),
            "directories": sorted(directories), "protectedPaths": [str(root / p) for p in protected],
            "systemChangesProposed": {"clearAfterBackup": ["HKCU\\Environment\\UserInitMprLogonScript", "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\BgDeskAgent"],
                                      "disableTasksMatching": "AgentWindowsOperation-BgDesk-*", "credentials": "untouched"}}
with (output / "cleanup-manifest.json").open("x", encoding="utf-8") as f: json.dump(manifest, f, ensure_ascii=False, indent=2)
with (output / "cleanup-all-files.csv").open("x", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["path", "bytes", "mtimeNs", "sha256", "target"]); writer.writeheader(); writer.writerows(manifest["files"])
lines = ["拟移入回收站的完整顶层清单（尚未执行）", "作用域：" + str(root), "所有目标均已解析为绝对路径，位于项目内，且不是工作区根、驱动器根或其父目录；无重解析点。", ""]
for n, target in enumerate(targets, 1):
    lines += [f"{n}. {target['path']}", f"   {target['files']} 文件，{target['bytes']} 字节；{target['group']}"]
lines += ["", f"合计：{len(targets)} 个顶层目标，{len(files)} 个文件，{manifest['totalBytes']} 字节。", "全部目录和逐文件绝对路径见 cleanup-manifest.json 与 cleanup-all-files.csv。", "回收站仍占磁盘空间，不会自动清空。", "", "另拟在保留备份后停用两个 BgDesk 登录启动值及 9 个 BgDesk 按需任务；凭据、当前配置、登录数据、历史和备份均保留。"]
with (output / "cleanup-targets.txt").open("x", encoding="utf-8-sig") as f: f.write("\n".join(lines) + "\n")
print("\n".join(lines))
print("CLEANUP_MANIFEST_ONLY_OK; no files deleted and no system settings changed")
