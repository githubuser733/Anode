"""Local read-only Alpha companion. Never starts another Alpha engine.

HTTP contract: docs/api/http-api.md at config.upstreamDocsCommit.
Windows v10 token format was verified against this machine's Alpha 0.3.5.
Only the HTTP API token is decrypted, in memory; no token is persisted here.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
UI = Path(__file__).resolve().parent / "ui"
APP_ID = "alpha-readonly-companion-v1"
PROJECT_ID = hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()[:16]
MAX_RESPONSE = 8 * 1024 * 1024
ROUTES = {
    "/api/accounts": ("/api/v1/accounts", set(), set()),
    "/api/sessions": ("/api/v1/sessions", {"account_id", "limit", "offset", "order"}, {"account_id"}),
    "/api/messages": ("/api/v1/messages", {"account_id", "username", "limit", "offset", "order"}, {"account_id", "username"}),
    "/api/search": ("/api/v1/search", {"account_id", "query", "page_size", "cursor"}, {"account_id", "query"}),
}


class ViewerError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def config():
    return json.loads((ROOT / "config/alpha-readonly.json").read_text("utf-8"))


def credentials(profile: Path):
    """Read only the existing Alpha HTTP config; fail closed on other formats."""
    import win32crypt
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        data = json.loads((profile / "http-api-v1.json").read_text("utf-8"))
        if not data["config"]["enabled"]:
            raise ViewerError("ALPHA_HTTP_DISABLED", 503)
        if data["config"].get("lan"):
            raise ViewerError("ALPHA_LOOPBACK_REQUIRED", 503)
        port = data["config"]["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ViewerError("ALPHA_CONFIG_INVALID", 503)
        local = json.loads((profile / "Local State").read_text("utf-8"))
        wrapped = base64.b64decode(local["os_crypt"]["encrypted_key"], validate=True)
        blob = base64.b64decode(data["token"], validate=True)
        if wrapped[:5] != b"DPAPI" or blob[:3] != b"v10":
            raise ViewerError("ALPHA_TOKEN_FORMAT_UNSUPPORTED", 503)
        key = win32crypt.CryptUnprotectData(wrapped[5:], None, None, None, 0)[1]
        token = AESGCM(key).decrypt(blob[3:15], blob[15:], None).decode("ascii")
        if not re.fullmatch(r"[\x21-\x7e]{32,256}", token):
            raise ViewerError("ALPHA_TOKEN_INVALID", 503)
        return port, token
    except ViewerError:
        raise
    except Exception:
        raise ViewerError("ALPHA_TOKEN_UNAVAILABLE", 503) from None


def validate_route(target: str):
    if len(target) > 8192:
        raise ViewerError("QUERY_TOO_LONG")
    url = urllib.parse.urlsplit(target)
    if url.scheme or url.netloc or url.fragment or url.path not in ROUTES:
        raise ViewerError("READ_ONLY_ROUTE_DENIED", 404)
    upstream, allowed, required = ROUTES[url.path]
    try:
        pairs = urllib.parse.parse_qsl(url.query, keep_blank_values=True,
                                      strict_parsing=True, max_num_fields=12)
    except ValueError:
        raise ViewerError("QUERY_INVALID") from None
    params = dict(pairs)
    if len(params) != len(pairs) or set(params) - allowed or required - set(params):
        raise ViewerError("QUERY_NOT_ALLOWED")
    for key, value in params.items():
        if not value or any(ord(c) < 32 for c in value):
            raise ViewerError("QUERY_INVALID")
        maximum = 1368 if key == "cursor" else 1024 if key == "username" else 512 if key == "query" else 256
        if len(value.encode("utf-8")) > maximum:
            raise ViewerError("QUERY_TOO_LONG")
        if key in {"limit", "page_size", "offset"}:
            cap = 2000000000 if key == "offset" else 100
            floor = 0 if key == "offset" else 1
            if not value.isascii() or not value.isdecimal() or not floor <= int(value) <= cap:
                raise ViewerError("PAGINATION_INVALID")
        if key == "order" and value not in {"asc", "desc"}:
            raise ViewerError("ORDER_INVALID")
    return upstream + ("?" + urllib.parse.urlencode(params) if params else "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AlphaClient:
    def __init__(self, credential_provider):
        self.credential_provider = credential_provider
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def read(self, target):
        route = validate_route(target)
        port, token = self.credential_provider()
        request = urllib.request.Request(f"http://127.0.0.1:{port}{route}",
                                         headers={"Authorization": "Bearer " + token,
                                                  "Accept": "application/json"}, method="GET")
        try:
            with self.opener.open(request, timeout=155) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ViewerError("ALPHA_RESPONSE_TOO_LARGE", 502)
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ViewerError("ALPHA_RESPONSE_INVALID", 502)
                return value
        except urllib.error.HTTPError as error:
            # Preserve the error code, never the upstream body, URLs or credential.
            code = "ALPHA_HTTP_" + str(error.code)
            try:
                data = json.loads(error.read(8192))
                candidate = data.get("code") or data.get("error", {}).get("code")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", candidate):
                    code = candidate
            except Exception:
                pass
            raise ViewerError(code, error.code if 400 <= error.code <= 599 else 502) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            raise ViewerError("ALPHA_UNAVAILABLE", 503) from None
        except (ValueError, UnicodeError):
            raise ViewerError("ALPHA_RESPONSE_INVALID", 502) from None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, client):
        self.client = client
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "AlphaReadOnly/1"

    def log_message(self, *args):
        pass  # Do not log private query text, conversation identifiers or headers.

    def reply(self, status, data, mime="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                raise ViewerError("HOST_DENIED", 403)
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in hosts}:
                raise ViewerError("ORIGIN_DENIED", 403)
            if self.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"}:
                raise ViewerError("CROSS_SITE_DENIED", 403)
            if self.path == "/health":
                self.reply(200, {"app": APP_ID, "project": PROJECT_ID, "readOnly": True})
                return
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if self.path in assets:
                name, mime = assets[self.path]
                self.reply(200, (UI / name).read_bytes(), mime)
                return
            if self.headers.get("X-Alpha-Viewer") != "1":
                raise ViewerError("VIEWER_HEADER_REQUIRED", 403)
            # Validate here and again inside AlphaClient: no generic proxy exists.
            validate_route(self.path)
            self.reply(200, self.server.client.read(self.path))
        except ViewerError as error:
            self.reply(error.status, {"error": {"code": error.code}})
        except Exception:
            self.reply(500, {"error": {"code": "VIEWER_INTERNAL_ERROR"}})

    def deny(self):
        self.reply(405, {"error": {"code": "READ_ONLY_METHOD_DENIED"}})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = deny


def make_client(cfg):
    return AlphaClient(lambda: credentials(Path(cfg["alphaUserData"])))


def health(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            value = json.load(response)
        if value.get("app") != APP_ID or value.get("project") != PROJECT_ID:
            raise ViewerError("VIEWER_PORT_OCCUPIED", 503)
        return True
    except urllib.error.URLError:
        return False
    except (ValueError, AttributeError):
        raise ViewerError("VIEWER_PORT_OCCUPIED", 503) from None


def current_session():
    session = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ViewerError("WINDOWS_SESSION_UNAVAILABLE", 503)
    return session.value


def open_window(cfg):
    # Check the real backend before launching a window; original Alpha is never restarted.
    make_client(cfg).read("/api/accounts")
    port = cfg["viewerPort"]
    if not health(port):
        directory = ROOT / "artifacts/alpha-readonly-launch"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / ("server-" + uuid.uuid4().hex + ".log")
        pythonw = Path(cfg["pythonExecutable"]).with_name("pythonw.exe")
        with log.open("xb") as stream:
            process = subprocess.Popen([str(pythonw), "-B", str(Path(__file__).resolve()), "--serve"],
                                       cwd=ROOT, stdout=stream, stderr=stream,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(50):
            if health(port):
                break
            if process.poll() is not None:
                raise ViewerError("VIEWER_START_FAILED", 503)
            time.sleep(0.2)
        else:
            raise ViewerError("VIEWER_START_TIMEOUT", 503)
    profile = ROOT / cfg["browserProfileRoot"] / ("session-" + str(current_session()))
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([cfg["edgeExecutable"], "--guest", "--disable-extensions", "--disable-sync",
                      f"--app=http://127.0.0.1:{port}/",
                      "--user-data-dir=" + str(profile), "--no-first-run", "--no-default-browser-check"],
                     cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    print("ALPHA_READONLY_WINDOW_LAUNCHED")


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--serve", action="store_true")
    action.add_argument("--open", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--show-errors", action="store_true")
    args = parser.parse_args()
    try:
        cfg = config()
        if args.serve:
            Server(cfg["viewerPort"], make_client(cfg)).serve_forever()
        elif args.open:
            open_window(cfg)
        else:
            accounts = make_client(cfg).read("/api/accounts")
            print(json.dumps({"result": "ALPHA_READONLY_BACKEND_OK", "accountCount": len(accounts.get("accounts", [])),
                              "viewerRunning": health(cfg["viewerPort"]), "readOnly": True}))
    except (ViewerError, OSError) as error:
        code = error.code if isinstance(error, ViewerError) else "LOCAL_RUNTIME_UNAVAILABLE"
        print(code, file=sys.stderr)
        if args.show_errors:
            ctypes.windll.user32.MessageBoxW(None, "只读副窗口未启动：" + code + "\n请保持原 Alpha 打开，并在设置中启动本机 HTTP API。", "Alpha 只读副窗口", 16)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
