import contextlib
import http.client
import io
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fault_support import FaultCase, invoice_row
from reconcile.http_server import make_server


class HttpFaults(FaultCase):
    def setUp(self):
        super().setUp()
        self.import_files(bank=self.bank_file(), invoice=self.invoice_file([invoice_row(amount=1300000)]))
        self.log = io.StringIO()
        self.redirect = contextlib.redirect_stderr(self.log)
        self.redirect.__enter__()
        self.server = make_server(self.service, "127.0.0.1", 0)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.redirect.__exit__(None, None, None)
        (self.directory / "http-server.log").write_text(self.log.getvalue(), encoding="utf-8")

    def call(self, route="/api/bootstrap", payload=None, method=None, headers=None, raw=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        body = raw if raw is not None else json.dumps(payload).encode("utf-8") if payload is not None else None
        h = {"Content-Type": "application/json", **(headers or {})}
        try:
            client.request(method or ("POST" if body is not None else "GET"), route, body, h)
            response = client.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            client.close()

    def test_wrong_host_and_origin_rejected(self):
        for header in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Origin": "null"}):
            with self.subTest(header=header):
                self.assertEqual(self.call(headers=header)[0], 403)

    def test_path_traversal_never_exposes_ledger(self):
        for route in ("/../ledger/company-ledger.v1.json", "/%2e%2e/ledger/company-ledger.v1.json", "/reports/../../ledger/company-ledger.v1.json", "/%5c..%5cledger%5ccompany-ledger.v1.json"):
            with self.subTest(route=route):
                status, body, _ = self.call(route)
                self.assertEqual(status, 404)
                self.assertNotIn(b"schema_version", body)

    def test_invalid_json_root_type_and_content_type(self):
        for raw in (b"{broken", b"[]", b"null", b"123"):
            with self.subTest(raw=raw):
                self.assertEqual(self.call("/api/import", raw=raw)[0], 400)
        self.assertEqual(self.call("/api/import", raw=b"{}", headers={"Content-Type":"text/plain"})[0], 415)

    def test_malformed_file_objects_get_clear_client_errors(self):
        for value in (True, 4, "invoice.xlsx", [1], {"name": [], "content": None}):
            with self.subTest(value=value):
                status, body, _ = self.call("/api/import", {"revision": 1, "invoice": value})
                self.assertEqual(status, 400, body)
        self.assertEqual(self.ledger()["revision"], 1)

    def test_invalid_filter_values_are_rejected(self):
        for payload in ({"month": ["2026-08"]}, {"month": "2026-13"}, {"month": "2026-08-01"}, {"unfinished": "false"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.call("/api/ledger", payload)[0], 400)

    def test_unknown_route_and_unsupported_method(self):
        self.assertEqual(self.call("/api/missing", {})[0], 404)
        self.assertEqual(self.call("/api/import", {}, method="PUT")[0], 501)

    def test_invoice_difference_route_and_stale_guard(self):
        view = self.ledger()
        iid = view["invoices"][0]["id"]
        self.allocate([dict(bank_id=view["bank"][0]["id"], invoice_id=iid, amount_cents=1200000)])
        payload = dict(revision=self.ledger()["revision"], invoice_id=iid, status="carry_forward", note="确认下月使用")
        code, body, _ = self.call("/api/invoice-difference", payload)
        self.assertEqual(code, 200, body)
        result = json.loads(body)
        self.assertEqual(result["invoices"][0]["distributable_cents"], 100000)
        self.assertEqual(len(result["allocations"]), 1)
        before = self.saved()
        self.assertEqual(self.call("/api/invoice-difference", payload)[0], 400)
        self.assertEqual(self.saved(), before)

    def test_duplicate_json_keys_rejected(self):
        raw = b'{"revision":1,"revision":1,"aliases":{},"exclude_special":false}'
        before = self.saved()
        self.assertEqual(self.call("/api/settings", raw=raw)[0], 400)
        self.assertEqual(self.saved(), before)

    def test_corrupt_ledger_get_returns_json_and_does_not_drop_connection(self):
        self.service.store.path.write_text('{"broken":', encoding="utf-8")
        before = self.saved()
        status, body, _ = self.call()
        self.assertEqual(status, 503)
        self.assertIn("error", json.loads(body))
        self.assertEqual(self.saved(), before)

    def test_half_sent_body_times_out_and_other_requests_continue(self):
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        connection.settimeout(2)
        try:
            connection.sendall(f"POST /api/import HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{{".encode("ascii"))
            self.assertEqual(self.call()[0], 200)
            self.assertIn(b"408", connection.recv(2048))
        finally:
            connection.close()
        self.assertEqual(self.ledger()["revision"], 1)

    def test_parallel_same_revision_one_success_only(self):
        view = self.ledger()
        payload = {"revision": 1, "allocations": [{"bank_id":view["bank"][0]["id"], "invoice_id":view["invoices"][0]["id"], "amount_cents":100}], "note":"同时点击提交"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _: self.call("/api/review", payload)[0], range(8)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(400), 7)
        self.assertEqual(self.ledger()["bank"][0]["allocated_cents"], 100)

    def test_wrong_length_and_oversized_request_rejected_without_read(self):
        for length in ("-1", "0", "9999999999", "abc"):
            with self.subTest(length=length):
                self.assertIn(self.call("/api/import", raw=b"", headers={"Content-Length":length})[0], (400,413))

    def test_null_character_path_is_handled_without_server_crash(self):
        self.assertIn(self.call("/%00")[0], (400,404))
        self.assertEqual(self.call()[0], 200)
