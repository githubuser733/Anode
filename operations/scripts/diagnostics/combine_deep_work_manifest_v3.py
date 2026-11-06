"""Expand a prepared cleanup list with three hash-backed historical source trees."""
import csv
import hashlib
import json
from pathlib import Path

import prepare_deep_work_cleanup as source

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'artifacts/work-deep-cleanup-audit-20261106-68e2d1c0'
PREVIOUS = BASE / 'cleanup-manifest.json'
BACKUPS = BASE / 'v2/preserved-work-evidence'
OUTPUT = BASE / 'v3'
PREVIOUS_SHA256 = 'b0e3570fc9281249845c545d0c0c331613a534f401983e11dba47e8288707f2c'
EXTRA = [
    ROOT / 'work/anode-timeline-20261031/source',
    ROOT / 'work/sources/anode-v0.11.3',
    ROOT / 'work/grokbot-comparison-20261031'
]


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    if OUTPUT.exists():
        raise RuntimeError('Final output already exists')
    if digest(PREVIOUS) != PREVIOUS_SHA256:
        raise RuntimeError('Earlier reviewed inventory changed')
    old = json.loads(PREVIOUS.read_text(encoding='utf-8'))
    if (old['targetCount'], old['fileCount'], old['totalBytes']) != (55, 3612, 997650523):
        raise RuntimeError('Earlier inventory totals differ')
    extra = [source.inspect(path, 'historical-source-evidence') for path in EXTRA]
    rows = old['targets'] + extra
    for row in rows:
        if row['category'] not in {'historical-script', 'historical-run-evidence', 'historical-source-evidence'}:
            continue
        for item in row['files']:
            copy = BACKUPS / Path(item['path']).relative_to(ROOT / 'work')
            if not copy.is_file() or digest(copy) != item['sha256']:
                raise RuntimeError(f'Preserved original differs: {copy}')
        row['preservedCopyRoot'] = str(BACKUPS)
    paths = [Path(x['path']) for x in rows]
    if len(set(paths)) != len(paths) or any(a != b and a in b.parents for a in paths for b in paths):
        raise RuntimeError('Target overlap')
    result = {'status': 'AWAITING_CURRENT_BATCH_APPROVAL', 'scope': str(ROOT / 'work'), 'targets': rows,
              'targetCount': len(rows), 'fileCount': sum(x['fileCount'] for x in rows),
              'totalBytes': sum(x['bytes'] for x in rows), 'mode': 'WindowsRecycleBinOnly',
              'note': 'No target moved or deleted; the unused Alpha documentation Git repository is excluded.'}
    if (result['targetCount'], result['fileCount'], result['totalBytes']) != (58, 3947, 1006522910):
        raise RuntimeError('Expanded inventory differs from expected totals')
    OUTPUT.mkdir()
    with (OUTPUT / 'cleanup-manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    with (OUTPUT / 'cleanup-targets.txt').open('x', encoding='utf-8') as handle:
        handle.write('完整回收目标；尚未回收。逐文件见 cleanup-files.csv。\n')
        for row in rows:
            handle.write(f"{row['path']} | {row['fileCount']} 文件 | {row['bytes']} 字节 | {row['category']}\n")
        handle.write(f"总计：{result['targetCount']} 项，{result['fileCount']} 文件，{result['totalBytes']} 字节。\n")
    with (OUTPUT / 'cleanup-files.csv').open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['target', 'category', 'path', 'size', 'mtimeNs', 'sha256'])
        writer.writeheader()
        for row in rows:
            for item in row['files']:
                writer.writerow({'target': row['path'], 'category': row['category'], **item})
    with (OUTPUT / 'cleanup-files.csv').open(encoding='utf-8-sig', newline='') as handle:
        check = list(csv.DictReader(handle))
    if len(check) != result['fileCount'] or sum(int(row['size']) for row in check) != result['totalBytes']:
        raise RuntimeError('Final CSV readback differs')
    print(f"FINAL_MANIFEST_VERIFIED targets={len(rows)} files={len(check)} bytes={result['totalBytes']} sha256={digest(OUTPUT / 'cleanup-manifest.json')} moved=0")


if __name__ == '__main__':
    main()
