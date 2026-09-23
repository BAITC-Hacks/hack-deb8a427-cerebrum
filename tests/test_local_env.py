"""Contract and accounting tests for the team's unofficial simulator."""

import unittest

import numpy as np
import pandas as pd

from local_env import Limits, LocalEnvironment


def tables(size=100):
    profile = pd.DataFrame({
        "customer_id": range(size),
        "current_tariff": ["basic"] * size,
        "arpu_segment": ["HIGH"] * size,
        "data_segment": ["HEAVY"] * size,
        "call_segment": ["LOW"] * size,
        "predicted_arpu": [1000.0] * size,
    })
    tariffs = pd.DataFrame({
        "tariff_id": ["basic", "other", "best"],
        "monthly_fee": [100.0, 200.0, 300.0],
    })
    return profile, tariffs


def campaign(**changes):
    return {"campaign_name": "Test", "target_tariff": "other", "channel": "push", **changes}


class FixedOutcomesEnvironment(LocalEnvironment):
    """Controlled responses test accounting independently of random mock effects."""

    def _outcomes(self, indices, target, channel):
        return np.full(len(indices), {"basic": 2.0, "other": -3.0, "best": 5.0}[target])


class LocalEnvironmentTests(unittest.TestCase):
    def make_env(self, size=100, **kwargs):
        return LocalEnvironment(*tables(size), **kwargs)

    def pilot(self, env, **changes):
        return env.run_pilot(**{"target_tariff": "other", "channel": "push", "n_customers": 10, **changes})

    def assert_rejected_without_charge(self, env, action):
        before = env.summary()
        history = env.pilot_history
        with self.assertRaises(ValueError):
            action()
        self.assertEqual(before, env.summary())
        self.assertEqual(history, env.pilot_history)

    def test_default_limits_match_case(self):
        limits = Limits()
        self.assertEqual((limits.budget, limits.contacts, limits.pilots), (100_000, 15_000, 20))
        self.assertEqual((limits.campaigns, limits.campaign_size), (10, 5000))
        self.assertEqual((limits.pilot_min, limits.pilot_max), (10, 200))

    def test_pilot_size_and_count_boundaries(self):
        env = self.make_env(200)
        for size in (0, 9, 201, 10.5, True):
            with self.subTest(size=size):
                self.assert_rejected_without_charge(env, lambda: self.pilot(env, n_customers=size))
        self.pilot(env, n_customers=200)
        for _ in range(19):
            self.pilot(env)
        self.assertEqual(env.pilots_left, 0)
        self.assert_rejected_without_charge(env, lambda: self.pilot(env))

    def test_pilot_budget_contacts_and_audience_preflight(self):
        for env, changes in [
            (self.make_env(limits=Limits(budget=1599)), {"channel": "call"}),
            (self.make_env(limits=Limits(contacts=9)), {}),
            (self.make_env(10), {"n_customers": 11}),
        ]:
            with self.subTest(changes=changes):
                self.assert_rejected_without_charge(env, lambda: self.pilot(env, **changes))

    def test_final_plan_requires_successful_pilot(self):
        env = self.make_env()
        self.assert_rejected_without_charge(env, lambda: env.evaluate([campaign()]))

    def test_campaign_count_and_size_boundaries(self):
        env = self.make_env(5001)
        self.pilot(env)
        for plan in ([], [campaign()] * 11, [campaign()]):
            with self.subTest(campaigns=len(plan)):
                self.assert_rejected_without_charge(env, lambda: env.evaluate(plan))
        valid = self.make_env(5000)
        self.pilot(valid)
        self.assertEqual(valid.evaluate([campaign()])["campaign_sizes"], [5000])

    def test_entire_plan_is_validated_before_charging(self):
        env = self.make_env()
        self.pilot(env)
        self.assert_rejected_without_charge(
            env, lambda: env.evaluate([campaign(channel="sms"), campaign(target_tariff="missing")])
        )
        self.assertEqual(env.evaluate([campaign()])["campaigns_count"], 1)

    def test_pilots_and_final_campaigns_share_budget_and_contacts(self):
        for limits in (Limits(budget=400), Limits(contacts=100)):
            with self.subTest(limits=limits):
                env = self.make_env(limits=limits)
                self.pilot(env, channel="sms")
                self.assertEqual(env.summary()["communication_cost"], 40)
                self.assertEqual(env.summary()["contacts_used"], 10)
                self.assert_rejected_without_charge(env, lambda: env.evaluate([campaign(channel="sms")]))

    def test_invalid_tariffs_channels_filters_and_fields_are_rejected(self):
        env = self.make_env()
        self.pilot(env)
        for changes in (
            {"target_tariff": "missing"}, {"channel": "email"},
            {"filter_arpu_segment": "UNKNOWN"}, {"filter_arpu_segment": "HIGH;"},
            {"filter_arpu_segment": ["HIGH"]}, {"n_customers": 10},
        ):
            with self.subTest(changes=changes):
                self.assert_rejected_without_charge(env, lambda: env.evaluate([campaign(**changes)]))

    def test_public_data_channels_and_history_are_independent_copies(self):
        profile, tariffs = tables()
        env = LocalEnvironment(profile, tariffs)
        profile.loc[0, "predicted_arpu"] = 1
        tariffs.loc[0, "monthly_fee"] = 1
        exposed_profile, exposed_tariffs, channels = env.customer_profile, env.tariffs, env.channels
        exposed_profile.loc[0, "predicted_arpu"] = 2
        exposed_tariffs.loc[0, "monthly_fee"] = 2
        channels["sms"]["cost"] = 0
        self.assertEqual(env.customer_profile.loc[0, "predicted_arpu"], 1000)
        self.assertEqual(env.tariffs.loc[0, "monthly_fee"], 100)
        self.assertEqual(env.channels["sms"]["cost"], 4)
        result = self.pilot(env)
        result["n_customers"] = 999
        history = env.pilot_history
        history[0]["n_customers"] = 888
        history.clear()
        self.assertEqual(env.pilot_history[0]["n_customers"], 10)

    def test_repeat_contacts_charge_each_time_and_keep_best_effect(self):
        env = FixedOutcomesEnvironment(*tables(10))
        self.pilot(env, target_tariff="basic")
        self.pilot(env, target_tariff="other", channel="sms")
        self.assertEqual(env.summary()["gross_uplift"], 20)
        metrics = env.evaluate([campaign(target_tariff="best", channel="sms")])
        self.assertEqual(metrics["gross_uplift"], 50)
        self.assertEqual(metrics["communication_cost"], 80)
        self.assertEqual(metrics["net_gain"], -30)
        self.assertEqual(metrics["contacts_used"], 30)
        self.assertEqual(metrics["unique_customers"], 10)

    def test_negative_first_effect_is_not_clamped_to_zero(self):
        env = FixedOutcomesEnvironment(*tables(10))
        self.pilot(env)
        self.assertEqual(env.summary()["gross_uplift"], -30)

    def test_finalization_blocks_further_pilots_and_evaluation(self):
        env = self.make_env()
        self.pilot(env)
        env.evaluate([campaign()])
        self.assert_rejected_without_charge(env, lambda: self.pilot(env))
        self.assert_rejected_without_charge(env, lambda: env.evaluate([campaign()]))


if __name__ == "__main__":
    unittest.main()
