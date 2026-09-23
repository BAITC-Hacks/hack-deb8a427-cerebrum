"""Small candidate shortlists from public tariff parameters and optional history."""

import json
from pathlib import Path

import numpy as np
import pandas as pd


def prepare_tables(profile, tariffs):
    """Preserve all audience rows; unavailable features cannot become filters."""
    if not isinstance(profile, pd.DataFrame) or profile.empty:
        raise ValueError("Public customer_profile must be a nonempty DataFrame")
    if not isinstance(tariffs, pd.DataFrame) or "tariff_id" not in tariffs:
        raise ValueError("Public tariffs must expose tariff_id")
    profile, tariffs = profile.reset_index(drop=True).copy(), tariffs.copy()
    for column in ["arpu_segment", "data_segment", "call_segment", "current_tariff"]:
        if column not in profile or not profile[column].map(
            lambda value: isinstance(value, str) and bool(value) and ";" not in value
        ).all():
            # Empty filters mean no restriction. Do not invent an unknown category.
            profile[column] = ""
    source = profile.get("predicted_arpu", profile.get("ARPU_3m_avg", pd.Series(0.0, index=profile.index)))
    values = pd.to_numeric(source, errors="coerce")
    # Zero is a neutral ranking weight for missing revenue, never a pilot effect.
    profile["predicted_arpu"] = values.where(np.isfinite(values) & (values >= 0), 0)
    valid_ids = tariffs["tariff_id"].map(lambda value: isinstance(value, str) and bool(value))
    tariffs = tariffs.loc[valid_ids].drop_duplicates("tariff_id").copy()
    if tariffs.empty:
        raise ValueError("No usable public tariff identifiers")
    values = pd.to_numeric(tariffs.get("monthly_fee", pd.Series(0.0, index=tariffs.index)), errors="coerce")
    tariffs["monthly_fee"] = values.where(np.isfinite(values) & (values >= 0), 0)
    return profile, tariffs


def load_history(directory=None):
    """Read only our explicitly labelled public fixtures, never evaluator files."""
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent / "synthetic_case"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("source") != "team_generated_synthetic_case":
            return None
        frame = pd.read_csv(directory / "data" / "change_tariff.csv")
        required = {"to_tariff", "arpu_before", "arpu_after", "arpu_segment", "data_segment", "event_date"}
        if not required.issubset(frame.columns):
            return None
        before = pd.to_numeric(frame["arpu_before"], errors="coerce")
        after = pd.to_numeric(frame["arpu_after"], errors="coerce")
        dates = pd.to_datetime(frame["event_date"], errors="coerce")
        valid = np.isfinite(before) & np.isfinite(after) & (before > 0) & (after >= 0)
        valid &= dates < pd.Timestamp(manifest["snapshot_date"])
        frame = frame.loc[valid].copy()
        frame["relative_change"] = (after[valid] / before[valid] - 1).clip(-1, 1)
        return frame
    except (OSError, ValueError, TypeError, KeyError):
        return None


def candidate_orders(profile, tariffs, cohorts, history=None):
    """At most three tariffs per cohort; history orders trials, never authorizes a plan."""
    prices = tariffs.set_index("tariff_id")["monthly_fee"].astype(float).to_dict()
    orders = []
    for cohort in cohorts:
        frame = profile.loc[cohort.indices]
        current = set(frame["current_tariff"])
        candidates = [name for name in prices if current != {name}] or list(prices)
        affordable = [name for name in candidates if prices[name] <= 2 * max(cohort.mean_arpu, 1)]
        candidates = affordable or candidates
        by_price = sorted(candidates, key=lambda name: (abs(prices[name] - cohort.mean_arpu), name))
        preferred = []
        if history is not None and not history.empty:
            sample = history[history["arpu_segment"].isin(frame["arpu_segment"].unique())
                             & history["data_segment"].isin(frame["data_segment"].unique())
                             & history["to_tariff"].isin(candidates)]
            stats = sample.groupby("to_tariff")["relative_change"].agg(["count", "mean", "std"])
            # Historical subscribers differ: shrink sparse evidence toward no effect.
            score = stats["mean"] * stats["count"] / (stats["count"] + 50)
            score -= stats["std"].fillna(1) / np.sqrt(stats["count"])
            preferred.extend(score.sort_values(ascending=False, kind="stable").index[:1])
        preferred.extend(by_price[:2])
        # Preserve a different price point when the historical choice duplicates a neighbour.
        preferred.extend(by_price[2:])
        orders.append(list(dict.fromkeys(preferred))[:3])
    return orders
