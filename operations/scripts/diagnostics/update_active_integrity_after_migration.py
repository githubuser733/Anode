"""Update active script hashes after the approved migration, preserving the prior record."""
import argparse
import hashlib
import json
from pathlib import Path

import recycle_approved_versions_batch as batch
import recycle_work_builds_20261106 as checks

ROOT = Path(__file__).resolve().parents[2]
AUDIT = batch.AUDIT
CURRENT = ROOT / 'artifacts/active-source-migration.json'
BEFORE = AUDIT / 'active-source-migration-before.json'
STAGED = AUDIT / 'active-source-migration-updated.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if checks.digest(batch.MANIFEST) != batch.EXPECTED_MANIFEST:
        raise RuntimeError('Cleanup manifest changed')
    receipt = batch.read_json(batch.RECEIPT)
    if not receipt.get('activeUserReplyVerified') or receipt.get('manifestSha256') != batch.EXPECTED_MANIFEST:
        raise RuntimeError('Current-batch authorization missing')
    batch.verify_migration()
    if BEFORE.exists() or STAGED.exists():
        raise RuntimeError('Integrity record migration was already attempted')
    records = batch.read_json(CURRENT)
    migration = batch.read_json(AUDIT / 'migration-manifest.json')
    by_source = {str(Path(row['source']).resolve()).casefold(): row for row in migration['files']}
    changes = []
    for entry in records:
        destination = str(Path(entry['destination']).resolve()).casefold()
        actual = checks.digest(entry['destination'])
        row = by_source.get(destination)
        if row:
            if (entry['destinationSha256'].casefold() != row['originalSha256']
                    or actual != row['stagedSha256'] or checks.digest(row['backup']) != row['originalSha256']):
                raise RuntimeError('Migration or original integrity record does not match: ' + entry['destination'])
            entry['destinationSha256'] = actual
            entry['change'] = ('2026-11-06: migrated reviewed entry to the current Anode manifest; '
                               'original script and previous integrity record were retained.')
            changes.append(Path(entry['destination']).name)
        elif actual != entry['destinationSha256'].casefold():
            raise RuntimeError('An unrelated active script hash changed: ' + entry['destination'])
    if sorted(changes) != sorted(['Verify-Shared-WorkBuddy.ps1', 'mcp_live_bridge.py']):
        raise RuntimeError('Unexpected set of integrity record changes: ' + repr(changes))
    before_hash = checks.digest(CURRENT)
    output = (json.dumps(records, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    after_hash = hashlib.sha256(output).hexdigest()
    print('INTEGRITY_PRECHECK_OK changedEntries=2 unrelatedEntriesVerified=true', flush=True)
    if not args.execute:
        print('VALIDATION_ONLY; no integrity record was changed', flush=True)
        return
    with STAGED.open('xb') as out:
        out.write(output)
    if checks.digest(STAGED) != after_hash:
        raise RuntimeError('Staged integrity record failed readback')
    CURRENT.rename(BEFORE)  # preserve the exact original; no content overwrite
    try:
        STAGED.rename(CURRENT)
        if checks.digest(CURRENT) != after_hash or checks.digest(BEFORE) != before_hash:
            raise RuntimeError('Installed integrity record readback failed')
    except Exception:
        if CURRENT.exists():
            failed = AUDIT / 'active-source-migration-failed-candidate.json'
            if failed.exists():
                raise RuntimeError('Failed candidate path already exists; manual recovery required')
            CURRENT.rename(failed)
        if BEFORE.exists():
            BEFORE.rename(CURRENT)
        raise
    result = {'status': 'INTEGRITY_RECORD_UPDATED', 'changedEntries': changes, 'beforeSha256': before_hash,
              'afterSha256': after_hash, 'previousRecord': str(BEFORE), 'unrelatedEntriesVerified': True}
    with (AUDIT / 'integrity-migration-result.json').open('x', encoding='utf-8') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print('INTEGRITY_RECORD_UPDATED changedEntries=2 priorRecordPreserved=true', flush=True)


if __name__ == '__main__':
    main()
