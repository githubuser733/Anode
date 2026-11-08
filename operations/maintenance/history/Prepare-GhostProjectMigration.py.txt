"""Inventory the approved Ghost relocation and unrelated retired trial files; never deletes."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--legacy-work', required=True)
parser.add_argument('--audit-directory', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
legacy = Path(args.legacy_work).absolute()
assert legacy.resolve(strict=True) == legacy and legacy.is_dir(), legacy
audit = Path(args.audit_directory).absolute()
assert root / 'artifacts' in audit.parents and not audit.exists(), audit
destination = root / 'work' / 'ghost'
assert root in destination.parents and not destination.exists(), destination


def scan(target):
    assert target.resolve(strict=True) == target, target
    files, directories = [], []
    pending = [target]
    while pending:
        current = pending.pop()
        st = current.lstat()
        assert not current.is_symlink() and not (getattr(st, 'st_file_attributes', 0) & 1024), f'Reparse point: {current}'
        if current.is_dir():
            directories.append(str(current))
            pending.extend(current.iterdir())
            continue
        digest = hashlib.sha256()
        with current.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        files.append({'path': str(current), 'bytes': st.st_size, 'mtimeNs': st.st_mtime_ns,
                      'sha256': digest.hexdigest()})
    return sorted(files, key=lambda x: x['path'].casefold()), sorted(directories)


ghost_names = [
    'ghost-portable-v0.23.4', 'ghost-source-archive-1',
    'ghost-v0.23.4-complete.zip.parts', 'ghost-v0.23.4-download',
    'ghost-bridge-port.json', 'ghost-src-v0.23.4.zip',
    'ghost-v0.23.4-api-download.zip', 'ghost-v0.23.4-complete.zip',
]
moves, migration_files = [], []
for name in ghost_names:
    source, dest = legacy / name, destination / name
    assert source.parent == legacy and source.exists(), source
    assert not dest.exists() and destination in dest.parents, dest
    files, dirs = scan(source)
    mapped = []
    for entry in files:
        entry = dict(entry)
        entry['destination'] = str(dest / Path(entry['path']).relative_to(source)) if source.is_dir() else str(dest)
        mapped.append(entry)
    migration_files.extend(mapped)
    moves.append({'source': str(source), 'destination': str(dest), 'isDirectory': source.is_dir(),
                  'fileCount': len(files), 'bytes': sum(x['bytes'] for x in files),
                  'directories': dirs, 'files': mapped})

retired = {
    'Trope failed test project': [
        'trope-built-3a1fbf0917fa', 'trope-source-3a1fbf0917fa', 'trope-trial-config',
        'trope-cua-main-20261030.zip', 'trope-bridge-port.json', 'trope-target-process.json',
    ],
    'MouseMux inactive download and inspection material (installed service excluded)': [
        'mousemux-installer-inspect', 'mousemux-payload-inspect',
        'mousemux-v3-setup-3.0.19-complete.exe.parts',
        'mousemux-v3-setup-3.0.19-verified.exe.small-parts',
        'mousemux-v3-setup-3.0.19-verified.exe', 'mousemux-v3-setup-3.0.19.exe',
    ],
}
cleanup_targets, cleanup_files, exclusions = [], [], []
for group, names in retired.items():
    for name in names:
        target = legacy / name
        if not target.exists():
            exclusions.append({'path': str(target), 'reason': 'Already absent; not a deletion target'})
            continue
        assert target.parent == legacy and target != legacy and target.parent != target, target
        files, dirs = scan(target)
        protected = [f['path'] for f in files if
                     any(part.casefold() in {'backup', 'backups', 'credentials', 'secrets'} for part in Path(f['path']).relative_to(target.parent).parts)
                     or Path(f['path']).suffix.casefold() in {'.pem', '.key', '.pfx', '.p12', '.kdbx'}]
        if protected:
            exclusions.append({'path': str(target), 'reason': 'Protected/recovery files detected', 'protectedFiles': protected})
            continue
        item = {'path': str(target), 'group': group, 'isDirectory': target.is_dir(),
                'fileCount': len(files), 'bytes': sum(x['bytes'] for x in files),
                'directories': dirs, 'files': files}
        cleanup_targets.append(item)
        cleanup_files.extend(dict(f, target=str(target)) for f in files)

audit.mkdir(parents=False, exist_ok=False)
plan = {'projectRoot': str(root), 'legacyWorkRoot': str(legacy), 'destinationRoot': str(destination),
        'auditDirectory': str(audit), 'status': 'PREPARED_NOT_MOVED', 'moves': moves,
        'fileCount': len(migration_files), 'totalBytes': sum(x['bytes'] for x in migration_files),
        'historicalArchive': 'Unchanged: artifacts/history-20261031',
        'compatibility': 'No registered Ghost MCP or Ghost shortcut references were found; no legacy links are planned.'}
cleanup = {'scope': str(legacy), 'mode': 'WindowsRecycleBinOnly', 'status': 'AWAITING_FRESH_USER_PASSPHRASE',
           'targets': cleanup_targets, 'files': cleanup_files, 'exclusions': exclusions,
           'fileCount': len(cleanup_files), 'totalBytes': sum(x['bytes'] for x in cleanup_files),
           'untouched': ['Anode processes, sessions, source and builds', 'Ghost and its historical evidence',
                         'Installed MouseMux service and installed application files',
                         'Shared SDK/NuGet directories, credentials, backups and business data']}
for name, data in [('migration-plan.json', plan), ('cleanup-plan.json', cleanup)]:
    with (audit / name).open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
with (audit / 'cleanup-all-files.csv').open('x', encoding='utf-8-sig', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=['path', 'bytes', 'mtimeNs', 'sha256', 'target'])
    writer.writeheader()
    writer.writerows(cleanup_files)
lines = ['拟移入 Windows 回收站的完整顶层清单；尚未执行', '限定来源目录：' + str(legacy),
         '所有目标为该来源目录的明确子项；已解析绝对路径，均不是工作区、驱动器根或其父目录；未发现重解析点。', '']
for index, target in enumerate(cleanup_targets, 1):
    lines += [f"{index}. {target['path']}", f"   {target['fileCount']} 文件；{target['bytes']} 字节；{target['group']}"]
lines += ['', f"总计：{len(cleanup_targets)} 个顶层目标，{cleanup['fileCount']} 个文件，{cleanup['totalBytes']} 字节。",
          '逐文件完整绝对路径及 SHA256：同目录 cleanup-all-files.csv。',
          '这里只回收旧测试材料；不会卸载当前 MouseMux 服务，不会清空回收站。',
          '执行前需要用户查看本批清单后，主动回复本批删除口令。']
with (audit / 'cleanup-targets.txt').open('x', encoding='utf-8-sig') as handle:
    handle.write('\n'.join(lines) + '\n')
print(json.dumps({'auditDirectory': str(audit), 'ghostTargets': len(moves),
                  'ghostFiles': plan['fileCount'], 'ghostBytes': plan['totalBytes'],
                  'cleanupTargets': len(cleanup_targets), 'cleanupFiles': cleanup['fileCount'],
                  'cleanupBytes': cleanup['totalBytes'], 'excluded': exclusions,
                  'status': 'INVENTORY_ONLY; no files moved or deleted'}, ensure_ascii=False))
