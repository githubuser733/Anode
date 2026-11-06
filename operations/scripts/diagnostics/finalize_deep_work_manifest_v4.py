"""Preserve the disabled BgDesk task target and narrow the historical source selection."""
import csv
import hashlib
import json
from pathlib import Path

import prepare_deep_work_cleanup as prepare

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'artifacts/work-deep-cleanup-audit-20261106-68e2d1c0'
PREVIOUS = BASE / 'v3/cleanup-manifest.json'
OUTPUT = BASE / 'v4'
PREVIOUS_SHA256 = '962634e1f91ec5ebdfd5879305413149dab9855958c30977fa40e7117e2d97f4'
HISTORICAL = ROOT / 'work/grokbot-comparison-20261031'
BACKUPS = BASE / 'v2/preserved-work-evidence'


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    if OUTPUT.exists() or digest(PREVIOUS) != PREVIOUS_SHA256:
        raise RuntimeError('Output exists or prior review manifest changed')
    old = json.loads(PREVIOUS.read_text(encoding='utf-8'))
    rows = []
    replaced = 0
    for row in old['targets']:
        if Path(row['path']) != HISTORICAL:
            rows.append(row)
            continue
        replaced += 1
        narrowed = prepare.inspect(HISTORICAL / 'source', 'historical-source-evidence')
        for item in narrowed['files']:
            backup = BACKUPS / Path(item['path']).relative_to(ROOT / 'work')
            if not backup.is_file() or digest(backup) != item['sha256']:
                raise RuntimeError(f'Preserved source differs: {backup}')
        narrowed['preservedCopyRoot'] = str(BACKUPS)
        rows.append(narrowed)
    if replaced != 1:
        raise RuntimeError('Unexpected source target count')
    result = {'status': 'AWAITING_CURRENT_BATCH_APPROVAL', 'scope': str(ROOT / 'work'), 'targets': rows,
              'targetCount': len(rows), 'fileCount': sum(x['fileCount'] for x in rows),
              'totalBytes': sum(x['bytes'] for x in rows), 'mode': 'WindowsRecycleBinOnly',
              'note': 'No target moved or deleted; protected Git data and the disabled task target are excluded.'}
    if (result['targetCount'], result['fileCount'], result['totalBytes']) != (58, 3945, 1006518492):
        raise RuntimeError('Final inventory differs from expected totals')
    OUTPUT.mkdir()
    with (OUTPUT / 'cleanup-manifest.json').open('x', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    with (OUTPUT / 'cleanup-targets.txt').open('x', encoding='utf-8') as out:
        out.write('完整回收目标；尚未回收。逐文件见 cleanup-files.csv。\n')
        for row in rows:
            out.write(f"{row['path']} | {row['fileCount']} 文件 | {row['bytes']} 字节 | {row['category']}\n")
        out.write(f"总计：{result['targetCount']} 项，{result['fileCount']} 文件，{result['totalBytes']} 字节。\n")
    with (OUTPUT / 'cleanup-files.csv').open('x', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=['target', 'category', 'path', 'size', 'mtimeNs', 'sha256'])
        writer.writeheader()
        for row in rows:
            for item in row['files']:
                writer.writerow({'target': row['path'], 'category': row['category'], **item})
    with (OUTPUT / 'cleanup-files.csv').open(encoding='utf-8-sig', newline='') as f:
        check = list(csv.DictReader(f))
    if len(check) != result['fileCount'] or sum(int(x['size']) for x in check) != result['totalBytes']:
        raise RuntimeError('CSV verification differs')
    print(f"FINAL_MANIFEST_VERIFIED targets={len(rows)} files={len(check)} bytes={result['totalBytes']} sha256={digest(OUTPUT / 'cleanup-manifest.json')} moved=0")


if __name__ == '__main__':
    main()
