"""Produce a reproducible DEMO submission after a successful local evaluation."""

import argparse
import csv
from pathlib import Path

from local_env import CAMPAIGN_COLUMNS, SCENARIOS
from local_eval import run_once


def write_submission(campaigns, output):
    with Path(output).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CAMPAIGN_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(campaigns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "submission.csv")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenario", choices=SCENARIOS, default="mixed")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    report = run_once(seed=args.seed, scenario=args.scenario, data_dir=args.data_dir)
    if not report["ok"]:
        raise SystemExit(f"Submission not written: {report['error']}")
    write_submission(report["campaigns"], args.output)
    print(f"DEMO submission: {args.output} ({len(report['campaigns'])} campaigns). "
          "Official compatibility has not been verified.")


if __name__ == "__main__":
    main()
