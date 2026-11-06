"""Exercise the network boundary against a fake Alpha, never a live send API."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from server import AlphaClient, Server


class FakeAlpha(BaseHTTPRequestHandler):
    calls = []
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.calls.append((self.command, self.path, self.headers.get("Authorization")))
        denied = "account_id=expired" in self.path
        data = {"error": {"code": "HTTP_API_OPERATION_BLOCKED"}} if denied else {"items": [{"id": "same-original-record", "text": "shared data"}]}
        body = json.dumps(data).encode()
        self.send_response(423 if denied else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeAlpha)
        cls.proxy = Server(0, AlphaClient(lambda: (cls.fake.server_port, "private-test-token-never-in-response")))
        cls.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (cls.fake, cls.proxy)]
        for t in cls.threads:
            t.start()

    @classmethod
    def tearDownClass(cls):
        for s in (cls.proxy, cls.fake):
            s.shutdown()
            s.server_close()
        for t in cls.threads:
            t.join(2)

    def request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=5)
        conn.request(method, path, headers={"Host": f"127.0.0.1:{self.proxy.server_port}",
                                            "X-Alpha-Viewer": "1", **(headers or {})})
        response = conn.getresponse()
        status, body = response.status, response.read()
        conn.close()
        return status, body

    def test_reads_use_original_backend_and_keep_token_private(self):
        before = len(FakeAlpha.calls)
        for route in ["/api/accounts", "/api/sessions?account_id=a&limit=10&offset=0", "/api/messages?account_id=a&username=u&order=desc", "/api/search?account_id=a&query=hello&page_size=20"]:
            with self.subTest(route=route):
                code, body = self.request(route)
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)["items"][0]["id"], "same-original-record")
                self.assertNotIn(b"private-test-token", body)
        calls = FakeAlpha.calls[before:]
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(method == "GET" for method, _, _ in calls))
        self.assertTrue(all(auth == "Bearer private-test-token-never-in-response" for _, _, auth in calls))

    def test_mutating_methods_never_reach_alpha(self):
        before = len(FakeAlpha.calls)
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]:
            for path in ["/api/messages", "/api/v1/messages/send-text", "/mcp"]:
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.request(path, method)[0], 405)
        self.assertEqual(len(FakeAlpha.calls), before)

    def test_unknown_routes_and_mutation_parameters_never_reach_alpha(self):
        before = len(FakeAlpha.calls)
        for path in ["/mcp", "/api/v1/exports", "/api/v1/messages/send-text", "/api/v1/sns/export", "/api/messages/send-text", "/api/%6dessages", "/api/messages/../exports", "/api/messages?account_id=a&username=u&destination_path=F%3A%5Cx", "/api/accounts?access_token=evil", "/api/sessions?account_id=a&account_id=b", "/api/sessions?account_id=a&limit=10001", "/api/messages?account_id=a&username=u%00", "/api/search?account_id=a&query=x&url=http%3A%2F%2Fevil", "http://evil/api/accounts"]:
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], [400, 404])
        self.assertEqual(len(FakeAlpha.calls), before)

    def test_browser_cross_site_requests_denied(self):
        before = len(FakeAlpha.calls)
        for headers in [{"Origin": "https://example.invalid"}, {"Origin": "null"}, {"Host": "example.invalid"}, {"Sec-Fetch-Site": "cross-site"}, {"X-Alpha-Viewer": ""}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/accounts", headers=headers)[0], 403)
        self.assertEqual(len(FakeAlpha.calls), before)

    def test_backend_error_is_not_an_empty_success(self):
        status, body = self.request("/api/sessions?account_id=expired")
        self.assertEqual(status, 423)
        self.assertEqual(json.loads(body)["error"]["code"], "HTTP_API_OPERATION_BLOCKED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
