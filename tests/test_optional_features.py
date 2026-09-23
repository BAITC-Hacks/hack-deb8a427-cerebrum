"""Behavioral checks for paid channels, LLM decisions and safe fallback."""

import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd

from agent import Agent
from llm_advisor import rank_cohorts
from tests.test_agent import PublicOnlyEnvironment
from tests import test_official_contract as official
from tests.test_official_contract import OfficialRewardEnvironment


def response(order):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps({"cohort_order": order})}]}]}


class ChannelEnvironment(OfficialRewardEnvironment):
    def __init__(self, arpu, favored_channel):
        super().__init__("left")
        self.customer_profile["predicted_arpu"] = arpu
        self.channels = {"push": {"cost_per_contact": 0, "conversion_multiplier": .5},
                         "sms": {"cost_per_contact": 4, "conversion_multiplier": .65},
                         "digital_ads": {"cost_per_contact": 22, "conversion_multiplier": .85},
                         "call": {"cost_per_contact": 160, "conversion_multiplier": 1.2}}
        self.favored_channel = favored_channel

    def run_pilot(self, **kwargs):
        result = super().run_pilot(**kwargs)
        # These independent effects deliberately disagree with published
        # channel multipliers, so the planner must use measured evidence.
        ratio = (1.8 if kwargs["channel"] == self.favored_channel else .65)
        if kwargs["target_tariff"] != "left":
            ratio = -.75
        result["observed_lift_ratio"] = ratio
        result["observed_lift_total"] = ratio * result["n_customers"] * float(self.customer_profile.predicted_arpu.mean())
        self.pilot_history[-1] = result.copy()
        return result


class OptionalFeatureTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"CEREBRUM_LLM": "0"})
        environment.start()
        self.addCleanup(environment.stop)

    def test_channel_selection_changes_with_observed_channel_value(self):
        for arpu, favored in ((100.0, "sms"), (5000.0, "call")):
            with self.subTest(arpu=arpu, favored=favored):
                env = ChannelEnvironment(arpu, favored)
                agent = Agent()
                plan = agent.act(PublicOnlyEnvironment(env))
                official.OfficialContractTests().assert_official_plan(env, plan)
                self.assertEqual({item["channel"] for item in plan}, {favored})
                self.assertTrue(any(row["phase"] == "channel_comparison" for row in agent.trace))
                self.assertTrue(any(row["phase"] == "confirmation" for row in agent.trace))

    def test_llm_reorders_real_pilots_using_only_aggregate_hypotheses(self):
        env = OfficialRewardEnvironment("left")
        other = env.customer_profile.copy()
        other["ID_NUMBER"] += 120
        other["arpu_segment"] = "LOW"
        other["predicted_arpu"] = 100.0
        env.customer_profile = pd.concat([env.customer_profile, other], ignore_index=True)

        def transport(payload, credential, timeout):
            cohorts = json.loads(payload["input"])["cohorts"]
            self.assertNotIn("ID_NUMBER", payload["input"])
            self.assertNotIn("customer_id", payload["input"])
            self.assertEqual(len(cohorts), 2)
            self.assertLessEqual(timeout, 8)
            self.assertFalse(payload["store"])
            return response([1, 0])

        with patch.dict(os.environ, {"CEREBRUM_LLM": "1", "OPENAI_API_KEY": "test"}), \
                patch("llm_advisor._request", side_effect=transport) as request, \
                patch.object(env, "run_pilot", wraps=env.run_pilot) as pilot:
            agent = Agent()
            plan = agent.act(PublicOnlyEnvironment(env))
        self.assertEqual(agent.llm_status, "applied")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(pilot.call_args_list[0].kwargs["filter_arpu_segment"], "LOW")
        official.OfficialContractTests().assert_official_plan(env, plan)

    def test_api_failure_preserves_the_offline_plan(self):
        expected = Agent().act(PublicOnlyEnvironment(OfficialRewardEnvironment("left")))
        with patch.dict(os.environ, {"CEREBRUM_LLM": "1", "OPENAI_API_KEY": "test"}), \
                patch("llm_advisor._request", side_effect=ConnectionError("unavailable")):
            agent = Agent()
            actual = agent.act(PublicOnlyEnvironment(OfficialRewardEnvironment("left")))
        self.assertEqual(actual, expected)
        self.assertEqual(agent.llm_status, "fallback")

    def test_missing_key_and_disabled_mode_never_make_requests(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("llm_advisor._request") as request:
            self.assertEqual(rank_cohorts([{}, {}], enabled=True), ([0, 1], "no_key"))
            self.assertEqual(rank_cohorts([{}, {}]), ([0, 1], "disabled"))
            request.assert_not_called()

    def test_invalid_rankings_refusals_and_incomplete_responses_fall_back(self):
        bad = [response([1, 1]), response([False, 1]), response([99, 0]), response([1]),
               {"status": "incomplete"}, {"status": "completed", "output": []}, None]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            for value in bad:
                with self.subTest(response=value):
                    order, status = rank_cohorts([{}, {}], enabled=True, transport=lambda *args: value)
                    self.assertEqual((order, status), ([0, 1], "fallback"))

    def test_hanging_transport_has_a_wall_clock_bound(self):
        release = threading.Event()

        def slow(*args):
            release.wait(.5)
            return response([1, 0])

        started = time.monotonic()
        try:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
                self.assertEqual(rank_cohorts([{}, {}], enabled=True, timeout=.02, transport=slow),
                                 ([0, 1], "timeout"))
            self.assertLess(time.monotonic() - started, .4)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
