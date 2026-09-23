"""Small candidate shortlists from public tariff parameters and optional history."""

from pathlib import Path

import numpy as np
import pandas as pd


def prepare_tables(profile, tariffs):
    """Normalize public schemas without replacing valid segments or dropping rows."""
    if not isinstance(profile, pd.DataFrame) or profile.empty:
        raise ValueError("Public customer_profile must be a nonempty DataFrame")
    if not isinstance(tariffs, pd.DataFrame):
        raise ValueError("Public tariffs must be a DataFrame")
    profile, tariffs = profile.reset_index(drop=True).copy(), tariffs.copy()
    if "customer_id" not in profile and "ID_NUMBER" in profile:
        profile["customer_id"] = profile["ID_NUMBER"]
    for public, internal in (("tariff_plan_code", "tariff_id"), ("price_tariff", "monthly_fee")):
        if internal not in tariffs and public in tariffs:
            tariffs[internal] = tariffs[public]
    if "tariff_id" not in tariffs:
        raise ValueError("Public tariffs must expose tariff_plan_code or tariff_id")
    for column in ["arpu_segment", "data_segment", "call_segment", "current_tariff"]:
        if column not in profile:
            # An absent feature can only be used as an unrestricted filter.
            profile[column] = ""
        else:
            valid = profile[column].map(
                lambda value: isinstance(value, str) and bool(value.strip()) and ";" not in value
            )
            # Missing values are not empty filters: groupby must skip these keys.
            # Preserve the rows so broader filters still count their full audience.
            profile[column] = profile[column].astype(object).where(valid, pd.NA)
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
    """Load the published change history; its subscribers are a separate population."""
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    try:
        columns = {
            "tariff_plan_code_from": "from_tariff", "tariff_plan_code_to": "to_tariff",
            "AVG_ARPU_PREV_3M": "arpu_before", "AVG_ARPU_NEXT_3M": "arpu_after",
        }
        # Explicit public columns avoid reading identifiers or unrelated data.
        frame = pd.read_csv(directory / "data" / "change_tariff.csv", usecols=list(columns)).rename(columns=columns)
        before = pd.to_numeric(frame["arpu_before"], errors="coerce")
        after = pd.to_numeric(frame["arpu_after"], errors="coerce")
        valid = np.isfinite(before) & np.isfinite(after) & (before > 0) & (after >= 0)
        for column in ("from_tariff", "to_tariff"):
            valid &= frame[column].map(lambda value: isinstance(value, str) and bool(value.strip()))
        frame = frame.loc[valid].copy()
        frame["arpu_before"], frame["arpu_after"] = before[valid], after[valid]
        # Use the published audience thresholds on pre-change revenue only.
        frame["arpu_segment"] = np.where(frame["arpu_before"] < 1000, "LOW",
                                         np.where(frame["arpu_before"] <= 5000, "MID", "HIGH"))
        frame["relative_change"] = (after[valid] / before[valid] - 1).clip(-1, 1)
        return frame
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _history_scores(frame, history):
    """Conservative transition priors for comparable public tariff/ARPU groups."""
    if history is None or history.empty:
        return pd.Series(dtype=float)
    # The two datasets describe different subscribers. Matching is aggregate,
    # and missing segment values supply no comparable historical evidence.
    sample = history[history["from_tariff"].isin(frame["current_tariff"].dropna().unique())
                     & history["arpu_segment"].isin(frame["arpu_segment"].dropna().unique())
                     & history["from_tariff"].ne(history["to_tariff"])]
    stats = sample.groupby("to_tariff")["relative_change"].agg(["count", "mean", "std"])
    score = stats["mean"] * stats["count"] / (stats["count"] + 50)
    return score - stats["std"].fillna(1) / np.sqrt(stats["count"])


def cohort_priority(profile, cohorts, history=None):
    """Return cohort indices in pilot order; history never authorizes a campaign."""
    priorities = []
    for cohort in cohorts:
        scores = _history_scores(profile.loc[cohort.indices], history)
        # No comparable evidence gives a neutral prior. Stable ties preserve the
        # caller's order; negative history is explored after unknown audiences.
        prior = float(scores.max()) if not scores.empty else 0.0
        priorities.append(prior * cohort.mean_arpu * len(cohort.indices))
    return sorted(range(len(cohorts)), key=lambda index: -priorities[index])


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
        score = _history_scores(frame, history)
        score = score.loc[score.index.isin(candidates)]
        preferred.extend(score.sort_values(ascending=False, kind="stable").index[:1])
        preferred.extend(by_price[:2])
        # Preserve a different price point when the historical choice duplicates a neighbour.
        preferred.extend(by_price[2:])
        orders.append(list(dict.fromkeys(preferred))[:3])
    return orders
