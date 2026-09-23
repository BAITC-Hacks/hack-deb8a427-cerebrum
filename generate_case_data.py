"""Generate the public synthetic case tables described by the supplied brief.

These are team fixtures, not a reconstruction of the organizer's dataset.
The historical population and its outcomes are independent of the simulator.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from generate_demo_data import build_demo_data


DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "synthetic_case"
BRIEF_SHA256 = "fa8b129a4e41213a723e4a5c5d3c9623ab41f60b8851635260245957c764f1a4"
MONTHS = pd.date_range("2026-01-01", "2026-08-01", freq="MS")
SNAPSHOT = "2026-09-01"

DESCRIPTIONS = {
    "customer_id": ("identifier", "Subscriber ID; historical and target populations do not overlap"),
    "current_tariff": ("identifier", "Tariff at the target snapshot"),
    "tariff_id": ("identifier", "Tariff ID, tariff_1 through tariff_21"),
    "arpu_segment": ("category", "ARPU_3m_avg: LOW <1000; MID 1000..5000; HIGH >5000"),
    "data_segment": ("category", "DATA_3m_avg: NON_USER =0; LITE >0..2000; HEAVY >2000 MB"),
    "call_segment": ("category", "CALL_3m_avg: LOW <100; MEDIUM 100..400; HIGH >400 minutes"),
    "predicted_arpu": ("conditional currency", "Forecast baseline; not an observed campaign outcome"),
    "ARPU_3m_avg": ("conditional currency/month", "Target mean ARPU over June-August 2026"),
    "DATA_3m_avg": ("MB/month", "Target mean data consumption over June-August 2026"),
    "CALL_3m_avg": ("minutes/month", "Target mean voice consumption over June-August 2026"),
    "SMS_3m_avg": ("messages/month", "Target mean SMS consumption over June-August 2026"),
    "snapshot_date": ("date", "Start of the planning month; all historical rows precede this date"),
    "device_type": ("category", "Synthetic device category, not a device identifier"),
    "monthly_fee": ("conditional currency/month", "Synthetic monthly tariff fee"),
    "data_gb": ("GB/month", "Synthetic included data allowance; use 1024 MB per GB locally"),
    "minutes": ("minutes/month", "Synthetic included voice allowance"),
    "sms": ("messages/month", "Synthetic included SMS allowance"),
    "description": ("text", "Human-readable synthetic tariff description"),
    "month": ("date", "Calendar month encoded by its first day"),
    "arpu": ("conditional currency/month", "Historical observed monthly revenue"),
    "voice_minutes": ("minutes/month", "Historical monthly voice consumption"),
    "sms_count": ("messages/month", "Historical monthly SMS consumption"),
    "data_mb": ("MB/month", "Historical monthly data consumption"),
    "event_id": ("identifier", "Unique tariff-change event ID"),
    "event_date": ("date", "Tariff change at the start of this month, at most once per subscriber/month"),
    "from_tariff": ("identifier", "Tariff in the preceding month"),
    "to_tariff": ("identifier", "Tariff in the event month; differs from from_tariff"),
    "arpu_before": ("conditional currency/month", "Revenue in the preceding month, same value as arpu_monthly"),
    "arpu_after": ("conditional currency/month", "Revenue in the event month, same value as arpu_monthly"),
}


def _arpu_segment(values):
    return np.select([values < 1000, values <= 5000], ["LOW", "MID"], default="HIGH")


def _data_segment(values):
    return np.select([values == 0, values <= 2000], ["NON_USER", "LITE"], default="HEAVY")


def _call_segment(values):
    return np.select([values < 100, values <= 400], ["LOW", "MEDIUM"], default="HIGH")


def build_case_data(seed=2026, n_customers=23_441, n_history=6000, n_changes=14_824):
    if n_customers < 1 or n_history < 1 or not 0 < n_changes <= n_history * (len(MONTHS) - 1):
        raise ValueError("Invalid population or tariff-change count")
    rng = np.random.default_rng(seed)
    profile, tariffs = build_demo_data(n_customers=n_customers, seed=seed)
    profile["ARPU_3m_avg"] = profile["predicted_arpu"]
    # Match the published aggregate in integer cents without changing segment definitions.
    target_cents = round(150_641_084 * 100 * n_customers / 23_441)
    cents = np.floor(profile["predicted_arpu"].to_numpy() / profile["predicted_arpu"].sum() * target_cents).astype(np.int64)
    cents[:int(target_cents - cents.sum())] += 1
    profile["predicted_arpu"] = cents / 100
    profile["DATA_3m_avg"] = np.where(
        profile["data_segment"] == "NON_USER", 0,
        np.where(profile["data_segment"] == "LITE", rng.uniform(.01, 2000, n_customers),
                 rng.uniform(2000.01, 24000, n_customers)),
    ).round(2)
    profile["CALL_3m_avg"] = np.select(
        [profile["call_segment"] == "LOW", profile["call_segment"] == "MEDIUM"],
        [rng.uniform(0, 99.99, n_customers), rng.uniform(100, 400, n_customers)],
        default=rng.uniform(400.01, 1800, n_customers),
    ).round(2)
    profile["SMS_3m_avg"] = rng.poisson(12, n_customers).astype(float)
    profile["device_type"] = rng.choice(["smartphone", "feature_phone", "modem"], n_customers, p=[.8, .15, .05])
    profile["snapshot_date"] = SNAPSHOT
    tariffs["sms"] = np.linspace(0, 300, len(tariffs)).astype(int)
    dictionary = tariffs.copy()
    dictionary["description"] = [f"Synthetic educational tariff {i}" for i in range(1, 22)]

    # Separate random stream/population; no imports or reads of evaluator effects.
    history_rng = np.random.default_rng(np.random.SeedSequence([seed, 1]))
    ids = np.arange(1_000_001 + n_customers, 1_000_001 + n_customers + n_history)
    tariff_names = tariffs["tariff_id"].to_numpy()
    current = history_rng.integers(0, len(tariffs), n_history)
    fees = tariffs["monthly_fee"].to_numpy()
    revenue = np.clip(history_rng.lognormal(8, .9, n_history), 50, 25000).round(2)
    voice_base = history_rng.gamma(2, 140, n_history)
    data_base = history_rng.lognormal(7.5, 1.1, n_history)
    data_base[history_rng.random(n_history) < .15] = 0
    devices = history_rng.choice(["smartphone", "feature_phone", "modem"], n_history, p=[.8, .15, .05])
    changing = np.zeros((len(MONTHS) - 1, n_history), dtype=bool)
    changing.flat[history_rng.choice(changing.size, n_changes, replace=False)] = True
    arpu_rows, traffic_rows, event_rows = [], [], []
    revenue_history, voice_history, data_history = [], [], []
    for month_index, month in enumerate(MONTHS):
        before = revenue.copy()
        origins = current.copy()
        if month_index:
            mask = changing[month_index - 1]
            positions = np.flatnonzero(mask)
            current[mask] = (current[mask] + history_rng.integers(1, len(tariffs), len(positions))) % len(tariffs)
            revenue = (before * history_rng.uniform(.97, 1.03, n_history)).round(2)
            price_change = (fees[current[mask]] - fees[origins[mask]]) / fees[origins[mask]]
            # Observational history has both losses and gains, never pilot labels.
            rates = np.clip(.12 * np.tanh(price_change) + history_rng.normal(-.015, .18, len(positions)), -.55, .55)
            rates[history_rng.random(len(positions)) < .25] = 0
            revenue[mask] = (before[mask] * (1 + rates)).round(2)
            previous_arpu = np.mean(revenue_history[-3:], axis=0)
            previous_data = np.mean(data_history[-3:], axis=0)
            previous_voice = np.mean(voice_history[-3:], axis=0)
            event_rows.append(pd.DataFrame({
                "customer_id": ids[mask], "event_date": month.strftime("%Y-%m-%d"),
                "from_tariff": tariff_names[origins[mask]], "to_tariff": tariff_names[current[mask]],
                "arpu_before": before[mask], "arpu_after": revenue[mask],
                "arpu_segment": _arpu_segment(previous_arpu[mask]),
                "data_segment": _data_segment(previous_data[mask]),
                "call_segment": _call_segment(previous_voice[mask]),
            }))
        voice = (voice_base * history_rng.uniform(.75, 1.25, n_history)).round(2)
        data = (data_base * history_rng.uniform(.7, 1.3, n_history)).round(2)
        arpu_rows.append(pd.DataFrame({"customer_id": ids, "month": month.strftime("%Y-%m-%d"),
                                       "tariff_id": tariff_names[current], "arpu": revenue.copy()}))
        traffic_rows.append(pd.DataFrame({
            "customer_id": ids, "month": month.strftime("%Y-%m-%d"), "tariff_id": tariff_names[current],
            "voice_minutes": voice, "sms_count": history_rng.poisson(12, n_history),
            "data_mb": data, "device_type": devices,
        }))
        revenue_history.append(revenue.copy())
        voice_history.append(voice)
        data_history.append(data)
    changes = pd.concat(event_rows, ignore_index=True).sort_values(["customer_id", "event_date"]).reset_index(drop=True)
    changes.insert(0, "event_id", np.arange(1, len(changes) + 1))
    tables = {
        "customer_profile.csv": profile,
        "tariff_dictionary.csv": dictionary,
        "data/change_tariff.csv": changes,
        "data/traffic.csv": pd.concat(traffic_rows, ignore_index=True),
        "data/arpu_monthly.csv": pd.concat(arpu_rows, ignore_index=True),
        "data/dict_tariff.csv": tariffs,
    }
    features = []
    for filename, frame in tables.items():
        for column in frame:
            unit, description = DESCRIPTIONS[column]
            if filename == "data/change_tariff.csv" and column.endswith("_segment"):
                description += "; computed from up to three months BEFORE the event"
            features.append({"file": filename, "column": column, "dtype": str(frame[column].dtype),
                             "unit": unit, "description": description, "nullable": False,
                             "schema_source": "team schema based on brief sections 5 and 6"})
    tables["feature_dictionary.csv"] = pd.DataFrame(features)
    return tables


def validate_case_data(tables):
    profile = tables["customer_profile.csv"]
    tariffs = tables["data/dict_tariff.csv"]
    changes = tables["data/change_tariff.csv"]
    monthly = tables["data/arpu_monthly.csv"]
    traffic = tables["data/traffic.csv"]
    assert profile["customer_id"].is_unique and tariffs["tariff_id"].is_unique
    assert not set(profile["customer_id"]) & set(monthly["customer_id"])
    assert set(changes["customer_id"]) <= set(monthly["customer_id"])
    for column in ["current_tariff"]:
        assert set(profile[column]) <= set(tariffs["tariff_id"])
    for frame, columns in [(changes, ["from_tariff", "to_tariff"]), (monthly, ["tariff_id"]), (traffic, ["tariff_id"])]:
        for column in columns:
            assert set(frame[column]) <= set(tariffs["tariff_id"])
    assert changes["event_id"].is_unique and not changes.duplicated(["customer_id", "event_date"]).any()
    assert (changes["from_tariff"] != changes["to_tariff"]).all()
    assert (profile["arpu_segment"] == _arpu_segment(profile["ARPU_3m_avg"])).all()
    assert (profile["data_segment"] == _data_segment(profile["DATA_3m_avg"])).all()
    assert (profile["call_segment"] == _call_segment(profile["CALL_3m_avg"])).all()
    for frame in [monthly, traffic]:
        assert not frame.duplicated(["customer_id", "month"]).any()
        assert frame["month"].between("2026-01-01", "2026-08-01").all()
    assert monthly[["customer_id", "month", "tariff_id"]].equals(traffic[["customer_id", "month", "tariff_id"]])
    events = changes.copy()
    events["previous_month"] = (pd.to_datetime(events["event_date"]) - pd.offsets.MonthBegin()).dt.strftime("%Y-%m-%d")
    before = events.merge(monthly, left_on=["customer_id", "previous_month"], right_on=["customer_id", "month"], validate="many_to_one")
    after = events.merge(monthly, left_on=["customer_id", "event_date"], right_on=["customer_id", "month"], validate="many_to_one")
    assert len(before) == len(after) == len(changes)
    assert (before["arpu_before"] == before["arpu"]).all() and (before["from_tariff"] == before["tariff_id"]).all()
    assert (after["arpu_after"] == after["arpu"]).all() and (after["to_tariff"] == after["tariff_id"]).all()
    for frame in tables.values():
        assert not frame.isna().any().any()
        numeric = frame.select_dtypes(include="number")
        assert np.isfinite(numeric.to_numpy()).all() and (numeric >= 0).all().all()


def write_case_data(directory=DEFAULT_DIRECTORY, seed=2026):
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError("Output directory already exists; choose a new --output")
    tables = build_case_data(seed=seed)
    validate_case_data(tables)
    directory.mkdir(parents=True)
    manifest = {"source": "team_generated_synthetic_case", "brief_sha256": BRIEF_SHA256,
                "seed": seed, "snapshot_date": SNAPSHOT, "history_months": [m.strftime("%Y-%m-%d") for m in MONTHS],
                "history_population": 6000, "populations_overlap": False, "official_dataset": False,
                "schema_version": 1, "files": {}}
    for filename, frame in tables.items():
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, lineterminator="\n")
        manifest["files"][filename] = {"rows": len(frame), "columns": list(frame.columns),
                                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    result = write_case_data(args.output, args.seed)
    print("TEAM SYNTHETIC CASE: not the organizer's dataset")
    for filename, details in result["files"].items():
        print(f"{filename}: {details['rows']} rows")
