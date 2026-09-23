"""Pilot-driven campaign planner using only the documented public env API.

No simulator imports, hidden-state access, external services or fixed effects.
The expected public table/result shapes are described in README.md.
"""

from dataclasses import dataclass
import math
import time

import numpy as np


@dataclass
class _Cohort:
    filters: dict
    indices: np.ndarray
    mean_arpu: float


@dataclass
class _Observation:
    cohort: int
    tariff: str
    channel: str
    count: int = 0
    total: float = 0.0
    squares: float = 0.0

    def update(self, result):
        count = int(result["n_customers"])
        mean = float(result["mean_arpu_uplift"])
        std = float(result["std_arpu_uplift"])
        if count < 10 or not all(math.isfinite(x) for x in [mean, std]) or std < 0:
            raise ValueError("Invalid public pilot result")
        self.count += count
        self.total += count * mean
        self.squares += (count - 1) * std * std + count * mean * mean

    @property
    def mean(self):
        return self.total / self.count

    @property
    def error(self):
        variance = max(0.0, (self.squares - self.total * self.total / self.count) / max(1, self.count - 1))
        return math.sqrt(variance / self.count)


def _cohorts(profile):
    """Disjoint, representable audiences; never silently truncate at 5000."""
    result = []

    def append(filters, indices):
        if 10 <= len(indices) <= 5000:
            result.append(_Cohort(filters, np.asarray(indices), float(profile.loc[indices, "predicted_arpu"].mean())))

    for (arpu, data), frame in profile.groupby(["arpu_segment", "data_segment"], sort=True):
        base = {"filter_arpu_segment": arpu, "filter_data_segment": data}
        if len(frame) <= 5000:
            append(base, frame.index.to_numpy())
            continue
        names, indices = [], []
        for tariff, part in frame.groupby("current_tariff", sort=True):
            if len(indices) + len(part) > 5000 and indices:
                append({**base, "filter_current_tariff": ";".join(names)}, indices)
                names, indices = [], []
            if len(part) > 5000:
                for call, subset in part.groupby("call_segment", sort=True):
                    append({**base, "filter_current_tariff": tariff, "filter_call_segment": call}, subset.index.to_numpy())
            else:
                names.append(tariff)
                indices.extend(part.index.tolist())
        if indices:
            append({**base, "filter_current_tariff": ";".join(names)}, indices)
    return sorted(result, key=lambda c: -(c.mean_arpu * len(c.indices)))


def _fit_audience(profile, cohort, cap, minimal=False):
    """Fit by real categorical filters, without inventing a campaign size key."""
    if not minimal and len(cohort.indices) <= cap:
        return dict(cohort.filters), len(cohort.indices)
    frame = profile.loc[cohort.indices]
    if minimal:
        pieces = sorted(frame.groupby(["current_tariff", "call_segment"], sort=True), key=lambda item: len(item[1]))
        for (tariff, call), part in pieces:
            if len(part) <= cap:
                return {**cohort.filters, "filter_current_tariff": tariff, "filter_call_segment": call}, len(part)
        return None
    names, count = [], 0
    groups = sorted(frame.groupby("current_tariff", sort=True), key=lambda item: -item[1]["predicted_arpu"].mean())
    for tariff, part in groups:
        if count + len(part) <= cap:
            names.append(tariff)
            count += len(part)
    if names:
        return {**cohort.filters, "filter_current_tariff": ";".join(names)}, count
    # A whole tariff does not fit. A single call segment may still fit.
    for tariff, part in groups:
        for call, subset in part.groupby("call_segment", sort=True):
            if len(subset) <= cap:
                return {**cohort.filters, "filter_current_tariff": tariff, "filter_call_segment": call}, len(subset)
    return None


