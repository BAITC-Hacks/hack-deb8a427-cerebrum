"""Team-created local checks on demo effects; NOT the official evaluator."""

import argparse
import importlib
import json
import multiprocessing
import statistics
import time

from local_env import SCENARIOS, create_environment


def _worker(connection, seed, scenario, agent_module, data_dir):
    env = None
    started = time.monotonic()
    try:
        env = create_environment(seed=seed, scenario=scenario, data_dir=data_dir)
        agent_class = importlib.import_module(agent_module).Agent
        agent_started = time.monotonic()
        campaigns = agent_class().act(env)
        agent_seconds = time.monotonic() - agent_started
        if agent_seconds > 600:
            raise TimeoutError("Agent exceeded 600 seconds")
        metrics = env.evaluate(campaigns)
        report = {"ok": True, "seed": seed, "scenario": scenario, "metrics": metrics,
                  "campaigns": campaigns, "pilot_history": env.pilot_history,
                  "agent_seconds": agent_seconds, "wall_seconds": time.monotonic() - started}
    except Exception as error:
        # Even on failure, pilot costs/effects remain in the local diagnostic report.
        report = {"ok": False, "seed": seed, "scenario": scenario,
                  "error": f"{type(error).__name__}: {error}",
                  "metrics": env.summary() if env is not None else {},
                  "wall_seconds": time.monotonic() - started}
    connection.send(report)
    connection.close()


def run_once(seed=42, scenario="mixed", agent_module="agent", data_dir=None, timeout=600):
    if not 0 < timeout <= 600:
        raise ValueError("Timeout must be in (0, 600]")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, seed, scenario, agent_module, data_dir))
    process.start()
    sender.close()
    try:
        if receiver.poll(timeout):
            try:
                return receiver.recv()
            except EOFError:
                return {"ok": False, "seed": seed, "scenario": scenario, "error": "Worker exited without a result"}
        process.terminate()
        return {"ok": False, "seed": seed, "scenario": scenario,
                "error": f"Timeout after {timeout:g}s; pilot metrics unavailable after forced termination"}
    finally:
        receiver.close()
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenario", choices=SCENARIOS, default="mixed")
    parser.add_argument("--agent", default="agent")
    parser.add_argument("--data-dir")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.runs < 1 or not 0 < args.timeout <= 600:
        parser.error("--runs must be positive; --timeout must be in (0, 600]")
    reports = [run_once(args.seed + i, args.scenario, args.agent, args.data_dir, args.timeout) for i in range(args.runs)]
    if args.json:
        print(json.dumps({"source": "unofficial_team_simulator", "reports": reports}, ensure_ascii=False, allow_nan=False))
    else:
        print("UNOFFICIAL team simulator / synthetic demo data. Scores are not judging predictions.")
        for report in reports:
            if not report["ok"]:
                print(f"seed={report['seed']} FAIL: {report['error']}")
                continue
            metrics = report["metrics"]
            print(f"seed={report['seed']} OK campaigns={metrics['campaigns_count']} "
                  f"pilots={metrics['pilots_count']} contacts={metrics['contacts_used']}/15000 "
                  f"cost={metrics['communication_cost']:.2f}/100000 "
                  f"net_gain={metrics['net_gain']:.2f} agent_seconds={report['agent_seconds']:.3f}")
        gains = [r["metrics"]["net_gain"] for r in reports if r["ok"]]
        if gains:
            print(f"Completed {len(gains)}/{len(reports)}; net_gain min={min(gains):.2f} "
                  f"mean={statistics.mean(gains):.2f} max={max(gains):.2f}; "
                  f"positive={sum(gain > 0 for gain in gains)}/{len(gains)}")
    return 0 if all(report["ok"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
