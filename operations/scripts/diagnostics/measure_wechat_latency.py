"""Read existing notice receipts and optionally profile authorized read-only stages."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/wechat_monitor'))
from core import configuration
from resident import DesktopNotifier
from clients import AlphaReader


def utc_ms(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


def summary(values):
    if not values:
        return None
    return {'samples': len(values), 'minimumMs': round(min(values), 2),
            'medianMs': round(statistics.median(values), 2), 'maximumMs': round(max(values), 2)}


def measure(profile=False):
    cfg = configuration()
    state = ROOT / cfg['resident']['stateDirectory']
    database = state / 'monitor.sqlite3'
    run_root = (ROOT / cfg['resident']['runsDirectory']).resolve()
    ids = [g['id'] for g in cfg['sources']]
    marks = ','.join('?' for unused in ids)
    samples, missing = [], 0
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows = db.execute(f'''SELECT m.source,m.body,m.first_seen,n.id,n.created,r.detail
          FROM messages m JOIN notice_items i ON i.source=m.source AND i.message_id=m.id
          JOIN notices n ON n.id=i.notice_id JOIN receipts r ON r.notice_id=n.id
          WHERE m.source IN ({marks}) AND r.state='shown'
          AND r.seq=(SELECT MAX(r2.seq) FROM receipts r2 WHERE r2.notice_id=n.id)
          ORDER BY n.created DESC LIMIT 200''', ids).fetchall()
        notice_values = {}
        for row in rows:
            detail = json.loads(row['detail'])
            receipt = Path(detail.get('receipt', '')).resolve()
            if not receipt.is_relative_to(run_root) or not receipt.is_file():
                missing += 1
                continue
            events = [json.loads(line) for line in receipt.read_text('utf-8').splitlines() if line.strip()]
            shown = [event for event in events if event.get('state') == 'shown']
            ready = [event for event in events if event.get('state') == 'ready']
            if not shown or not ready:
                missing += 1
                continue
            shown_at, ready_at = utc_ms(shown[0]['atUtc']), utc_ms(ready[0]['atUtc'])
            message = json.loads(row['body'])
            sample = {'notice': row['id'], 'group': message['source_name'],
                      'messageAt': message['time_ms'], 'pollCutoffAt': row['first_seen'],
                      'flushCheckAt': row['created'], 'helperReadyAt': round(ready_at, 3),
                      'osShownAt': round(shown_at, 3),
                      'messageTimestampToShownMs': round(shown_at - message['time_ms'], 2),
                      'pollCutoffToShownMs': round(shown_at - row['first_seen'], 2),
                      'queueBeforeFlushMs': row['created'] - row['first_seen'],
                      'flushCheckToHelperReadyMs': round(ready_at - row['created'], 2),
                      'helperReadyToShownMs': round(shown_at - ready_at, 2),
                      'flushCheckToShownMs': round(shown_at - row['created'], 2)}
            samples.append(sample)
            notice_values[row['id']] = sample['flushCheckToShownMs']
    result = {'result': 'EXISTING_REAL_RECEIPTS_MEASURED', 'samples': samples, 'missingReceiptRows': missing,
              'messageTimestampToShown': summary([s['messageTimestampToShownMs'] for s in samples]),
              'pollCutoffToShown': summary([s['pollCutoffToShownMs'] for s in samples]),
              'flushCheckToShown': summary(list(notice_values.values())),
              'limits': ['Message timestamp is not the measured local arrival time.',
                         'first_seen is the poll cutoff taken before the group reads, not exact detection time.',
                         'Receipt atUtc/shown is the OS event, not human reading confirmation.',
                         '60 second notification cooldown can defer burst hits.'],
              'sendCalls': 0, 'notificationCalls': 0, 'modelCalls': 0}
    if profile:
        prechecks, reads = [], []
        for unused in range(5):
            start = time.perf_counter()
            DesktopNotifier(cfg, ROOT).ready()
            prechecks.append((time.perf_counter() - start) * 1000)
        reader = AlphaReader(cfg)
        reader.verify_sources()
        for unused in range(6):
            start = time.perf_counter()
            before = int(time.time() * 1000)
            for source in cfg['sources']:
                reader.messages(source, cfg['maxPollMessagesPerGroup'], after_ms=before - 300000,
                                before_ms=before, require_complete=True)
            reads.append((time.perf_counter() - start) * 1000)
        result['readOnlyStageProfile'] = {'anodeReadyCheck': summary(prechecks), 'twoGroupReads': summary(reads),
                                        'alphaReadCalls': reader.calls, 'notificationCalls': 0}
    directory = ROOT / 'artifacts' / ('wechat-latency-assessment-20261105-' + uuid.uuid4().hex[:8])
    directory.mkdir()
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    return {**result, 'evidence': str(directory / 'result.json')}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', action='store_true', help='Additional bounded local read-only calls, no popup.')
    args = parser.parse_args()
    print(json.dumps(measure(args.profile), ensure_ascii=True))
