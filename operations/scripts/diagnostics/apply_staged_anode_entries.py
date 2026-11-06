"""Validate staged compatibility updates; install only after current-batch approval."""
import argparse
import json
from pathlib import Path

import win32com.client

import recycle_approved_versions_batch as batch
import recycle_work_builds_20261106 as checks

ROOT = Path(__file__).resolve().parents[2]
AUDIT = batch.AUDIT
TASK_BACKUP = AUDIT / 'legacy-smoke-task-before.xml'
TASK_BACKUP_HASH = 'e7276bd7d362bd2688ff650b39c565b41f27d3b1080b1c85b7cb9cb6bd8b372f'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if checks.digest(batch.MANIFEST) != batch.EXPECTED_MANIFEST:
        raise RuntimeError('Approved cleanup manifest changed')
    manifest = batch.read_json(batch.MANIFEST)
    migration = batch.read_json(AUDIT / 'migration-manifest.json')
    if len(migration['files']) != 4:
        raise RuntimeError('Unexpected migration count')
    for item in migration['files']:
        source, backup, staged = (Path(item[k]) for k in ('source', 'backup', 'staged'))
        if (source.parent != ROOT / 'scripts' or backup.parent != AUDIT / 'migration-original'
                or staged.parent != AUDIT / 'migration-staged'):
            raise RuntimeError('Unexpected migration paths')
        if (checks.digest(source) != item['originalSha256'] or checks.digest(backup) != item['originalSha256']
                or checks.digest(staged) != item['stagedSha256']):
            raise RuntimeError(f'Migration inputs changed: {source}')
    if checks.digest(TASK_BACKUP) != TASK_BACKUP_HASH:
        raise RuntimeError('Legacy task backup changed')
    service = win32com.client.Dispatch('Schedule.Service')
    service.Connect()
    task = service.GetFolder('\\').GetTask(batch.OLD_TASK)
    if (not task.Enabled or task.State == 4 or task.Definition.Triggers.Count != 0
            or task.Xml.encode('utf-8') != TASK_BACKUP.read_bytes()):
        raise RuntimeError('Legacy task changed, is running, or gained a trigger')
    print('MIGRATION_PRECHECK_OK files=4 taskEnabled=true triggers=0 backupHashes=true', flush=True)
    if not args.execute:
        print('VALIDATION_ONLY; no source or task was modified', flush=True)
        return
    receipt = batch.read_json(batch.RECEIPT)
    if (not receipt.get('activeUserReplyVerified') or receipt.get('manifestSha256') != batch.EXPECTED_MANIFEST
            or receipt.get('strategy') != manifest['entryStrategy']):
        raise RuntimeError('Current user approval missing or mismatched')
    originals = AUDIT / 'migration-installed-originals'
    aborted = AUDIT / 'migration-aborted-staged'
    result_path = AUDIT / 'migration-install-result.json'
    if originals.exists() or aborted.exists() or result_path.exists():
        raise RuntimeError('Migration already attempted; refusing replay')
    originals.mkdir()
    moved = []
    prepared = []
    task_disabled = False
    try:
        for item in migration['files']:
            source, staged = Path(item['source']), Path(item['staged'])
            archived = originals / source.name
            temporary = source.with_name(source.name + '.migration-candidate-3d6a9c2b')
            if archived.exists() or temporary.exists() or source.resolve(strict=True) != source:
                raise RuntimeError('Unexpected source or destination while staging: ' + str(source))
            with staged.open('rb') as inp, temporary.open('xb') as out:
                out.write(inp.read())
            prepared.append(temporary)
            if checks.digest(temporary) != item['stagedSha256']:
                raise RuntimeError('Prepared candidate hash differs: ' + str(temporary))
            source.rename(archived)  # same-volume reversible rename; preserve original file
            moved.append(item)
            temporary.rename(source)
            if checks.digest(source) != item['stagedSha256'] or checks.digest(archived) != item['originalSha256']:
                raise RuntimeError('Installed source or retained original hash differs: ' + str(source))
            print('ENTRY_MIGRATED', source.name, flush=True)
        task.Enabled = False
        task_disabled = True
        if task.Enabled:
            raise RuntimeError('Old no-trigger fixture task remained enabled')
        result = {'status': 'MIGRATION_INSTALLED', 'files': migration['files'], 'renamedOriginals': str(originals),
                  'legacyTask': batch.OLD_TASK, 'legacyTaskDisabled': True,
                  'currentMonitorTasksModified': False, 'userApplicationsRestarted': False}
        with result_path.open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
        print('MIGRATION_INSTALLED entries=4 legacyFixtureTaskDisabled=true', flush=True)
    except Exception:
        if task_disabled:
            task.Enabled = True
        aborted.mkdir(exist_ok=False)
        for temporary in prepared:
            if temporary.exists():
                temporary.rename(aborted / temporary.name)
        for item in reversed(moved):
            source = Path(item['source'])
            archived = originals / source.name
            if source.exists():
                if checks.digest(source) != item['stagedSha256']:
                    raise RuntimeError(f'Concurrent source change prevents rollback: {source}')
                source.rename(aborted / source.name)
            if archived.exists():
                archived.rename(source)
            if checks.digest(source) != item['originalSha256']:
                raise RuntimeError(f'Rollback verification failed: {source}')
        raise


if __name__ == '__main__':
    main()
