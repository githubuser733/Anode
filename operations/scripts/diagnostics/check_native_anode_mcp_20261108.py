"""Compare published native MCP definitions to the previous verified executable; stdin EOF only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

OPS = Path(__file__).resolve().parents[2]
EVIDENCE = OPS / 'artifacts/anode-slim-20261108'


def discover(executable):
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {},
            'clientInfo': {'name': 'native-entry-verification', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
    ]
    process = subprocess.Popen([str(executable), '--channel', 'main', 'mcp'], cwd=OPS,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW, text=True, encoding='utf-8')
    try:
        stdout, stderr = process.communicate(''.join(json.dumps(row) + '\n' for row in requests), timeout=45)
    except subprocess.TimeoutExpired:
        process.kill()  # Only this check's stdin-closed MCP child; never a desktop host.
        process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f'MCP exited {process.returncode}: {stderr}')
    replies = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    return next(row['result']['tools'] for row in replies if row.get('id') == 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=EVIDENCE / 'native-mcp-comparison.json')
    args = parser.parse_args()
    report = args.out.resolve()
    if not report.is_relative_to(EVIDENCE.resolve()) or report.exists():
        raise ValueError('Use a new report path inside the current evidence directory')
    active = json.loads((OPS / 'config/anode-local-mcp.json').read_text('utf-8-sig'))
    prior = json.loads((OPS / 'config/anode-previous.json').read_text('utf-8-sig'))
    previous = OPS / prior['executable']
    current = OPS / 'work/anode-current/anode.exe'
    assert previous.resolve() != current.resolve(), 'Previous and current runtime must be different'
    for executable, manifest in ((previous, prior), (current, active)):
        with executable.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == manifest['sha256'], 'Runtime hash differs'
    old_tools, new_tools = discover(previous), discover(current)
    assert old_tools == new_tools and len(new_tools) == 41, 'MCP tools or definitions differ'
    with current.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    with report.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'NATIVE_MCP_DEFINITIONS_IDENTICAL', 'count': len(new_tools),
                   'currentExecutable': str(current), 'previousExecutable': str(previous),
                   'sha256': digest, 'normalEofExit': True, 'seatStarted': False}, stream, indent=2)
    print('NATIVE_MCP_DEFINITIONS_IDENTICAL tools=41 normalEofExit=true seatStarted=false')


if __name__ == '__main__':
    main()
