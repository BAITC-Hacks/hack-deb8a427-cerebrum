"""Behavioral checks use the public contract, never simulator effect constants."""

import unittest

import numpy as np
import pandas as pd

from agent import Agent, _PlanGuard
from generate_demo_data import build_demo_data
from local_env import FILTER_COLUMNS, Limits, LocalEnvironment, SCENARIOS


PUBLIC_API = frozenset({
    "customer_profile", "tariffs", "channels", "remaining_budget",
    "remaining_contacts", "pilots_left", "pilot_history", "run_pilot",
})


class PublicOnlyEnvironment:
    """Fail immediately if Agent asks for an undocumented environment member."""

    def __init__(self, backend):
        object.__setattr__(self, "backend", backend)

    def __getattribute__(self, name):
        if name not in PUBLIC_API:
            raise AssertionError(f"Agent accessed a non-public environment member: {name}")
        return getattr(object.__getattribute__(self, "backend"), name)


class RewardEnvironment:
    """Independent public API double; rewards deliberately differ from the simulator."""

    def __init__(self, favored_tariff):
        self.customer_profile = pd.DataFrame({
            "customer_id": range(120), "current_tariff": ["original"] * 120,
            "arpu_segment": ["HIGH"] * 120, "data_segment": ["HEAVY"] * 120,
            "call_segment": ["LOW"] * 60 + ["HIGH"] * 60,
            "predicted_arpu": [1000.0] * 120,
        })
        self.tariffs = pd.DataFrame({"tariff_id": ["original", "left", "right"], "monthly_fee": [1, 2, 3]})
        self.channels = {"push": {"cost": 0.0}}
        self.remaining_budget = 100_000.0
        self.remaining_contacts = 15_000
        self.pilots_left = 20
        self.pilot_history = []
        self.favored_tariff = favored_tariff

    def run_pilot(self, *, target_tariff, channel, n_customers, **filters):
        if not 10 <= n_customers <= 200 or self.pilots_left <= 0:
            raise ValueError("Invalid pilot")
        if target_tariff not in set(self.tariffs["tariff_id"]) or channel not in self.channels:
            raise ValueError("Unknown action")
        mean = 100.0 if target_tariff == self.favored_tariff else -100.0
        result = {"n_customers": n_customers, "mean_arpu_uplift": mean, "std_arpu_uplift": 0.0}
        self.pilots_left -= 1
        self.remaining_contacts -= n_customers
        self.pilot_history.append({"target_tariff": target_tariff, "channel": channel, **filters, **result})
        return result


class PaidOnlyEnvironment(PublicOnlyEnvironment):
    """Expose only SMS while keeping the real simulator's resource accounting."""

    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        return {"sms": value["sms"]} if name == "channels" else value


class UncertainRewardEnvironment(RewardEnvironment):
    def run_pilot(self, **kwargs):
        result = super().run_pilot(**kwargs)
        result["std_arpu_uplift"] = 50.0
        self.pilot_history[-1]["std_arpu_uplift"] = 50.0
        return result


class IncompleteView(PublicOnlyEnvironment):
    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if name == "customer_profile":
            return value.drop(columns=["arpu_segment", "data_segment", "call_segment", "predicted_arpu"])
        if name == "tariffs":
            return value.drop(columns=["monthly_fee"])
        return value


def audience(profile, campaign):
    selected = np.ones(len(profile), dtype=bool)
    for field, column in FILTER_COLUMNS.items():
        value = campaign.get(field, "")
        if value:
            selected &= profile[column].isin(value.split(";")).to_numpy()
    return set(profile.loc[selected, "customer_id"])


