"""Prepare, serve, or audit an assisted author review-time pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.pilot_bundle import prepare_pilot, report_pilot, verify_pilot_setup
from docwork.review_pilot import ReviewPilot
from docwork.web import ReviewServer


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--output-dir", type=Path, required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("directory", type=Path)
    serve.add_argument("--port", type=int, default=8766)
    report = commands.add_parser("report")
    report.add_argument("directory", type=Path)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        protocol = prepare_pilot(root, root / "datasets/invoices-v1/manifest.json",
                                 root / "evals/invoice-freeze-2026-10-03/development", args.output_dir)
        print(json.dumps({"status": "prepared", "documents": len(protocol["documents"]), "directory": str(args.output_dir)}, indent=2))
    elif args.action == "serve":
        directory = args.directory.resolve(strict=True)
        verify_pilot_setup(root, directory, require_current_source=True)
        store = IntakeStore(directory / "review.sqlite", directory / "objects")
        pilot = ReviewPilot(directory, store)
        try:
            with ReviewServer(("127.0.0.1", args.port), store, review_pilot=pilot) as server:
                print(f"Author pilot · recorded OCR rules · open {server.origin}/?token={server.token}", flush=True)
                server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            pilot.close()
    else:
        result = report_pilot(root, args.directory)
        if args.output.resolve().is_relative_to(args.directory.resolve()):
            parser.error("Report must be outside the pilot session")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"status": result["status"], "completed": result["completed"], "scheduled": result["scheduled"], "report": str(args.output)}))
        return 0 if result["status"] == "complete" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
