"""Local-only read-only dashboard HTTP service; no Alpha or WeChat calls."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

if __package__:
    from .data import BadQuery, DashboardData, now_ms, read_json, tail_rows
else:
    from data import BadQuery, DashboardData, now_ms, read_json, tail_rows

APP_ID = 'wechat-monitor-dashboard'
ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).resolve().parent / 'web'
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
LOG_LOCK = threading.Lock()


def log(root, event, **fields):
    path = Path(root) / 'artifacts/wechat-dashboard/events.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOG_LOCK, path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at_ms': now_ms(), 'event': event, **fields}, ensure_ascii=False) + '\n')


def desired(root):
    rows = tail_rows(Path(root) / 'artifacts/wechat-dashboard/control.jsonl')
    return bool(rows and rows[-1].get('enabled'))


def set_desired(root, value):
    path = Path(root) / 'artifacts/wechat-dashboard/control.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at_ms': now_ms(), 'enabled': value}) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def existing(port):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
    try:
        connection.request('GET', '/api/health')
        response = connection.getresponse()
        value = json.loads(response.read(4096))
        return value if response.status == 200 and value.get('app') == APP_ID else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, data):
        self.data, self.started_at = data, now_ms()
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = 'LocalDashboard/1'
    sys_version = ''

    def log_message(self, format, *args):
        pass  # Polling is intentionally not appended as a request log.

    def authorized(self):
        origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        hosts = {origin.removeprefix('http://') for origin in origins}
        if self.headers.get('Host') not in hosts:
            self.error(403, 'LOCAL_HOST_REQUIRED')
            return False
        if self.headers.get('Origin') and self.headers['Origin'] not in origins:
            self.error(403, 'CROSS_ORIGIN_DENIED')
            return False
        return True

    def respond(self, code, body, kind='application/json; charset=utf-8', download=None):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass

    def json(self, value, code=200):
        self.respond(code, json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))

    def error(self, code, value):
        self.json({'error': value}, code)

    def do_POST(self):
        self.error(405, 'READ_ONLY_DASHBOARD')

    do_PUT = do_DELETE = do_PATCH = do_POST

    def do_GET(self):
        if not self.authorized():
            return
        request = urlsplit(self.path)
        try:
            try:
                values = parse_qs(request.query, keep_blank_values=True, max_num_fields=8)
            except ValueError:
                raise BadQuery('INVALID_QUERY') from None
            path = request.path
            if path == '/api/health':
                self.json({'app': APP_ID, 'pid': os.getpid(), 'startedAt': self.server.started_at, 'readOnly': True})
            elif path == '/api/snapshot':
                self.json(self.server.data.snapshot(values))
            elif path == '/api/export.md':
                self.respond(200, self.server.data.export(values), 'text/markdown; charset=utf-8', 'wechat-keyword-records.md')
            elif re.fullmatch(r'/api/hit/[1-9][0-9]{0,14}', path):
                self.json(self.server.data.hit_detail(int(path.rsplit('/', 1)[1])))
            elif path.startswith('/api/report/'):
                identity = path.rsplit('/', 1)[1]
                self.respond(200, self.server.data.report(identity), 'text/markdown; charset=utf-8', identity + '.md')
            elif path in ('/', '/app.js', '/style.css', '/favicon.svg'):
                filename = 'index.html' if path == '/' else path[1:]
                kind = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8',
                        'style.css': 'text/css; charset=utf-8', 'favicon.svg': 'image/svg+xml'}[filename]
                self.respond(200, (ASSETS / filename).read_bytes(), kind)
            else:
                self.error(404, 'NOT_FOUND')
        except BadQuery as error:
            self.error(400, str(error))
        except FileNotFoundError:
            self.error(404, 'NOT_FOUND')
        except (sqlite3.Error, ValueError, KeyError, OSError) as error:
            log(self.server.data.root, 'read_error', error=type(error).__name__, route=request.path[:100])
            self.error(503, 'RECORDS_TEMPORARILY_UNAVAILABLE')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve', 'open', 'status', 'enable', 'disable'], nargs='?', default='serve')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    cfg = read_json(root / 'config/wechat-dashboard.json')
    if cfg['host'] != '127.0.0.1' or not 1024 <= cfg['port'] <= 65535:
        raise ValueError('DASHBOARD_MUST_USE_LOOPBACK')
    port = cfg['port']
    if args.command in ('enable', 'disable'):
        set_desired(root, args.command == 'enable')
        return {'enabled': desired(root)}
    running = existing(port)
    if args.command == 'status':
        return {'enabled': desired(root), 'running': bool(running), 'url': f'http://127.0.0.1:{port}/', 'server': running}
    if args.command == 'open':
        set_desired(root, True)
    if not desired(root):
        return {'result': 'DASHBOARD_DISABLED'}
    if running:
        if args.command == 'open':
            webbrowser.open(f'http://127.0.0.1:{port}/')
        return {'result': 'DASHBOARD_ALREADY_RUNNING'}
    try:
        server = DashboardServer(('127.0.0.1', port), DashboardData(root))
    except OSError:
        if existing(port):
            return {'result': 'DASHBOARD_ALREADY_RUNNING'}
        log(root, 'port_unavailable', port=port)
        raise
    log(root, 'server_started', pid=os.getpid(), port=port)
    def check_stop():
        while desired(root):
            time.sleep(1)
        server.shutdown()
    threading.Thread(target=check_stop, daemon=True).start()
    if args.command == 'open':
        threading.Timer(.4, lambda: webbrowser.open(f'http://127.0.0.1:{port}/')).start()
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()
        log(root, 'server_stopped', pid=os.getpid())
    return {'result': 'DASHBOARD_STOPPED'}


if __name__ == '__main__':
    try:
        value = main()
        if sys.stdout is not None:
            print(json.dumps(value, ensure_ascii=True))
    except Exception as error:
        try:
            log(ROOT, 'server_error', error=type(error).__name__)
        finally:
            if sys.stderr is not None:
                print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
