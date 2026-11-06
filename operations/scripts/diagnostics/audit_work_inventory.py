"""Read-only logical size inventory; never follows reparse points or opens file contents."""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import stat
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='A new JSON file inside project artifacts')
    args = parser.parse_args()
    output = args.out.absolute()
    artifacts = (ROOT / 'artifacts').resolve(strict=True)
    if not output.parent.resolve(strict=True).is_relative_to(artifacts) or output.exists():
        raise ValueError('Output must be a new file inside existing project artifacts')
    work = WORK.resolve(strict=True)
    if work != WORK or work == ROOT or work == Path(work.anchor):
        raise ValueError('Unexpected work root')
    groups = defaultdict(lambda: {'files': 0, 'bytes': 0, 'directories': 0, 'reparsePoints': 0, 'errors': 0, 'hardlinkedFiles': 0})
    problems = []
    skipped_links = []
    start = time.time()

    def add(group, field, value=1):
        groups[group][field] += value

    def problem(path, error, ancestors):
        problems.append({'path': str(path), 'error': str(error)})
        for group in ancestors: add(group, 'errors')

    with os.scandir(work) as scan:
        top_entries = sorted(list(scan), key=lambda entry: entry.name.casefold())
    for top in top_entries:
        path = Path(top.path)
        pending = [(path, [str(path)])]
        while pending:
            item, parents = pending.pop()
            try:
                info = item.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    skipped_links.append(str(item))
                    for group in parents: add(group, 'reparsePoints')
                    continue
                if stat.S_ISDIR(info.st_mode):
                    for group in parents: add(group, 'directories')
                    with os.scandir(item) as scan:
                        children = list(scan)
                    for child in children:
                        child_path = Path(child.path)
                        child_parents = parents if item != path else [str(path), str(child_path)]
                        pending.append((child_path, child_parents))
                elif stat.S_ISREG(info.st_mode):
                    for group in parents:
                        add(group, 'files')
                        add(group, 'bytes', info.st_size)
                        if info.st_nlink > 1: add(group, 'hardlinkedFiles')
                else:
                    problem(item, 'not a regular file or directory', parents)
            except OSError as error:
                problem(item, error, parents)
        print(f"SCANNED {top.name} files={groups[str(path)]['files']} bytes={groups[str(path)]['bytes']} errors={groups[str(path)]['errors']}", flush=True)

    top_names = {str(Path(item.path)) for item in top_entries}
    rows = [{'path': path, 'depth': 1 if path in top_names else 2, **values} for path, values in groups.items()]
    rows.sort(key=lambda row: (-row['bytes'], row['path'].casefold()))
    result = {'scope': str(work), 'createdUnix': time.time(), 'elapsedSeconds': round(time.time() - start, 3),
              'logicalBytes': sum(row['bytes'] for row in rows if row['depth'] == 1),
              'files': sum(row['files'] for row in rows if row['depth'] == 1),
              'reparsePointsSkipped': skipped_links, 'errors': problems, 'entries': rows,
              'note': 'Logical lengths; hardlinks, compression and excluded reparse targets may affect physical usage.'}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(f"INVENTORY_DONE files={result['files']} logicalBytes={result['logicalBytes']} linksSkipped={len(skipped_links)} errors={len(problems)} output={output}", flush=True)
    return 0 if not problems else 1


if __name__ == '__main__':
    raise SystemExit(main())