class AgentTests(unittest.TestCase):
    def assert_valid_plan(self, env, plan):
        self.assertGreaterEqual(len(env.pilot_history), 1)
        self.assertLessEqual(len(env.pilot_history), 20)
        self.assertTrue(all(10 <= p["n_customers"] <= 200 for p in env.pilot_history))
        self.assertGreaterEqual(len(plan), 1)
        self.assertLessEqual(len(plan), 10)
        previous = set()
        for campaign in plan:
            self.assertIn(campaign["target_tariff"], set(env.tariffs["tariff_id"]))
            self.assertIn(campaign["channel"], env.channels)
            selected = audience(env.customer_profile, campaign)
            self.assertGreater(len(selected), 0)
            self.assertLessEqual(len(selected), 5000)
            self.assertFalse(previous & selected, "Final audiences must be disjoint")
            previous |= selected
            self.assertTrue(any(
                p["target_tariff"] == campaign["target_tariff"] and p["channel"] == campaign["channel"]
                for p in env.pilot_history
            ), "Every final tariff/channel must have successful pilot evidence")
        metrics = env.evaluate(plan)
        self.assertLessEqual(metrics["contacts_used"], 15_000)
        self.assertLessEqual(metrics["communication_cost"], 100_000)
        self.assertGreaterEqual(metrics["remaining_contacts"], 0)
        self.assertGreaterEqual(metrics["remaining_budget"], 0)
        self.assertEqual(metrics["contacts_used"], sum(metrics["campaign_sizes"]) + sum(
            p["n_customers"] for p in env.pilot_history
        ))
        return metrics

    def test_public_api_only_and_limits_on_varied_response_surfaces(self):
        profile, tariffs = build_demo_data()
        for scenario in SCENARIOS:
            for seed in (7, 91):
                with self.subTest(scenario=scenario, seed=seed):
                    env = LocalEnvironment(profile, tariffs, seed=seed, scenario=scenario)
                    plan = Agent().act(PublicOnlyEnvironment(env))
                    self.assert_valid_plan(env, plan)

    def test_changed_pilot_results_change_decision_on_identical_public_data(self):
        plans = []
        for favorite in ("left", "right"):
            env = RewardEnvironment(favorite)
            plan = Agent().act(PublicOnlyEnvironment(env))
            self.assertGreaterEqual(len(env.pilot_history), 2)
            self.assertEqual({row["target_tariff"] for row in plan}, {favorite})
            plans.append(plan)
        self.assertNotEqual(plans[0], plans[1])

    def test_all_negative_results_return_one_minimum_exposure_campaign(self):
        env = RewardEnvironment(None)
        plan = Agent().act(PublicOnlyEnvironment(env))
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(audience(env.customer_profile, plan[0])), 60)
        self.assertGreater(len(env.pilot_history), 0)
        self.assertLess(len(env.pilot_history), 20)

    def test_uncertain_good_candidates_receive_larger_confirmation_samples(self):
        env = UncertainRewardEnvironment("left")
        plan = Agent().act(PublicOnlyEnvironment(env))
        samples = [row["n_customers"] for row in env.pilot_history]
        self.assertEqual(min(samples), 30)
        self.assertGreater(max(samples), min(samples))
        self.assertLess(len(samples), 20)
        self.assertEqual({row["target_tariff"] for row in plan}, {"left"})
        bad_samples = [row["n_customers"] for row in env.pilot_history if row["target_tariff"] == "right"]
        self.assertEqual(bad_samples, [30])

    def test_fixed_data_and_seed_are_reproducible(self):
        profile, tariffs = build_demo_data(1200, seed=51)
        env_a = LocalEnvironment(profile, tariffs, seed=901)
        env_b = LocalEnvironment(profile, tariffs, seed=901)
        plan_a = Agent().act(PublicOnlyEnvironment(env_a))
        plan_b = Agent().act(PublicOnlyEnvironment(env_b))
        self.assertEqual(plan_a, plan_b)
        self.assertEqual(env_a.pilot_history, env_b.pilot_history)
        self.assertEqual(env_a.evaluate(plan_a), env_b.evaluate(plan_b))

    def test_small_representable_datasets(self):
        for size in (10, 80, 650):
            with self.subTest(size=size):
                profile, tariffs = build_demo_data(size)
                profile["arpu_segment"] = "HIGH"
                profile["data_segment"] = "HEAVY"
                env = LocalEnvironment(profile, tariffs, seed=18)
                plan = Agent().act(PublicOnlyEnvironment(env))
                self.assert_valid_plan(env, plan)

    def test_no_successful_pilot_never_returns_unvalidated_plan(self):
        env = RewardEnvironment("left")
        env.pilots_left = 0
        with self.assertRaisesRegex(RuntimeError, "No successful pilot"):
            Agent().act(PublicOnlyEnvironment(env))

    def test_small_segments_are_combined_for_a_valid_fallback_pilot(self):
        profile, tariffs = build_demo_data(12)
        profile["arpu_segment"] = "HIGH"
        profile["data_segment"] = ["LITE"] * 6 + ["HEAVY"] * 6
        env = LocalEnvironment(profile, tariffs, seed=18)
        plan = Agent().act(PublicOnlyEnvironment(env))
        self.assert_valid_plan(env, plan)

    def test_paid_fallback_reserves_one_representable_final_contact(self):
        profile, tariffs = build_demo_data(12)
        profile["arpu_segment"] = "HIGH"
        profile["data_segment"] = ["LITE"] * 6 + ["HEAVY"] * 6
        profile["current_tariff"] = "tariff_1"
        profile["call_segment"] = ["LOW"] + ["HIGH"] * 11
        # The exploration allowance cannot afford even one SMS pilot.
        # A fallback can still spend 40 on a pilot and 4 on the final contact.
        env = LocalEnvironment(profile, tariffs, seed=18, limits=Limits(budget=44, contacts=11))
        plan = Agent().act(PaidOnlyEnvironment(env))
        metrics = self.assert_valid_plan(env, plan)
        self.assertEqual(metrics["pilots_count"], 1)
        self.assertEqual(metrics["campaign_sizes"], [1])
        self.assertEqual(metrics["communication_cost"], 44)
        self.assertEqual(metrics["contacts_used"], 11)

    def test_missing_optional_features_use_unrestricted_filters(self):
        env = RewardEnvironment("left")
        plan = Agent().act(IncompleteView(env))
        self.assertTrue(plan)
        self.assertGreater(len(env.pilot_history), 0)
        self.assertTrue(all(len(audience(env.customer_profile, row)) > 0 for row in plan))
        self.assertEqual({row["target_tariff"] for row in plan}, {"left"})

    def test_transient_pilot_error_and_malformed_charged_response_recover(self):
        for corruption in ("timeout", "nan", "missing", "wrong_size", "wrong_tariff"):
            with self.subTest(corruption=corruption):
                env = RewardEnvironment("left")
                original = env.run_pilot
                calls = []

                def unreliable(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == 1 and corruption == "timeout":
                        raise TimeoutError("temporary service failure")
                    result = original(**kwargs)
                    if len(calls) == 1:
                        if corruption == "nan":
                            result["mean_arpu_uplift"] = float("nan")
                        elif corruption == "missing":
                            del result["std_arpu_uplift"]
                        elif corruption == "wrong_size":
                            result["n_customers"] += 1
                        elif corruption == "wrong_tariff":
                            result["target_tariff"] = "unknown"
                    return result

                env.run_pilot = unreliable
                plan = Agent().act(PublicOnlyEnvironment(env))
                self.assertEqual({row["target_tariff"] for row in plan}, {"left"})
                self.assertLessEqual(len(calls), 20)
                self.assertGreaterEqual(env.remaining_contacts, 0)

    def test_permanently_failing_pilot_has_bounded_attempts(self):
        env = RewardEnvironment("left")
        calls = []

        def unavailable(**kwargs):
            calls.append(kwargs)
            raise ConnectionError("unavailable")

        env.run_pilot = unavailable
        with self.assertRaisesRegex(RuntimeError, "No successful pilot"):
            Agent().act(PublicOnlyEnvironment(env))
        self.assertGreater(len(calls), 0)
        self.assertLessEqual(len(calls), 20)

    def test_invalid_channel_costs_do_not_disable_usable_channels(self):
        env = RewardEnvironment("left")
        env.channels.update({"invalid": {"cost": float("nan")}, "missing": {}, "negative": {"cost": -1}})
        plan = Agent().act(PublicOnlyEnvironment(env))
        self.assertEqual({row["channel"] for row in plan}, {"push"})

    def test_final_guard_rejects_duplicate_empty_and_unknown_campaigns(self):
        env = RewardEnvironment("left")
        guard = _PlanGuard(env, env.customer_profile, env.tariffs["tariff_id"], {"push": 0})
        campaign = {"target_tariff": "left", "channel": "push"}
        self.assertTrue(guard.campaigns_ok([campaign]))
        self.assertFalse(guard.campaigns_ok([campaign, campaign.copy()]))
        self.assertFalse(guard.campaigns_ok([]))
        self.assertFalse(guard.campaigns_ok([{**campaign, "filter_arpu_segment": "missing"}]))
        self.assertFalse(guard.campaigns_ok([{**campaign, "n_customers": 10}]))


if __name__ == "__main__":
    unittest.main()
