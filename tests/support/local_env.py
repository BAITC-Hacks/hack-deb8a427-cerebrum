"""Legacy team simulator retained only for independent unit tests.

The agent must not import this module or access simulator internals. This is a
testing fixture, not a security sandbox and not the organizer's scoring code.
"""

from copy import deepcopy
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path

import numpy as np
import pandas as pd

from tests.support.generate_demo_data import build_demo_data


FILTER_COLUMNS = {
    "filter_arpu_segment": "arpu_segment",
    "filter_data_segment": "data_segment",
    "filter_call_segment": "call_segment",
    "filter_current_tariff": "current_tariff",
}
CAMPAIGN_COLUMNS = ["campaign_name", *FILTER_COLUMNS, "target_tariff", "channel"]
CHANNEL_COSTS = {"push": 0.0, "sms": 4.0, "digital_ads": 22.0, "call": 160.0}
SCENARIOS = ("mixed", "adverse", "channel_shift")


@dataclass(frozen=True)
class Limits:
    budget: float = 100_000.0
    contacts: int = 15_000
    pilots: int = 20
    campaigns: int = 10
    campaign_size: int = 5_000
    pilot_min: int = 10
    pilot_max: int = 200


class LocalEnvironment:
    def __init__(self, customer_profile, tariffs, seed=42, scenario="mixed", limits=None):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}")
        required = {"customer_id", "predicted_arpu", *FILTER_COLUMNS.values()}
        if not required.issubset(customer_profile.columns):
            raise ValueError(f"Missing profile columns: {sorted(required - set(customer_profile.columns))}")
        if not {"tariff_id", "monthly_fee"}.issubset(tariffs.columns):
            raise ValueError("Tariffs require tariff_id and monthly_fee")
        if customer_profile.empty or tariffs.empty:
            raise ValueError("Empty customer or tariff table")
        if customer_profile[list(required)].isna().any().any():
            raise ValueError("Profile contains missing values")
        if tariffs[["tariff_id", "monthly_fee"]].isna().any().any():
            raise ValueError("Tariffs contain missing values")
        if customer_profile["customer_id"].duplicated().any() or tariffs["tariff_id"].duplicated().any():
            raise ValueError("Customer and tariff IDs must be unique")
        if not set(customer_profile["current_tariff"]).issubset(set(tariffs["tariff_id"])):
            raise ValueError("Profile references an unknown tariff")
        for values in [customer_profile["predicted_arpu"], tariffs["monthly_fee"]]:
            if not np.isfinite(values.to_numpy(dtype=float)).all() or (values < 0).any():
                raise ValueError("ARPU and prices must be finite and nonnegative")
        for column in FILTER_COLUMNS.values():
            if not customer_profile[column].map(lambda x: isinstance(x, str) and bool(x) and ";" not in x).all():
                raise ValueError(f"Invalid categorical values: {column}")
        self._limits = limits or Limits()
        self._profile = customer_profile.reset_index(drop=True).copy(deep=True)
        self._tariffs = tariffs.reset_index(drop=True).copy(deep=True)
        self._budget = float(self._limits.budget)
        self._contacts = self._limits.contacts
        self._history = []
        self._best = np.full(len(self._profile), np.nan)
        self._finalized = False
        self._rng = np.random.default_rng(seed)
        self._tariff_index = {value: i for i, value in enumerate(tariffs["tariff_id"])}
        self._channel_index = {value: i for i, value in enumerate(CHANNEL_COSTS)}
        self._origin = self._profile["current_tariff"].map(self._tariff_index).to_numpy()
        self._arpu_codes, arpu_names = pd.factorize(self._profile["arpu_segment"], sort=True)
        self._data_codes, data_names = pd.factorize(self._profile["data_segment"], sort=True)
        count = len(tariffs)
        # Randomized response surfaces; no relationship to the official effects.
        shape = (count, count, len(arpu_names), len(data_names), len(CHANNEL_COSTS))
        target_quality = self._rng.normal(.01, .035, (1, count, 1, 1, 1))
        segment_quality = self._rng.normal(0, .04, (1, count, len(arpu_names), len(data_names), 1))
        channel_quality = self._rng.normal(0, .012, (1, count, 1, 1, len(CHANNEL_COSTS)))
        self._rates = target_quality + segment_quality + channel_quality + self._rng.normal(0, .025, shape)
        self._rates *= np.array([.5, .65, .85, 1.2])
        if scenario == "adverse":
            self._rates = -np.abs(self._rates) - .02
        elif scenario == "channel_shift":
            self._rates = self._rates[..., self._rng.permutation(len(CHANNEL_COSTS))]
        self._noise = self._rng.normal(0, .045, (len(self._profile), count))
        if scenario == "adverse":
            self._noise = -np.abs(self._noise)

    @property
    def customer_profile(self):
        return self._profile.copy(deep=True)

    @property
    def tariffs(self):
        return self._tariffs.copy(deep=True)

    @property
    def channels(self):
        return {name: {"cost": cost} for name, cost in CHANNEL_COSTS.items()}

    @property
    def remaining_budget(self):
        return self._budget

    @property
    def remaining_contacts(self):
        return self._contacts

    @property
    def pilots_left(self):
        return self._limits.pilots - len(self._history)

    @property
    def pilot_history(self):
        return deepcopy(self._history)

    def _selection(self, campaign):
        if not isinstance(campaign, dict):
            raise ValueError("Campaign must be a dictionary")
        unknown = set(campaign) - set(CAMPAIGN_COLUMNS)
        if unknown:
            raise ValueError(f"Unknown campaign fields: {sorted(unknown)}")
        if campaign.get("target_tariff") not in self._tariff_index:
            raise ValueError("Unknown target_tariff")
        if campaign.get("channel") not in CHANNEL_COSTS:
            raise ValueError("Unknown channel")
        if "campaign_name" in campaign and not isinstance(campaign["campaign_name"], str):
            raise ValueError("campaign_name must be a string")
        mask = np.ones(len(self._profile), dtype=bool)
        for field, column in FILTER_COLUMNS.items():
            value = campaign.get(field, "")
            if value == "":
                continue
            if not isinstance(value, str):
                raise ValueError(f"{field} must be a semicolon-separated string")
            tokens = value.split(";")
            if any(not token for token in tokens) or not set(tokens).issubset(set(self._profile[column])):
                raise ValueError(f"Unknown or empty value in {field}")
            mask &= self._profile[column].isin(tokens).to_numpy()
        indices = np.flatnonzero(mask)
        if not len(indices):
            raise ValueError("Campaign has an empty audience")
        return indices

    def _outcomes(self, indices, target, channel):
        target_index = self._tariff_index[target]
        rates = self._rates[
            self._origin[indices], target_index, self._arpu_codes[indices],
            self._data_codes[indices], self._channel_index[channel],
        ] + self._noise[indices, target_index]
        # Same-tariff contacts have no revenue effect, but still cost money.
        rates = np.where(self._origin[indices] == target_index, 0, rates)
        return self._profile["predicted_arpu"].to_numpy()[indices] * np.clip(rates, -.8, .8)

    def _record(self, indices, values, cost):
        previous = self._best[indices]
        self._best[indices] = np.where(np.isnan(previous), values, np.maximum(previous, values))
        self._budget -= cost
        self._contacts -= len(indices)

    def run_pilot(self, *, target_tariff, channel, n_customers, **filters):
        if self._finalized:
            raise ValueError("Evaluation is already finalized")
        if self.pilots_left <= 0:
            raise ValueError("Pilot limit reached")
        if isinstance(n_customers, bool) or not isinstance(n_customers, Integral):
            raise ValueError("Pilot size must be an integer")
        if not self._limits.pilot_min <= n_customers <= self._limits.pilot_max:
            raise ValueError("Pilot size must be between 10 and 200")
        if set(filters) - set(FILTER_COLUMNS):
            raise ValueError("Unexpected pilot filter")
        campaign = {"target_tariff": target_tariff, "channel": channel, **filters}
        eligible = self._selection(campaign)
        cost = n_customers * CHANNEL_COSTS[channel]
        if n_customers > len(eligible) or n_customers > self._contacts or cost > self._budget:
            raise ValueError("Pilot exceeds audience, contacts or budget")
        selected = self._rng.choice(eligible, size=n_customers, replace=False)
        values = self._outcomes(selected, target_tariff, channel)
        result = {
            **campaign, "pilot_id": len(self._history) + 1,
            "n_customers": int(n_customers),
            "mean_arpu_uplift": float(values.mean()),
            "std_arpu_uplift": float(values.std(ddof=1)),
            "communication_cost": float(cost),
            "net_gain": float(values.sum() - cost),
        }
        self._record(selected, values, cost)
        self._history.append(deepcopy(result))
        return result

    def evaluate(self, campaigns):
        """Validate the entire plan before charging any final campaign."""
        if self._finalized:
            raise ValueError("Evaluation is already finalized")
        if not self._history:
            raise ValueError("At least one successful pilot is required")
        if not isinstance(campaigns, list) or not 1 <= len(campaigns) <= self._limits.campaigns:
            raise ValueError("Return between 1 and 10 campaigns")
        prepared = []
        total_cost = 0.0
        total_contacts = 0
        for i, campaign in enumerate(campaigns, 1):
            try:
                selected = self._selection(campaign)
                if len(selected) > self._limits.campaign_size:
                    raise ValueError("Audience exceeds 5000")
                cost = len(selected) * CHANNEL_COSTS[campaign["channel"]]
                total_cost += cost
                total_contacts += len(selected)
                if total_cost > self._budget or total_contacts > self._contacts:
                    raise ValueError("Total budget or contact limit exceeded")
                prepared.append((campaign, selected, cost))
            except (TypeError, ValueError) as error:
                raise ValueError(f"Campaign {i} rejected: {error}") from error
        for campaign, selected, cost in prepared:
            values = self._outcomes(selected, campaign["target_tariff"], campaign["channel"])
            self._record(selected, values, cost)
        self._finalized = True
        return {**self.summary(), "campaigns_count": len(campaigns),
                "campaign_sizes": [len(selected) for _, selected, _ in prepared]}

    def summary(self):
        gross = float(np.nansum(self._best))
        spent = self._limits.budget - self._budget
        baseline = float(self._profile["predicted_arpu"].sum())
        return {
            "baseline_arpu": baseline, "gross_uplift": gross,
            "communication_cost": spent, "net_gain": gross - spent,
            "total_arpu_after_costs": baseline + gross - spent,
            "contacts_used": self._limits.contacts - self._contacts,
            "unique_customers": int(np.isfinite(self._best).sum()),
            "pilots_count": len(self._history), "remaining_budget": self._budget,
            "remaining_contacts": self._contacts,
        }


def create_environment(seed=42, scenario="mixed", data_dir=None):
    if data_dir is None:
        profile, tariffs = build_demo_data()
    else:
        data_dir = Path(data_dir)
        profile = pd.read_csv(data_dir / "customer_profile.csv", dtype={"current_tariff": str})
        tariffs = pd.read_csv(data_dir / "tariff_dictionary.csv", dtype={"tariff_id": str})
    return LocalEnvironment(profile, tariffs, seed=seed, scenario=scenario)
