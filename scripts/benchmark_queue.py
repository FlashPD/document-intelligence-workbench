"""Measure or verify the fixed 20-document production-parser queue."""

import argparse
import json
from pathlib import Path

from docwork.queue_benchmark import run_queue, verify_queue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--co-running-workload", required=True, help="Declare other host workloads; use 'none observed' only when checked")
    verify = commands.add_parser("verify")
    verify.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.action == "verify":
        print(json.dumps(verify_queue(root, args.directory), indent=2))
        return 0
    report = run_queue(root, args.output_dir, co_running_workload=args.co_running_workload)
    print(json.dumps({"status": report["status"], "summary": report["summary"]}, indent=2))
    return 2 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
