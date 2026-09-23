"""Independent synthetic unit-test fixtures, never the participant dataset."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "demo_data"


def build_demo_data(n_customers=23_441, seed=2026):
    rng = np.random.default_rng(seed)
    tariffs = pd.DataFrame({
        "tariff_id": [f"tariff_{i}" for i in range(1, 22)],
        "monthly_fee": np.linspace(700, 15_000, 21).round(2),
        "data_gb": np.linspace(1, 100, 21).round(1),
        "minutes": np.linspace(50, 1500, 21).astype(int),
    })
    arpu_segment = rng.choice(["LOW", "MID", "HIGH"], n_customers, p=[.2, .4, .4])
    predicted = np.empty(n_customers)
    for segment, low, high in [("LOW", 100, 999), ("MID", 1000, 5000), ("HIGH", 5001, 18000)]:
        mask = arpu_segment == segment
        predicted[mask] = rng.uniform(low, high, int(mask.sum()))
    profile = pd.DataFrame({
        "customer_id": np.arange(1, n_customers + 1),
        "current_tariff": rng.choice(tariffs["tariff_id"], n_customers),
        "arpu_segment": arpu_segment,
        "data_segment": rng.choice(["NON_USER", "LITE", "HEAVY"], n_customers, p=[.15, .4, .45]),
        "call_segment": rng.choice(["LOW", "MEDIUM", "HIGH"], n_customers),
        "predicted_arpu": predicted.round(2),
    })
    return profile, tariffs


def write_demo_data(directory=DEFAULT_DIRECTORY, seed=2026):
    directory = Path(directory)
    paths = [directory / "customer_profile.csv", directory / "tariff_dictionary.csv"]
    if any(path.exists() for path in paths):
        raise FileExistsError("Demo files already exist; use a new --output directory.")
    directory.mkdir(parents=True, exist_ok=True)
    profile, tariffs = build_demo_data(seed=seed)
    profile.to_csv(paths[0], index=False, lineterminator="\n")
    tariffs.to_csv(paths[1], index=False, lineterminator="\n")
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    for output in write_demo_data(args.output, args.seed):
        print(output)
