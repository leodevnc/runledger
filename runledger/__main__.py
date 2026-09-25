import argparse
import json
import time
import uuid
from pathlib import Path

from . import InjectedCrash, ReportSink, Store, Worker, investigation
from .replay import replay
from .server import make_server


def main():
    parser = argparse.ArgumentParser(description="Runledger local execution workbench")
    parser.add_argument("--data", type=Path, default=Path(".runledger"))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("demo")
    commands.add_parser("list")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("run_id")
    create = commands.add_parser("create")
    create.add_argument("plan", type=Path)
    create.add_argument("--request-id", required=True)
    tick = commands.add_parser("tick")
    tick.add_argument("run_id")
    decision = commands.add_parser("approve")
    decision.add_argument("run_id")
    decision.add_argument("step_id")
    decision.add_argument("approval_hash")
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    store, sink = Store(args.data / "runs.sqlite3"), ReportSink(args.data / "reports.sqlite3")
    if args.command == "serve":
        server = make_server(store, sink, args.port)
        print(f"Runledger: http://127.0.0.1:{server.server_port} (synthetic local tools)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    if args.command == "demo":
        run_id = store.create(investigation(), uuid.uuid4().hex)
        worker = Worker(store, sink, lease_seconds=0.1)
        worker.tick(run_id)
        worker.tick(run_id)
        worker.tick(run_id)
        proposal = store.get(run_id)["steps"][-1]
        store.decide(run_id, "publish", proposal["approval_hash"], approve=True, actor="demo-script")
        try:
            worker.tick(run_id, fault="after_effect")
        except InjectedCrash:
            pass
        before = {"receipts": len(sink.receipts(run_id)), "publish_state": store.get(run_id)["steps"][-1]["state"]}
        time.sleep(0.12)
        Worker(store, sink).tick(run_id)
        run = store.get(run_id)
        result = {"run_id": run_id, "after_crash": before, "recovered_state": run["state"],
                  "receipts_after_recovery": len(sink.receipts(run_id)), "journal_valid": run["journal_valid"],
                  "counterfactual": replay(run, policy="conservative")}
    elif args.command == "list":
        result = store.list_runs()
    elif args.command == "inspect":
        result = store.get(args.run_id)
    elif args.command == "create":
        result = {"run_id": store.create(json.loads(args.plan.read_text()), args.request_id)}
    elif args.command == "tick":
        result = Worker(store, sink).tick(args.run_id)
    else:
        store.decide(args.run_id, args.step_id, args.approval_hash, approve=True)
        result = {"outcome": "approved"}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
