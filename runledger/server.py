"""Loopback-only operator console. This development server is not a public API."""
import json
import sqlite3
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .model import Conflict, canonical, investigation
from .replay import replay
from .worker import InjectedCrash, Worker

STATIC = Path(__file__).parent / "static"


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, store, sink, **kwargs):
        self.store, self.sink = store, sink
        super().__init__(*args, **kwargs)

    def log_message(self, *_args):
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8", download=False):
        data = canonical(body).encode() if content_type.startswith("application/json") else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="runledger-trace.json"')
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in hosts and (origin is None or origin == f"http://{host}")

    def dispatch(self, method):
        if not self.allowed():
            self.respond(403, {"error": "loopback same-origin access required"})
            return
        try:
            path = urlsplit(self.path).path
            if method == "GET" and path in ("/", "/app.js", "/style.css"):
                name = "index.html" if path == "/" else path[1:]
                media = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name]
                self.respond(200, (STATIC / name).read_bytes(), media + "; charset=utf-8")
                return
            payload = {}
            if method == "POST":
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json" or self.headers.get("X-Runledger-Client") != "operator-console":
                    self.respond(403, {"error": "JSON and operator-console header required"})
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 150_000:
                    raise ValueError("request body must contain 1 to 150000 bytes")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
            if path == "/api/runs":
                if method == "GET":
                    self.respond(200, {"runs": self.store.list_runs()})
                else:
                    plan = payload.get("plan", investigation(payload.get("incident_id", "INC-1042")))
                    run_id = self.store.create(plan, payload["request_id"])
                    self.respond(201, {"run_id": run_id})
                return
            parts = path.strip("/").split("/")
            if len(parts) not in (3, 4) or parts[:2] != ["api", "runs"]:
                self.respond(404, {"error": "not found"})
                return
            run_id = parts[2]
            action = parts[3] if len(parts) == 4 else ""
            run = self.store.get(run_id)
            if method == "GET" and action in ("", "export"):
                run["receipts"] = self.sink.receipts(run_id)
                self.respond(200, run, download=action == "export")
            elif method == "POST" and action == "tick":
                try:
                    result = Worker(self.store, self.sink, lease_seconds=2).tick(run_id, fault=payload.get("fault"))
                except InjectedCrash:
                    result = {"outcome": "worker_crashed", "explanation": "Report committed, checkpoint absent. Wait 2 seconds, then advance with a fresh worker."}
                self.respond(200, result)
            elif method == "POST" and action == "approve":
                self.store.decide(run_id, payload["step_id"], payload["approval_hash"], approve=payload["approve"])
                self.respond(200, {"outcome": "decision_recorded"})
            elif method == "POST" and action == "cancel":
                self.store.cancel(run_id)
                self.respond(200, {"outcome": "cancelled"})
            elif method == "POST" and action == "replay":
                self.respond(200, replay(run, policy=payload.get("policy", "recorded")))
            else:
                self.respond(404, {"error": "not found"})
        except LookupError:
            self.respond(404, {"error": "run or field not found"})
        except Conflict as exc:
            self.respond(409, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.respond(400, {"error": str(exc)})
        except sqlite3.Error:
            self.respond(503, {"error": "storage unavailable"})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")


def make_server(store, sink, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, store=store, sink=sink))
    server.daemon_threads = True
    return server
