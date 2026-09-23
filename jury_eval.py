"""Independently validate public decisions, then display the official mock score."""
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import statistics
import subprocess
import time

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from agent import Agent
from local_eval import evaluate_agent
from make_submission import CAMPAIGN_COLUMNS
import pandas as pd

PUBLIC = {"customer_profile", "tariffs", "channels", "remaining_budget",
          "remaining_contacts", "pilots_left", "pilot_history", "run_pilot"}
FILTERS = {"filter_arpu_segment": "arpu_segment", "filter_data_segment": "data_segment",
           "filter_call_segment": "call_segment", "filter_current_tariff": "current_tariff"}


class PublicView:
    def __init__(self, env, calls):
        self.env = env
        self.calls = calls

    def __getattr__(self, name):
        if name not in PUBLIC:
            raise AttributeError("Only the public participant API is available")
        return getattr(self.env, name)

    def run_pilot(self, **kwargs):
        result = self.env.run_pilot(**kwargs)
        self.calls.append({**kwargs, **result})
        return result


class RecordedAgent:
    def act(self, env):
        self.pilots = []
        started = time.perf_counter()
        agent = Agent()
        self.campaigns = agent.act(PublicView(env, self.pilots))
        self.trace, self.llm_status = agent.trace, agent.llm_status
        self.seconds = time.perf_counter() - started
        self.sizes = []
        final_cost = 0.0
        if not isinstance(self.campaigns, list) or not 1 <= len(self.campaigns) <= 10:
            raise ValueError("Invalid final campaign count")
        for campaign in self.campaigns:
            selected = env.customer_profile
            if set(campaign) - set(CAMPAIGN_COLUMNS):
                raise ValueError("Unknown campaign fields")
            if campaign["target_tariff"] not in set(env.tariffs["tariff_plan_code"]):
                raise ValueError("Unknown target tariff")
            for key, column in FILTERS.items():
                value = campaign.get(key)
                if value is None:
                    continue
                selected = selected[selected[column].isin(value.split(";"))] if key == "filter_current_tariff" else selected[selected[column] == value]
            size = len(selected)
            if not 1 <= size <= 5000:
                raise ValueError("Invalid final audience size")
            self.sizes.append(size)
            final_cost += size * env.channels[campaign["channel"]]["cost_per_contact"]
        if not 1 <= len(self.pilots) <= 20 or any(not 10 <= p["n_customers"] <= 200 for p in self.pilots):
            raise ValueError("Invalid pilot sizes or count")
        self.contacts = sum(self.sizes) + sum(p["n_customers"] for p in self.pilots)
        self.cost = final_cost + sum(p["cost"] for p in self.pilots)
        if self.contacts > 15000 or self.cost > 100000 or sum(self.sizes) > env.remaining_contacts or final_cost > env.remaining_budget:
            raise ValueError("Shared contact or budget limit exceeded")
        self.remaining_budget = float(env.remaining_budget) - final_cost
        self.remaining_contacts = int(env.remaining_contacts) - sum(self.sizes)
        self.finished = True
        return self.campaigns


def version():
    digest = hashlib.sha256()
    for name in ("agent.py", "candidates.py", "llm_advisor.py", "jury_eval.py", "requirements.txt"):
        digest.update((PROJECT / name).read_bytes())
    return digest.hexdigest()[:12]


