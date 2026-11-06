"""Compare files in a retired test profile to the current Edge extension files."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / 'work/alpha-readonly-browser/session-7/Default/Extensions'
PRIMARY = Path.home() / 'AppData/Local/Microsoft/Edge/User Data/Default/Extensions'
OUTPUT = ROOT / 'artifacts/work-deep-cleanup-audit-20261106-68e2d1c0/edge-extension-comparison.json'
SENSITIVE_EXT = {'.pem', '.p12', '.pfx', '.key', '.db', '.sqlite'}
SENSITIVE_NAMES = {'.env', 'credentials.json', 'secrets.json', 'passwords.json'}


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    if OUTPUT.exists():
        raise RuntimeError('Comparison output already exists')
    results = []
    for folder in sorted(OLD.iterdir()):
        if not folder.is_dir():
            raise RuntimeError(f'Unexpected file at extension root: {folder}')
        primary = PRIMARY / folder.name
        source_files = sorted(x for x in folder.rglob('*') if x.is_file())
        size = sum(x.stat().st_size for x in source_files)
        sensitive = [str(x) for x in source_files if x.suffix.casefold() in SENSITIVE_EXT or x.name.casefold() in SENSITIVE_NAMES]
        result = {'path': str(folder), 'files': len(source_files), 'bytes': size,
                  'sensitiveNames': sensitive, 'identicalToPrimary': False}
        if primary.is_dir() and not sensitive:
            primary_files = sorted(x for x in primary.rglob('*') if x.is_file())
            if {x.relative_to(folder) for x in source_files} == {x.relative_to(primary) for x in primary_files}:
                identical = True
                for source in source_files:
                    current = primary / source.relative_to(folder)
                    a, b = source.stat(), current.stat()
                    if a.st_size != b.st_size or digest(source) != digest(current):
                        identical = False
                        break
                    aa, bb = source.stat(), current.stat()
                    if (a.st_size, a.st_mtime_ns, b.st_size, b.st_mtime_ns) != (aa.st_size, aa.st_mtime_ns, bb.st_size, bb.st_mtime_ns):
                        raise RuntimeError('Comparison input changed during read')
                result['identicalToPrimary'] = identical
        results.append(result)
    matched = [x for x in results if x['identicalToPrimary']]
    out = {'oldRoot': str(OLD), 'primaryRoot': str(PRIMARY), 'entries': results,
           'matchedDirectories': len(matched), 'matchedFiles': sum(x['files'] for x in matched),
           'matchedBytes': sum(x['bytes'] for x in matched),
           'note': 'Read-only comparison of files; it does not establish semantic equivalence of browser profiles.'}
    with OUTPUT.open('x', encoding='utf-8') as handle:
        json.dump(out, handle, ensure_ascii=False, indent=2)
    print(f"EDGE_COMPARISON_OK matchedDirs={out['matchedDirectories']} matchedFiles={out['matchedFiles']} matchedBytes={out['matchedBytes']}")
    for item in sorted(matched, key=lambda x: -x['bytes'])[:10]:
        print(f"LARGEST_MATCH files={item['files']} bytes={item['bytes']} {item['path']}")


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as error:
        print('EDGE_COMPARISON_FAILED:', error, file=sys.stderr)
        raise SystemExit(1)
