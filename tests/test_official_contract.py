"""Official public API checks; no inspection of evaluator state or effects."""

import math
import unittest
from unittest.mock import patch

import pandas as pd

from agent import Agent, _Observation, _PlanGuard
from candidates import prepare_tables
from mock_environment import make_mock_env
from tests.test_agent import PublicOnlyEnvironment


FILTERS = {
    "filter_arpu_segment": "arpu_segment",
    "filter_data_segment": "data_segment",
    "filter_call_segment": "call_segment",
    "filter_current_tariff": "current_tariff",
}


def selected_audience(profile, campaign):
    """Independently apply the participant package's public filter contract."""
    selected = profile
    for field, column in FILTERS.items():
        value = campaign.get(field)
        if value is None or isinstance(value, float) and math.isnan(value):
            continue
        if field == "filter_current_tariff":
            values = [item.strip() for item in str(value).split(";") if item.strip()]
            selected = selected[selected[column].isin(values)]
        else:
            selected = selected[selected[column] == value]
    return selected


class OfficialRewardEnvironment:
    """Independent observations in official units, unrelated to mock effects."""

    def __init__(self, favored_tariff):
        self.customer_profile = pd.DataFrame({
            "ID_NUMBER": range(120), "current_tariff": ["original"] * 120,
            "arpu_segment": ["HIGH"] * 120, "data_segment": ["HEAVY"] * 120,
            "call_segment": ["LOW"] * 60 + ["HIGH"] * 60,
            "predicted_arpu": [1000.0] * 120,
        })
        self.tariffs = pd.DataFrame({
            "tariff_plan_code": ["original", "left", "right"],
            "price_tariff": [1000.0, 1100.0, 1200.0],
        })
        self.channels = {"push": {"cost_per_contact": 0.0}, "sms": {"cost_per_contact": 4.0}}
        self.total_budget = self.remaining_budget = 100_000.0
        self.max_total_contacts = self.remaining_contacts = 15_000
        self.pilots_left = 20
        self.pilot_history = []
        self.favored_tariff = favored_tariff

    def run_pilot(self, target_tariff, channel, n_customers=100,
                  filter_arpu_segment=None, filter_data_segment=None,
                  filter_call_segment=None, filter_current_tariff=None):
        campaign = {"target_tariff": target_tariff, "channel": channel,
                    "filter_arpu_segment": filter_arpu_segment,
                    "filter_data_segment": filter_data_segment,
                    "filter_call_segment": filter_call_segment,
                    "filter_current_tariff": filter_current_tariff}
        if not 10 <= n_customers <= 200 or self.pilots_left <= 0:
            raise ValueError("Invalid pilot size or exhausted pilot count")
        if target_tariff not in set(self.tariffs["tariff_plan_code"]) or channel not in self.channels:
            raise ValueError("Unknown public action")
        selected = selected_audience(self.customer_profile, campaign)
        cost = n_customers * self.channels[channel]["cost_per_contact"]
        if len(selected) < n_customers or n_customers > self.remaining_contacts or cost > self.remaining_budget:
            raise ValueError("Pilot exceeds its public audience or available resources")
        ratio = 0.75 if target_tariff == self.favored_tariff else -0.75
        self.remaining_budget -= cost
        self.remaining_contacts -= n_customers
        self.pilots_left -= 1
        result = {"pilot": f"pilot_{len(self.pilot_history) + 1}",
                  "target_tariff": target_tariff, "channel": channel,
                  "n_customers": n_customers, "cost": cost,
                  "observed_lift_ratio": ratio,
                  "observed_lift_total": ratio * n_customers * float(selected["predicted_arpu"].mean()),
                  "remaining_budget": self.remaining_budget,
                  "remaining_contacts": self.remaining_contacts}
        self.pilot_history.append(result.copy())
        return result


class OfficialContractTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect"):
            blocker = patch(target, side_effect=ConnectionError("Network disabled in official contract tests"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def assert_official_plan(self, env, campaigns):
        self.assertIsInstance(campaigns, list)
        self.assertTrue(1 <= len(campaigns) <= 10)
        self.assertTrue(1 <= len(env.pilot_history) <= 20)
        self.assertTrue(all(10 <= item["n_customers"] <= 200 for item in env.pilot_history))
        final_contacts, final_cost = 0, 0.0
        for campaign in campaigns:
            self.assertIsInstance(campaign, dict)
            self.assertFalse(set(campaign) - {"target_tariff", "channel", "campaign_name", *FILTERS})
            self.assertIn(campaign["target_tariff"], set(env.tariffs["tariff_plan_code"]))
            self.assertIn(campaign["channel"], env.channels)
            for field in FILTERS:
                value = campaign.get(field)
                if value is not None:
                    self.assertIsInstance(value, str)
                    self.assertTrue(value, "Blank filters select no official audience")
                    if field != "filter_current_tariff":
                        self.assertNotIn(";", value)
            selected = selected_audience(env.customer_profile, campaign)
            self.assertTrue(1 <= len(selected) <= 5000)
            self.assertTrue(any(item["target_tariff"] == campaign["target_tariff"]
                                and item["channel"] == campaign["channel"] for item in env.pilot_history))
            final_contacts += len(selected)
            final_cost += len(selected) * env.channels[campaign["channel"]]["cost_per_contact"]
        pilot_contacts = sum(item["n_customers"] for item in env.pilot_history)
        pilot_cost = sum(item["cost"] for item in env.pilot_history)
        self.assertLessEqual(final_contacts + pilot_contacts, 15_000)
        self.assertLessEqual(final_cost + pilot_cost, 100_000)
        self.assertEqual(env.remaining_contacts, 15_000 - pilot_contacts)
        self.assertAlmostEqual(env.remaining_budget, 100_000 - pilot_cost)
        self.assertLessEqual(final_contacts, env.remaining_contacts)
        self.assertLessEqual(final_cost, env.remaining_budget)

    def test_agent_uses_only_public_official_api_and_respects_actual_audiences(self):
        for seed in (42, 7):
            with self.subTest(seed=seed):
                env, _ = make_mock_env(seed=seed)
                denied = []
                with patch.object(env, "run_pilot", wraps=env.run_pilot) as pilot:
                    campaigns = Agent().act(PublicOnlyEnvironment(env, denied_accesses=denied))
                self.assertEqual(denied, [], "Even caught non-public API attempts violate the contract")
                self.assertEqual(pilot.call_count, len(env.pilot_history))
                self.assert_official_plan(env, campaigns)

    def test_official_observations_change_decisions_on_identical_public_data(self):
        plans = []
        for favorite in ("left", "right"):
            env = OfficialRewardEnvironment(favorite)
            campaigns = Agent().act(PublicOnlyEnvironment(env))
            self.assert_official_plan(env, campaigns)
            self.assertEqual({item["target_tariff"] for item in campaigns}, {favorite})
            plans.append(campaigns)
        self.assertNotEqual(plans[0], plans[1])

    def test_official_uncertainty_remains_nonzero_without_sample_std(self):
        result = {"n_customers": 30, "observed_lift_ratio": 0.2, "observed_lift_total": 6000.0}
        observation = _Observation(0, "left", "push")
        observation.update(result, mean_arpu=1000.0)
        first_error = observation.error
        self.assertTrue(math.isfinite(first_error))
        self.assertGreater(first_error, 0)
        self.assertAlmostEqual(observation.mean, 200.0)
        observation.update(result, mean_arpu=1000.0)
        self.assertGreater(observation.error, 0)
        self.assertLess(observation.error, first_error)

    def test_official_nonfinite_observation_is_rejected(self):
        observation = _Observation(0, "left", "push")
        with self.assertRaises(ValueError):
            observation.update({"n_customers": 30, "observed_lift_ratio": float("nan")}, mean_arpu=1000)
        self.assertEqual(observation.count, 0)

    def test_noisy_first_pilot_does_not_trigger_paid_channel_exploration(self):
        class NoisyFirstPilotEnvironment(OfficialRewardEnvironment):
            def run_pilot(self, **kwargs):
                result = super().run_pilot(**kwargs)
                count = sum(item["target_tariff"] == kwargs["target_tariff"]
                            for item in self.pilot_history)
                # A small positive initial observation is followed by losses.
                # Neither observation is evidence of a reliably profitable cell.
                ratio = 0.12 if count == 1 else -0.30
                result["observed_lift_ratio"] = ratio
                result["observed_lift_total"] = ratio * result["n_customers"] * 1000.0
                self.pilot_history[-1] = result.copy()
                return result

        env = NoisyFirstPilotEnvironment(None)
        env.channels["call"] = {"cost_per_contact": 160.0}
        campaigns = Agent().act(PublicOnlyEnvironment(env))
        self.assert_official_plan(env, campaigns)
        self.assertEqual({item["channel"] for item in env.pilot_history}, {"push"})
        self.assertTrue(any(item["observed_lift_ratio"] > 0 for item in env.pilot_history))

    def test_guard_matches_official_blank_and_multivalue_filter_semantics(self):
        env = OfficialRewardEnvironment("left")
        profile, tariffs = prepare_tables(env.customer_profile, env.tariffs)
        guard = _PlanGuard(env, profile, tariffs["tariff_id"], {"push": 0.0, "sms": 4.0})
        base = {"target_tariff": "left", "channel": "push"}
        self.assertEqual(guard.audience_size(base), 120)
        self.assertEqual(guard.audience_size({**base, "filter_arpu_segment": ""}), 0)
        self.assertEqual(guard.audience_size({**base, "filter_call_segment": "LOW;HIGH"}), 0)
        self.assertEqual(guard.audience_size({**base, "filter_call_segment": "HIGH"}), 60)

    def test_negative_pilots_with_missing_call_segments_use_whole_feasible_cohort(self):
        env = OfficialRewardEnvironment(None)
        env.customer_profile["call_segment"] = None
        campaigns = Agent().act(PublicOnlyEnvironment(env))
        self.assert_official_plan(env, campaigns)
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(len(selected_audience(env.customer_profile, campaigns[0])), 120)
        self.assertNotIn("filter_call_segment", campaigns[0])
        self.assertTrue(all(item["observed_lift_ratio"] < 0 for item in env.pilot_history))

    def test_zero_arpu_cohort_keeps_valid_pilot_observations_and_returns_plan(self):
        env = OfficialRewardEnvironment("left")
        env.customer_profile["predicted_arpu"] = 0.0
        campaigns = Agent().act(PublicOnlyEnvironment(env))
        self.assert_official_plan(env, campaigns)
        self.assertLess(len(env.pilot_history), 20)
        self.assertTrue(all(item["observed_lift_total"] == 0 for item in env.pilot_history))


if __name__ == "__main__":
    unittest.main()
