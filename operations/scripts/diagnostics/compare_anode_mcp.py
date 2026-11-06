"""Compare tool discovery only. Never starts, reconnects or stops a desktop."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def discover(exe):
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'Anode compatibility check', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    proc = subprocess.run([str(exe), '--channel', 'remember-validation', 'mcp'],
        input='\n'.join(json.dumps(r) for r in requests) + '\n', capture_output=True,
        text=True, encoding='utf-8', timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode:
        raise RuntimeError(f'MCP discovery exit {proc.returncode}: {proc.stderr}')
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    return next(r['result']['tools'] for r in replies if r.get('id') == 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    cfg = json.loads((ROOT / 'config/anode-local-mcp.json').read_text('utf-8-sig'))
    active = ROOT / cfg['executable']
    assert hashlib.sha256(active.read_bytes()).hexdigest() == cfg['sha256'], 'Active binary hash mismatch'
    before, after = discover(active), discover(args.candidate.resolve(strict=True))
    assert before == after, 'MCP tool metadata or schema changed'
    result = {'result': 'ANODE_MCP_COMPATIBILITY_OK', 'tools': len(after),
              'candidateEqualsActive': True, 'desktopCalls': 0,
              'candidateSha256': hashlib.sha256(args.candidate.read_bytes()).hexdigest()}
    if args.output:
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, indent=2)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