class Agent:
    def act(self, env):
        deadline = time.monotonic() + 540.0
        profile = env.customer_profile.reset_index(drop=True)
        tariffs = env.tariffs.copy()
        channels = env.channels
        costs = {name: float(details["cost"]) for name, details in channels.items()}
        if not costs or any(not math.isfinite(cost) or cost < 0 for cost in costs.values()):
            raise ValueError("Public channel costs must be finite and nonnegative")
        channel_order = sorted(costs, key=lambda name: (costs[name], name))
        cheapest = channel_order[0]
        cohorts = _cohorts(profile)
        if not cohorts:
            raise ValueError("No representable audience of 10..5000 customers")
        tariff_ids = sorted(tariffs["tariff_id"].tolist())
        if not tariff_ids:
            raise ValueError("No public tariffs")
        # A stable exploration order is independent of evaluator seeds/effects.
        rng = np.random.default_rng(1729)
        target_orders = []
        for cohort in cohorts:
            current = set(profile.loc[cohort.indices, "current_tariff"])
            candidates = [t for t in tariff_ids if current != {t}] or tariff_ids
            target_orders.append(list(rng.permutation(candidates)))
        observations = {}
        attempts = min(20, max(0, int(env.pilots_left)))
        exploration_rounds = min(10, max(1, attempts // 2))
        start_budget = float(env.remaining_budget)
        start_contacts = int(env.remaining_contacts)
        # Reserve enough resources for at least one representable final audience.
        reserve_contacts = min(
            len(part) for cohort in cohorts
            for _, part in profile.loc[cohort.indices].groupby(["current_tariff", "call_segment"], sort=True)
        )
        exploration_budget = max(0.0, min(start_budget * .25, start_budget - reserve_contacts * costs[cheapest]))
        exploration_contacts = min(2000, max(0, start_contacts - reserve_contacts))

        def optimism(observation):
            return (observation.mean + 1.5 * observation.error - costs[observation.channel]) * len(cohorts[observation.cohort].indices)

        def new_target(cohort_index):
            for tariff in target_orders[cohort_index]:
                key = (cohort_index, tariff, cheapest)
                if key not in observations:
                    return _Observation(*key)
            return None

        for step in range(attempts):
            if time.monotonic() >= deadline or env.pilots_left <= 0:
                break
            observation = None
            if step < exploration_rounds or not observations:
                observation = new_target(step % len(cohorts))
            else:
                ranked = sorted(observations.values(), key=optimism, reverse=True)
                if step % 3 == 1:
                    # Test another channel; do not assume its response multiplier.
                    for previous in ranked:
                        for channel in channel_order:
                            key = (previous.cohort, previous.tariff, channel)
                            available = exploration_budget - (start_budget - float(env.remaining_budget))
                            if key not in observations and costs[channel] * 40 <= available:
                                observation = _Observation(*key)
                                break
                        if observation is not None:
                            break
                elif step % 3 == 2:
                    for previous in ranked:
                        observation = new_target(previous.cohort)
                        if observation is not None:
                            break
                if observation is None:
                    observation = ranked[0]
            if observation is None:
                continue
            cohort = cohorts[observation.cohort]
            # Larger follow-up samples where estimated profit is hard to resolve.
            desired = 60
            if observation.count:
                margin = max(1.0, abs(observation.mean - costs[observation.channel]))
                desired = max(80, min(200, math.ceil(2 * observation.error ** 2 * observation.count / margin ** 2)))
            available_contacts = min(
                int(env.remaining_contacts) - reserve_contacts,
                exploration_contacts - (start_contacts - int(env.remaining_contacts)),
            )
            available_budget = min(
                float(env.remaining_budget) - reserve_contacts * costs[cheapest],
                exploration_budget - (start_budget - float(env.remaining_budget)),
            )
            cap = min(200, len(cohort.indices), available_contacts)
            cost = costs[observation.channel]
            if cost:
                cap = min(cap, math.floor(max(0, available_budget) / cost))
            sample = min(desired, cap)
            if sample < 10:
                continue
            try:
                result = env.run_pilot(
                    target_tariff=observation.tariff, channel=observation.channel,
                    n_customers=int(sample), **cohort.filters,
                )
            except (ValueError, RuntimeError):
                # A failed attempt must never authorize an untested campaign.
                continue
            observation.update(result)
            observations[(observation.cohort, observation.tariff, observation.channel)] = observation
        if not observations:
            raise RuntimeError("No successful pilot; cannot return an evidence-based plan")

        budget = float(env.remaining_budget)
        contacts = int(env.remaining_contacts)
        campaigns, used_cohorts = [], set()
        for _ in range(10):
            options = []
            for observation in observations.values():
                if observation.cohort in used_cohorts:
                    continue
                cost = costs[observation.channel]
                cap = min(5000, contacts, math.floor(budget / cost) if cost else contacts)
                fitted = _fit_audience(profile, cohorts[observation.cohort], cap)
                if fitted is None:
                    continue
                filters, size = fitted
                lower_margin = observation.mean - observation.error - cost
                if lower_margin > 0:
                    options.append((lower_margin * size, observation, filters, size))
            if not options:
                break
            _, chosen, filters, size = max(options, key=lambda item: item[0])
            campaigns.append({"campaign_name": f"Pilot-based campaign {len(campaigns) + 1}",
                              **filters, "target_tariff": chosen.tariff, "channel": chosen.channel})
            used_cohorts.add(chosen.cohort)
            budget -= size * costs[chosen.channel]
            contacts -= size
        if not campaigns:
            # The case requires >=1 campaign even when every observed effect is bad.
            # Minimize exposure using the smallest representable tested subaudience.
            options = []
            for observation in observations.values():
                cost = costs[observation.channel]
                cap = min(5000, contacts, math.floor(budget / cost) if cost else contacts)
                fitted = _fit_audience(profile, cohorts[observation.cohort], cap, minimal=True)
                if fitted:
                    filters, size = fitted
                    options.append(((observation.mean - observation.error - cost) * size, observation, filters))
            if not options:
                raise RuntimeError("Remaining resources cannot cover a representable final campaign")
            _, chosen, filters = max(options, key=lambda item: item[0])
            campaigns.append({"campaign_name": "Minimum-exposure pilot-based campaign", **filters,
                              "target_tariff": chosen.tariff, "channel": chosen.channel})
        return campaigns