def run(seed):
    os.chdir(PROJECT)
    recorded, output = RecordedAgent(), io.StringIO()
    with contextlib.redirect_stdout(output):
        result = evaluate_agent(recorded, seed=seed, verbose=False)
    if not getattr(recorded, "finished", False) or not result or output.getvalue().strip():
        raise ValueError("Official evaluator reported an agent or campaign error")
    if result["total_contacts"] != recorded.contacts or not math.isclose(float(result["total_cost"]), recorded.cost, abs_tol=1e-6):
        raise ValueError("Evaluator totals differ from public campaign accounting")
    if result["n_campaigns"] != len(recorded.pilots) + len(recorded.campaigns):
        raise ValueError("Evaluator did not accept all campaigns")
    metrics = {
        "baseline_arpu": float(result["baseline_total_arpu"]), "gross_uplift": float(result["gross_arpu_lift"]),
        "communication_cost": float(result["total_cost"]), "net_gain": float(result["net_arpu_gain"]),
        "total_arpu_after_costs": float(result["total_arpu_after"]), "contacts_used": int(result["total_contacts"]),
        "unique_customers": int(result["unique_customers_targeted"]), "pilots_count": len(recorded.pilots),
        "campaigns_count": len(recorded.campaigns), "campaign_sizes": recorded.sizes,
        "remaining_budget": recorded.remaining_budget, "remaining_contacts": recorded.remaining_contacts,
    }
    # Match the official exporter column order, missing values and line endings.
    frame = pd.DataFrame(recorded.campaigns)
    for column in CAMPAIGN_COLUMNS:
        if column not in frame:
            frame[column] = None
    return {"ok": True, "source": "official_participant_mock", "seed": seed, "version": version(),
            "score_status": result["status"], "agent_seconds": recorded.seconds,
            "campaigns": recorded.campaigns, "pilot_history": recorded.pilots,
            "decisions": recorded.trace, "llm_status": recorded.llm_status,
            "metrics": metrics, "csv": frame[CAMPAIGN_COLUMNS].to_csv(index=False)}


def run_isolated(seed):
    completed = subprocess.run([sys.executable, "-B", "-X", "utf8", str(PROJECT / "jury_eval.py"),
                                "--seed", str(seed)], cwd=PROJECT, capture_output=True,
                               text=True, encoding="utf-8", timeout=600)
    try:
        report = json.loads(completed.stdout)
    except ValueError:
        raise RuntimeError("Evaluation process failed before returning a report") from None
    if completed.returncode or not report.get("ok"):
        raise RuntimeError(report.get("error", "Evaluation failed"))
    return report


def check():
    """Use process timeouts and inspect outcomes, never just evaluator exit codes."""
    os.environ["CEREBRUM_LLM"] = "0"
    reference = run_isolated(42)
    reports = []
    for seed in range(10):
        report = run_isolated(seed)
        reports.append(report)
        print(f"seed {seed}: {report['score_status']}, net={report['metrics']['net_gain']:,.2f}, "
              f"contacts={report['metrics']['contacts_used']}, pilots={report['metrics']['pilots_count']}", flush=True)
    gains = [report["metrics"]["net_gain"] for report in reports]
    expected = reference["csv"].encode("utf-8")
    for _ in range(2):
        subprocess.run([sys.executable, "-B", "-X", "utf8", str(PROJECT / "make_submission.py")],
                       cwd=PROJECT, capture_output=True, check=True, timeout=600)
        if (PROJECT / "submission.csv").read_bytes() != expected:
            raise RuntimeError("Official submission does not reproduce the validated seed 42 plan")
    evidence = {"version": version(), "environment": "official_participant_mock",
                "runs": 10, "valid": len(reports), "positive": sum(gain > 0 for gain in gains),
                "median_gain": statistics.median(gains), "min_gain": min(gains), "max_gain": max(gains),
                "submission_sha256": hashlib.sha256(expected).hexdigest(),
                "seeds": [{"seed": report["seed"], "status": report["score_status"],
                           "seconds": report["agent_seconds"], **report["metrics"]} for report in reports]}
    output = PROJECT / "artifacts"
    output.mkdir(exist_ok=True)
    (output / "validation.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "latest.json").write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
    if evidence["positive"] != 10 or reference["score_status"] != "PASS":
        raise RuntimeError("Limits passed but at least one mock run was not profitable; see artifacts/validation.json")
    print(f"PASS 11 valid profitable runs; reproducible submission {evidence['submission_sha256']}")
    print("Report: artifacts/validation.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            check()
            raise SystemExit(0)
        report = run(args.seed)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error), "seed": args.seed}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
