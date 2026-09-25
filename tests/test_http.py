import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from runledger import Store, ReportSink
from runledger.server import make_server


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.server = make_server(Store(root / "runs.sqlite3"), ReportSink(root / "reports.sqlite3"), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        base = {"Content-Type": "application/json", "X-Runledger-Client": "operator-console"}
        base.update(headers or {})
        connection.request(method, path, json.dumps(body) if body is not None else None, headers=base)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_dashboard_and_content_security_policy(self):
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Every run has a receipt", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/../../model.py")[0], 404)

    def test_cross_origin_and_bad_host_cannot_mutate(self):
        for headers in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"Content-Type": "text/plain"}, {"X-Runledger-Client": ""}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", "/api/runs", {"request_id": "bad"}, headers)[0], 403)
        self.assertEqual(json.loads(self.request("GET", "/api/runs")[1])["runs"], [])

    def test_http_workflow_approval_replay_and_export(self):
        status, body, _ = self.request("POST", "/api/runs", {"request_id": "http"})
        self.assertEqual(status, 201)
        run_id = json.loads(body)["run_id"]
        path = "/api/runs/" + run_id
        for _ in range(3): self.assertEqual(self.request("POST", path + "/tick", {})[0], 200)
        run = json.loads(self.request("GET", path)[1])
        self.assertEqual(run["state"], "waiting")
        proposal = run["steps"][-1]
        self.assertEqual(self.request("POST", path + "/approve", {"step_id": "publish", "approval_hash": proposal["approval_hash"], "approve": True})[0], 200)
        self.request("POST", path + "/tick", {})
        result = json.loads(self.request("POST", path + "/replay", {"policy": "conservative"})[1])
        self.assertEqual(result["divergences"], 1)
        status, body, headers = self.request("GET", path + "/export")
        exported = json.loads(body)
        self.assertTrue(exported["journal_valid"])
        self.assertEqual(len(exported["receipts"]), 1)
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_invalid_and_unknown_requests(self):
        self.assertEqual(self.request("GET", "/api/runs/missing")[0], 404)
        self.assertEqual(self.request("POST", "/api/runs", {"request_id": "bad", "incident_id": "missing"})[0], 400)
        self.assertEqual(self.request("POST", "/api/runs", ["not-an-object"])[0], 400)


if __name__ == "__main__": unittest.main()
