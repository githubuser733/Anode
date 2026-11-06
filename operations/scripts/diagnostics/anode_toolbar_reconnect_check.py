"""Opt-in bounded real toolbar check; preserves the expected seat and stops at any failure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                          creationflags=subprocess.CREATE_NO_WINDOW, timeout=20, **kwargs)


def write_new(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def ghost_call(name, arguments):
    cfg = json.loads((ROOT / 'config/ghost-runtime.json').read_text('utf-8-sig'))
    exe = ROOT / cfg['mcpExecutable']
    digest = next(row['sha256'] for row in cfg['executableHashes'] if row['relativePath'] == cfg['mcpExecutable'])
    if hashlib.sha256(exe.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Ghost binary changed')
    env = os.environ.copy()
    env.update(GHOST_FOCUS_POLICY='background', GHOST_FOCUS_LOCK='on')
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'Anode toolbar validation', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}]
    process = run([str(exe)], input='\n'.join(json.dumps(row) for row in requests) + '\n', env=env)
    if process.returncode:
        raise RuntimeError(f'Ghost exit {process.returncode}; no retry')
    response = next(row for line in process.stdout.splitlines() if (row := json.loads(line)).get('id') == 2)
    if response.get('error') or response.get('result', {}).get('isError'):
        raise RuntimeError(f'Ghost tool failed: {response}')
    payload = json.loads(next(item['text'] for item in response['result']['content'] if item['type'] == 'text'))
    if not payload.get('ok'):
        raise RuntimeError(f'Ghost did not succeed: {payload}')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=int, required=True)
    parser.add_argument('--daemon-pid', type=int, required=True)
    parser.add_argument('--window-handle', type=int, required=True)
    parser.add_argument('--cycles', type=int, choices=range(1, 4), default=3)
    parser.add_argument('--out-directory', type=Path, required=True)
    args = parser.parse_args()
    args.out_directory.mkdir(exist_ok=False)
    cfg = json.loads((ROOT / 'config/anode-local-mcp.json').read_text('utf-8-sig'))
    exe = ROOT / cfg['executable']
    if hashlib.sha256(exe.read_bytes()).hexdigest() != cfg['sha256']:
        raise RuntimeError('Active Anode binary changed')
    log = ROOT / cfg['stateDirectory'] / 'anode.log'

    def status():
        result = run([str(exe), '--channel', cfg['channel'], 'status', '--json'])
        if result.returncode:
            raise RuntimeError('Anode status unavailable; no start or retry')
        return json.loads(result.stdout)

    summary = {'result': 'TOOLBAR_RECONNECT_FAIL', 'expectedSession': args.session,
               'cycles': [], 'stopSeatCalls': 0, 'keyboardInputCalls': 0}
    try:
        for cycle in range(1, args.cycles + 1):
            before = status()
            write_new(args.out_directory / f'{cycle}-before.json', before)
            if (before.get('state') != 'ready' or before.get('session') != args.session
                    or not before.get('viewOnly') or before.get('lease', {}).get('ownerAgentId')):
                raise RuntimeError('Expected ready, unleased, view-only seat required')
            # Pin UIA to a verified native window and its direct toolbar children.
            # This avoids walking the entire Timeline or relying on posted clicks.
            helper = [str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                      str(Path(__file__).with_name('Invoke-AnodeToolbarReconnect.ps1')),
                      '-DaemonPid', str(args.daemon_pid), '-WindowHandle', str(args.window_handle)]
            inspected = run(helper + ['-InspectOnly'])
            if inspected.returncode:
                raise RuntimeError('Toolbar inspection failed: ' + inspected.stderr)
            found = json.loads(inspected.stdout)
            write_new(args.out_directory / f'{cycle}-found.json', found)
            if found.get('matches') != 1 or found.get('pid') != args.daemon_pid or found.get('name') != 'Reconnect':
                raise RuntimeError('Reconnect toolbar target is not unique or belongs to another process')
            offset = log.stat().st_size
            began = time.monotonic()
            dispatched = run(helper)
            if dispatched.returncode:
                raise RuntimeError('Toolbar dispatch failed: ' + dispatched.stderr)
            action = json.loads(dispatched.stdout)
            write_new(args.out_directory / f'{cycle}-action.json', action)
            if not action.get('focus_preserved') or not action.get('cursor_preserved'):
                raise RuntimeError('Reconnect action did not preserve parent input; no retry')
            samples = []
            while time.monotonic() - began < 30:
                current = status()
                samples.append({'elapsedSeconds': round(time.monotonic() - began, 3), 'state': current.get('state'),
                                'session': current.get('session'), 'login': current.get('rememberedLogin')})
                with log.open('rb') as handle:
                    handle.seek(offset)
                    new_log = handle.read().decode('utf-8-sig', errors='replace')
                if (current.get('state') in ('error', 'logon-error', 'stopped', 'stopping')
                        or current.get('session') not in (None, args.session)
                        or current.get('rememberedLogin', {}).get('state') == 'awaiting-user'):
                    write_new(args.out_directory / f'{cycle}-failure.json', {'status': current, 'samples': samples, 'log': new_log})
                    raise RuntimeError('Reconnect failed or required user credentials; no retry')
                if ('[connecting] Reconnecting the viewer...' in new_log and '[ready]' in new_log
                        and current.get('state') == 'ready' and current.get('session') == args.session
                        and current.get('rememberedLogin', {}).get('state') == 'reused'
                        and current.get('seat', {}).get('pid') == before.get('seat', {}).get('pid')):
                    write_new(args.out_directory / f'{cycle}-after.json', {'status': current, 'samples': samples, 'log': new_log})
                    summary['cycles'].append({'cycle': cycle, 'session': current['session'],
                                              'hostPid': current['seat']['pid'], 'credential': 'reused',
                                              'seconds': round(time.monotonic() - began, 3)})
                    print(json.dumps(summary['cycles'][-1]), flush=True)
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError('No completed toolbar reconnect observed within 30 seconds; do not replay')
        summary['result'] = 'TOOLBAR_RECONNECT_PASS'
    except Exception as error:
        summary['error'] = str(error)
    finally:
        write_new(args.out_directory / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary['result'] == 'TOOLBAR_RECONNECT_PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
