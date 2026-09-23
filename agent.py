"""Pilot-driven campaign planner using only the documented public env API.

No simulator imports, hidden-state access or fixed effects.
The expected public table/result shapes are described in README.md.
"""

from dataclasses import dataclass
import math
import os
from numbers import Integral
import time

import numpy as np

from candidates import candidate_orders, cohort_priority, load_history, prepare_tables
from llm_advisor import rank_cohorts


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

    def update(self, result, mean_arpu=1.0):
        count = result["n_customers"]
        if isinstance(count, bool) or not isinstance(count, Integral) or not 10 <= count <= 200:
            raise ValueError("Invalid public pilot size")
        count = int(count)
        if "observed_lift_ratio" in result:
            # The public API reports a relative effect, without a sample std.
            # Use cohort ARPU to compare the estimate with contact costs.
            # A unit relative std is a conservative uncertainty prior, not a
            # learned/hidden effect or a claim of calibrated confidence.
            scale = float(mean_arpu)
            ratio = float(result["observed_lift_ratio"])
            if not math.isfinite(scale) or scale < 0:
                raise ValueError("Invalid public cohort ARPU")
            mean, std = ratio * scale, scale
        else:
            # Independent unit-test environments can report absolute moments.
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


class _PlanGuard:
    """Check public API limits before spending resources or returning a plan."""

    FILTERS = {
        "filter_arpu_segment": "arpu_segment",
        "filter_data_segment": "data_segment",
        "filter_call_segment": "call_segment",
        "filter_current_tariff": "current_tariff",
    }
    MAX_PILOTS = 20
    MIN_PILOT_SIZE = 10
    MAX_PILOT_SIZE = 200
    MAX_CAMPAIGNS = 10
    MAX_CAMPAIGN_SIZE = 5000

    def __init__(self, env, profile, tariff_ids, costs):
        self.env = env
        self.profile = profile
        self.tariff_ids = set(tariff_ids)
        self.costs = costs

    def audience_size(self, campaign):
        mask = self.audience_mask(campaign)
        return int(mask.sum()) if mask is not None else 0

    def audience_mask(self, campaign):
        if not isinstance(campaign, dict) or set(campaign) - {*self.FILTERS, "target_tariff", "channel", "campaign_name"}:
            return None
        mask = np.ones(len(self.profile), dtype=bool)
        for field, column in self.FILTERS.items():
            value = campaign.get(field)
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                return None
            if field == "filter_current_tariff":
                values = [item.strip() for item in value.split(";")]
                if any(not item for item in values) or not set(values).issubset(set(self.profile[column].dropna())):
                    return None
                selected = self.profile[column].isin(values)
            else:
                if value not in set(self.profile[column].dropna()):
                    return None
                selected = self.profile[column].eq(value).fillna(False)
            mask &= selected.to_numpy(dtype=bool)
        return mask

    def pilot_ok(self, campaign, size):
        if (campaign.get("target_tariff") not in self.tariff_ids
                or campaign.get("channel") not in self.costs
                or not isinstance(size, int) or isinstance(size, bool)
                or not self.MIN_PILOT_SIZE <= size <= self.MAX_PILOT_SIZE
                or self.env.pilots_left <= 0
                or len(self.env.pilot_history) >= self.MAX_PILOTS):
            return False
        audience = self.audience_size(campaign)
        return (size <= audience and size <= self.env.remaining_contacts
                and size * self.costs[campaign["channel"]] <= self.env.remaining_budget)

    def campaigns_ok(self, campaigns):
        if not isinstance(campaigns, list) or not 1 <= len(campaigns) <= self.MAX_CAMPAIGNS:
            return False
        contacts = 0
        budget = 0.0
        seen = np.zeros(len(self.profile), dtype=bool)
        for campaign in campaigns:
            selected = self.audience_mask(campaign)
            if selected is None or np.any(seen & selected):
                return False
            seen |= selected
            tariff = campaign.get("target_tariff")
            channel = campaign.get("channel")
            if tariff not in self.tariff_ids or channel not in self.costs:
                return False
            size = int(selected.sum())
            if not 1 <= size <= self.MAX_CAMPAIGN_SIZE:
                return False
            contacts += size
            budget += size * self.costs[channel]
            if contacts > self.env.remaining_contacts or budget > self.env.remaining_budget:
                return False
        return True


