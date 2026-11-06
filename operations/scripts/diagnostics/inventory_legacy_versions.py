"""Read-only inventory of work and artifacts, without following reparse points."""
import json
import os
from pathlib import Path
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def scan(root, skip):
    groups = {}
    links = []
    errors = []
    with os.scandir(root) as it:
        pending = [(Path(entry.path), []) for entry in it]
    while pending:
        path, parents = pending.pop()
        if path == skip:
            continue
        depth = len(path.relative_to(root).parts)
        keys = parents + ([str(path)] if depth <= 2 else [])
        if depth <= 2:
            groups.setdefault(str(path), {'files': 0, 'dirs': 0, 'bytes': 0, 'links': 0, 'hardlinks': 0, 'errors': 0})
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                links.append(str(path))
                for key in keys:
                    groups[key]['links'] += 1
            elif stat.S_ISDIR(info.st_mode):
                for key in keys:
                    groups[key]['dirs'] += 1
                with os.scandir(path) as children:
                    pending.extend((Path(entry.path), keys) for entry in children)
            elif stat.S_ISREG(info.st_mode):
                for key in keys:
                    groups[key]['files'] += 1
                    groups[key]['bytes'] += info.st_size
                    if info.st_nlink > 1:
                        groups[key]['hardlinks'] += 1
            else:
                raise OSError('unsupported item type')
        except OSError as error:
            errors.append({'path': str(path), 'error': str(error)})
            for key in keys:
                groups[key]['errors'] += 1
    rows = [{'path': path, 'depth': len(Path(path).relative_to(root).parts), **value} for path, value in groups.items()]
    rows.sort(key=lambda row: (-row['bytes'], row['path'].casefold()))
    top = [row for row in rows if row['depth'] == 1]
    return {'root': str(root), 'files': sum(row['files'] for row in top),
            'bytes': sum(row['bytes'] for row in top), 'linksSkipped': links, 'errors': errors, 'entries': rows}


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: inventory_legacy_versions.py <new report directory>')
    report = Path(sys.argv[1])
    if report.exists():
        raise SystemExit('Report directory already exists')
    report.mkdir(parents=False)
    start = time.monotonic()
    result = {'createdUnix': time.time(),
              'roots': [scan(ROOT / 'work', report), scan(ROOT / 'artifacts', report)],
              'note': 'Logical bytes only; reparse targets are not followed.'}
    result['elapsedSeconds'] = round(time.monotonic() - start, 2)
    with (report / 'inventory.json').open('x', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    for item in result['roots']:
        print(f"INVENTORIED {item['root']} files={item['files']} bytes={item['bytes']} links={len(item['linksSkipped'])} errors={len(item['errors'])}")
    print('REPORT', report)
    return 1 if any(item['errors'] for item in result['roots']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
