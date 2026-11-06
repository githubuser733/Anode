"""Check bounded samples of unified-tray logs for the observed repeated pipe error."""
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
import re

PIPE_ERROR = 'Controller error: 所有的管道范例都在使用中。'
LINE = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+[+-]\d\d:\d\d) (.*)$')


def sample(path, offset, length):
    with path.open('rb') as stream:
        stream.seek(offset)
        text = stream.read(length).decode('utf-8-sig', errors='replace')
    lines = text.splitlines()
    if offset and lines:
        lines = lines[1:]
    if lines:
        lines = lines[:-1]
    matches = []
    messages = Counter()
    for line in lines:
        match = LINE.match(line)
        if not match:
            continue
        messages[match.group(2)] += 1
        if match.group(2) == PIPE_ERROR:
            matches.append(datetime.fromisoformat(match.group(1)))
    seconds = (max(matches) - min(matches)).total_seconds() if len(matches) > 1 else 0
    return len(lines), messages[PIPE_ERROR], seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--max-errors-per-second', type=float, default=10.0)
    parser.add_argument('--bytes', type=int, default=262144)
    args = parser.parse_args()
    size = args.log.stat().st_size
    if size < 2 * args.bytes:
        raise SystemExit('Insufficient log data for a representative bounded sample')
    for label, offset in (('middle', size // 2), ('end', max(0, size - args.bytes))):
        total, errors, seconds = sample(args.log, offset, args.bytes)
        rate = (errors - 1) / seconds if seconds > 0 else 0
        print(f'{label} sample_lines={total} matching_errors={errors} seconds={seconds:.3f} errors_per_second={rate:.1f}')
        if rate > args.max_errors_per_second:
            print('LOG_FLOOD_DETECTED')
            return 1
    print('LOG_FLOOD_NOT_OBSERVED_IN_SAMPLES')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