def _cohorts(profile):
    """Disjoint, representable audiences; never silently truncate at 5000."""
    result = []

    def append(filters, indices):
        if 10 <= len(indices) <= 5000:
            filters = {key: value for key, value in filters.items() if value != ""}
            result.append(_Cohort(filters, np.asarray(indices), float(profile.loc[indices, "predicted_arpu"].mean())))

    # A tariff transition has a different meaning for different starting
    # tariffs. Keep those populations separate before spending pilot contacts.
    for (tariff, arpu), frame in profile.groupby(["current_tariff", "arpu_segment"], sort=True):
        base = {"filter_current_tariff": tariff, "filter_arpu_segment": arpu}
        if len(frame) <= 5000:
            append(base, frame.index.to_numpy())
            continue
        for data, part in frame.groupby("data_segment", sort=True):
            if len(part) > 5000:
                for call, subset in part.groupby("call_segment", sort=True):
                    append({**base, "filter_data_segment": data, "filter_call_segment": call}, subset.index.to_numpy())
            else:
                append({**base, "filter_data_segment": data}, part.index.to_numpy())
    return sorted(result, key=lambda c: -(c.mean_arpu * len(c.indices)))


def _fallback_cohorts(profile):
    """Relax fine segments until a disjoint set can support a valid pilot."""
    columns = ["arpu_segment", "data_segment", "current_tariff", "call_segment"]
    for depth in range(len(columns), -1, -1):
        selected = columns[:depth]
        groups = profile.groupby(selected, sort=True) if selected else [((), profile)]
        cohorts = []
        for values, frame in groups:
            if 10 <= len(frame) <= 5000:
                values = values if isinstance(values, tuple) else (values,)
                filters = {f"filter_{column}": value for column, value in zip(selected, values) if value != ""}
                cohorts.append(_Cohort(filters, frame.index.to_numpy(), float(frame["predicted_arpu"].mean())))
        if cohorts:
            # Mixing resolutions could produce overlapping final audiences.
            return sorted(cohorts, key=lambda cohort: len(cohort.indices))
    return []


def _fit_audience(profile, cohort, cap, minimal=False):
    """Fit by real categorical filters, without inventing a campaign size key."""
    if not minimal and len(cohort.indices) <= cap:
        return dict(cohort.filters), len(cohort.indices)
    frame = profile.loc[cohort.indices]
    if minimal:
        pieces = sorted(frame.groupby(["current_tariff", "call_segment"], sort=True), key=lambda item: len(item[1]))
        for (tariff, call), part in pieces:
            if len(part) <= cap:
                filters = {**cohort.filters, "filter_current_tariff": tariff, "filter_call_segment": call}
                return {key: value for key, value in filters.items() if value != ""}, len(part)
        # Missing optional split keys do not invalidate the tested audience.
        if len(cohort.indices) <= cap:
            return dict(cohort.filters), len(cohort.indices)
        return None
    names, count = [], 0
    groups = sorted(frame.groupby("current_tariff", sort=True), key=lambda item: -item[1]["predicted_arpu"].mean())
    for tariff, part in groups:
        if count + len(part) <= cap:
            names.append(tariff)
            count += len(part)
    if names:
        filters = {**cohort.filters, "filter_current_tariff": ";".join(names)}
        return {key: value for key, value in filters.items() if value != ""}, count
    # A whole tariff does not fit. A single call segment may still fit.
    for tariff, part in groups:
        for call, subset in part.groupby("call_segment", sort=True):
            if len(subset) <= cap:
                filters = {**cohort.filters, "filter_current_tariff": tariff, "filter_call_segment": call}
                return {key: value for key, value in filters.items() if value != ""}, len(subset)
    return None


