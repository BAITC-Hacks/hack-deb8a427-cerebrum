"""Behavioral checks use the public contract, never simulator effect constants."""

import unittest

import numpy as np
import pandas as pd

from agent import Agent
from generate_demo_data import build_demo_data
from local_env import FILTER_COLUMNS, LocalEnvironment, SCENARIOS


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


if __name__ == "__main__":
    unittest.main()
