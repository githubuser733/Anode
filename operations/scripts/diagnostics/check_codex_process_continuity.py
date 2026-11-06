"""Check actual process continuity separately from Windows forced-repair events."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('watch', type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.watch.read_text('utf-8-sig').splitlines() if line.strip()]
    if not rows or any(not isinstance(row.get('originalCodexAlive'), bool) for row in rows):
        raise ValueError('Missing or invalid process-continuity samples')
    failed = [row for row in rows if not row['originalCodexAlive']]
    print(json.dumps({'result': 'OBSERVED_CODEX_RESTART' if failed else 'ORIGINAL_CODEX_SURVIVED_OBSERVATION',
                     'samples': len(rows), 'firstObservedMissing': failed[0]['atUtc'] if failed else None,
                     'begin': rows[0]['atUtc'], 'end': rows[-1]['atUtc'],
                     'scope': 'Only the captured interval; does not identify who requested exit.'}, ensure_ascii=True))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