class Agent:
    def act(self, env):
        self.trace = []
        self.llm_status = "disabled"
        deadline = time.monotonic() + 540.0
        profile, tariffs = prepare_tables(env.customer_profile, env.tariffs)
        channels = env.channels
        costs = {}
        if isinstance(channels, dict):
            for name, details in channels.items():
                try:
                    cost = float(details["cost_per_contact"] if "cost_per_contact" in details else details["cost"])
                    if isinstance(name, str) and name and math.isfinite(cost) and cost >= 0:
                        costs[name] = cost
                except (TypeError, ValueError, KeyError):
                    continue
        if not costs:
            raise ValueError("No public channel with a finite nonnegative cost")
        channel_order = sorted(costs, key=lambda name: (costs[name], name))
        multipliers = {}
        for name in channel_order:
            try:
                value = float(channels[name]["conversion_multiplier"])
                if math.isfinite(value) and value > 0:
                    multipliers[name] = value
            except (TypeError, ValueError, KeyError):
                pass
        cheapest = channel_order[0]
        cohorts = _cohorts(profile)
        if not cohorts:
            cohorts = _fallback_cohorts(profile)
        tariff_ids = sorted(tariffs["tariff_id"].tolist())
        if not tariff_ids:
            raise ValueError("No public tariffs")
        guard = _PlanGuard(env, profile, tariff_ids, costs)
        if not cohorts:
            raise ValueError("No audience can support the required minimum pilot")
        history = load_history()
        cohorts = [cohorts[index] for index in cohort_priority(profile, cohorts, history)]
        target_orders = candidate_orders(profile, tariffs, cohorts, history)
        # The LLM sees aggregate public hypotheses, never rows, IDs or env.
        # It can reorder exploration, but cannot authorize a final campaign.
        if os.environ.get("CEREBRUM_LLM") == "1":
            prices = tariffs.set_index("tariff_id")["monthly_fee"].to_dict()
            shortlist = [{"id": i, "filters": cohort.filters,
                          "audience_size": len(cohort.indices), "mean_arpu": cohort.mean_arpu,
                          "target_options": [{"tariff": name, "price": float(prices[name])}
                                             for name in target_orders[i]]}
                         for i, cohort in enumerate(cohorts[:20])]
            order, self.llm_status = rank_cohorts(
                shortlist, enabled=True, timeout=max(0.0, min(8.0, deadline - time.monotonic())))
            order += list(range(len(shortlist), len(cohorts)))
            cohorts = [cohorts[i] for i in order]
            target_orders = [target_orders[i] for i in order]
        observations = {}
        attempts = min(20, max(0, int(env.pilots_left)))
        exploration_rounds = min(10, max(1, attempts // 2))
        start_budget = float(env.remaining_budget)
        start_contacts = int(env.remaining_contacts)
        if not math.isfinite(start_budget) or start_budget < 0 or start_contacts < 0:
            raise ValueError("Invalid public resource balances")
        calls_left = attempts

        def try_pilot(observation, cohort, sample):
            nonlocal calls_left
            if calls_left <= 0 or time.monotonic() >= deadline:
                return False
            pilot = {"target_tariff": observation.tariff, "channel": observation.channel, **cohort.filters}
            if not guard.pilot_ok(pilot, sample):
                return False
            calls_left -= 1  # Bound failed attempts too, even when the service does not charge them.
            phase = "confirmation" if observation.count else (
                "channel_comparison" if observation.channel != cheapest else "exploration")
            try:
                result = env.run_pilot(n_customers=sample, **pilot)
                if not isinstance(result, dict) or result.get("n_customers") != sample:
                    return False
                if any(key in result and result[key] != value for key, value in pilot.items()):
                    return False
                reported_cost = result.get("cost", result.get("communication_cost"))
                if reported_cost is not None and not math.isclose(
                    float(reported_cost), sample * costs[observation.channel], abs_tol=1e-7
                ):
                    return False
                observation.update(result, cohort.mean_arpu)
            except Exception:
                # Charged failed/malformed responses stay charged in env; reread balances next time.
                return False
            observations[(observation.cohort, observation.tariff, observation.channel)] = observation
            self.trace.append({"phase": phase, "target_tariff": observation.tariff,
                               "channel": observation.channel, "sample": sample,
                               "observed_customers": observation.count,
                               "mean_gain": observation.mean, "standard_error": observation.error,
                               "lower_net_per_contact": observation.mean - 2 * observation.error - costs[observation.channel],
                               "filters": dict(cohort.filters)})
            return True
        # Reserve enough resources for at least one representable final audience.
        reserve_contacts = min((
            len(part) for cohort in cohorts
            for _, part in profile.loc[cohort.indices].groupby(["current_tariff", "call_segment"], sort=True)
        ), default=min(len(cohort.indices) for cohort in cohorts))
        exploration_budget = max(0.0, min(start_budget * .25, start_budget - reserve_contacts * costs[cheapest]))
        exploration_contacts = min(2000, max(0, start_contacts - reserve_contacts))

        def optimism(observation):
            return (observation.mean + 2 * observation.error - costs[observation.channel]) * len(cohorts[observation.cohort].indices)

        def new_target(cohort_index):
            for tariff in target_orders[cohort_index]:
                key = (cohort_index, tariff, cheapest)
                if key not in observations:
                    return _Observation(*key)
            return None

        for step in range(attempts):
            if time.monotonic() >= deadline or env.pilots_left <= 0 or calls_left <= 0:
                break
            observation = None
            if step < exploration_rounds or not observations:
                observation = new_target(step % len(cohorts))
            else:
                ranked = sorted((item for item in observations.values() if optimism(item) > 0),
                                key=optimism, reverse=True)
                if not ranked:
                    break  # Do not spend confirmations on clearly losing candidates.
                choices = []
                for previous in ranked:
                    size = len(cohorts[previous.cohort].indices)
                    # Reducing uncertainty is useful only while another sample can change a decision.
                    if previous.error > 0 and previous.count < min(200, size):
                        choices.append((2 * previous.error * size, previous))
                    lower = previous.mean - 2 * previous.error - costs[previous.channel]
                    # First resolve the uncertainty in an existing hypothesis.
                    # Otherwise the 20 pilots can all become noisy first tries.
                    if previous.count >= min(200, size) or lower > 0:
                        other = new_target(previous.cohort)
                        if other is not None:
                            choices.append((optimism(previous), other))
                    for channel in channel_order:
                        key = (previous.cohort, previous.tariff, channel)
                        if key in observations:
                            continue
                        # Paid exploration requires confirmed positive evidence;
                        # a noisy small pilot is not enough to justify its cost.
                        if lower <= 0 or previous.count < min(80, size):
                            continue
                        # This is a hypothesis for a pilot, not an assumed channel effect.
                        # Published multipliers rank experiments, never replace
                        # measured evidence for a paid final campaign.
                        scale = (multipliers[channel] / multipliers[previous.channel]
                                 if channel in multipliers and previous.channel in multipliers else 1.0)
                        potential = scale * (previous.mean + 2 * previous.error) - costs[channel]
                        value = size * (potential - max(0.0, lower))
                        if potential > 0 and value > 30 * costs[channel]:
                            choices.append((value, _Observation(*key)))
                if not choices:
                    break
                observation = max(choices, key=lambda item: item[0])[1]
            if observation is None:
                continue
            cohort = cohorts[observation.cohort]
            # Larger follow-up samples where estimated profit is hard to resolve.
            desired = 30
            if observation.count:
                margin = max(1.0, abs(observation.mean - costs[observation.channel]))
                desired = max(80, min(200, math.ceil(2 * observation.error ** 2 * observation.count / margin ** 2)))
            # Keep the best measured campaign affordable before spending on more information.
            protected_contacts, protected_budget = reserve_contacts, reserve_contacts * costs[cheapest]
            if step >= exploration_rounds:
                profitable = [item for item in observations.values()
                              if item.mean - 2 * item.error - costs[item.channel] > 0]
                if profitable:
                    best = max(profitable, key=lambda item: (
                        item.mean - 2 * item.error - costs[item.channel]) * len(cohorts[item.cohort].indices))
                    best_cost = costs[best.channel]
                    affordable = min(5000, int(env.remaining_contacts),
                                     math.floor(float(env.remaining_budget) / best_cost) if best_cost else 5000)
                    fitted = _fit_audience(profile, cohorts[best.cohort], affordable)
                    if fitted:
                        protected_contacts = max(protected_contacts, fitted[1])
                        protected_budget = max(protected_budget, fitted[1] * best_cost)
            available_contacts = min(
                int(env.remaining_contacts) - protected_contacts,
                exploration_contacts - (start_contacts - int(env.remaining_contacts)),
            )
            available_budget = min(
                float(env.remaining_budget) - protected_budget,
                exploration_budget - (start_budget - float(env.remaining_budget)),
            )
            cap = min(200, len(cohort.indices), available_contacts)
            cost = costs[observation.channel]
            if cost:
                cap = min(cap, math.floor(max(0, available_budget) / cost))
            sample = min(desired, cap)
            if sample < 10:
                continue
            try_pilot(observation, cohort, int(sample))
        if not observations:
            # The exploration reserve may be too conservative for a small
            # account. Spend the minimum on one feasible pilot instead.
            for cohort in _fallback_cohorts(profile):
                if time.monotonic() >= deadline or env.pilots_left <= 0 or calls_left <= 0:
                    break
                for tariff in tariff_ids:
                    if time.monotonic() >= deadline or env.pilots_left <= 0 or calls_left <= 0:
                        break
                    if set(profile.loc[cohort.indices, "current_tariff"]) == {tariff} and len(tariff_ids) > 1:
                        continue
                    pilot = {"target_tariff": tariff, "channel": cheapest, **cohort.filters}
                    if not guard.pilot_ok(pilot, 10):
                        continue
                    final_cap = min(5000, int(env.remaining_contacts) - 10)
                    if costs[cheapest]:
                        final_cap = min(final_cap, math.floor(
                            (float(env.remaining_budget) - 10 * costs[cheapest]) / costs[cheapest]
                        ))
                    if _fit_audience(profile, cohort, final_cap, minimal=True) is None:
                        continue
                    fallback_cohort = len(cohorts)
                    observation = _Observation(fallback_cohort, tariff, cheapest)
                    if try_pilot(observation, cohort, 10):
                        cohorts.append(cohort)
                        break
                if observations:
                    break
        if not observations:
            raise RuntimeError("No successful pilot; no feasible pilot and final campaign")

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
                lower_margin = observation.mean - 2 * observation.error - cost
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
                    options.append(((observation.mean - 2 * observation.error - cost) * size, observation, filters))
            if not options:
                raise RuntimeError("Remaining resources cannot cover a representable final campaign")
            _, chosen, filters = max(options, key=lambda item: item[0])
            campaigns.append({"campaign_name": "Minimum-exposure pilot-based campaign", **filters,
                              "target_tariff": chosen.tariff, "channel": chosen.channel})
        if not guard.campaigns_ok(campaigns):
            raise RuntimeError("Final campaigns exceed public limits")
        return campaigns
