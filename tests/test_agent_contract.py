"""Agent contract and offline behavior through the provided public mock API."""

import importlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.support.local_env import FILTER_COLUMNS, LocalEnvironment
from tests.support.generate_demo_data import build_demo_data
from tests.test_agent import PublicOnlyEnvironment, RewardEnvironment, audience


ROOT = Path(__file__).resolve().parents[1]


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect"):
            blocker = patch(target, side_effect=ConnectionError("Network disabled in contract tests"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def assert_contract(self, env, campaigns):
        self.assertIsInstance(campaigns, list)
        self.assertTrue(1 <= len(campaigns) <= 10)
        tariffs, channels = set(env.tariffs["tariff_id"]), set(env.channels)
        signatures = set()
        for campaign in campaigns:
            self.assertIsInstance(campaign, dict)
            self.assertFalse(set(campaign) - {"campaign_name", "target_tariff", "channel", *FILTER_COLUMNS})
            for field in ("target_tariff", "channel"):
                self.assertIn(field, campaign)
                self.assertIsInstance(campaign[field], str)
            self.assertIn(campaign["target_tariff"], tariffs)
            self.assertIn(campaign["channel"], channels)
            if "campaign_name" in campaign:
                name = campaign["campaign_name"]
                self.assertIsInstance(name, str)
                self.assertTrue(name.strip(), "Campaign name must not be blank")
            filters = []
            for field, column in FILTER_COLUMNS.items():
                value = campaign.get(field, "")
                self.assertIsInstance(value, str)
                if value:
                    self.assertTrue(set(value.split(";")).issubset(set(env.customer_profile[column])))
                filters.append(tuple(sorted(set(value.split(";")))) if value else ())
            # Renaming an otherwise identical campaign does not make it distinct.
            signature = (campaign["target_tariff"], campaign["channel"], *filters)
            self.assertNotIn(signature, signatures, "Duplicate campaign action")
            signatures.add(signature)

    def run_agent(self, env):
        self.assertTrue((ROOT / "agent.py").is_file())
        agent_class = importlib.import_module("agent").Agent
        agent = agent_class()
        self.assertTrue(callable(agent.act))
        with patch.object(env, "run_pilot", wraps=env.run_pilot) as pilot:
            campaigns = agent.act(PublicOnlyEnvironment(env))
        self.assertTrue(1 <= pilot.call_count <= 20)
        self.assertTrue(all(10 <= call.kwargs["n_customers"] <= 200 for call in pilot.call_args_list))
        self.assert_contract(env, campaigns)
        self.assertGreater(len(env.pilot_history), 0)
        self.assertLessEqual(len(env.pilot_history), 20)
        sizes = [len(audience(env.customer_profile, campaign)) for campaign in campaigns]
        self.assertTrue(all(1 <= size <= 5000 for size in sizes))
        self.assertLessEqual(sum(sizes) + sum(p["n_customers"] for p in env.pilot_history), 15000)
        final_cost = sum(size * env.channels[campaign["channel"]]["cost"]
                         for size, campaign in zip(sizes, campaigns))
        self.assertLessEqual(final_cost, env.remaining_budget)
        return campaigns

    def test_import_callable_act_and_output_contract(self):
        for favored in ("left", "right", None):
            with self.subTest(favored=favored):
                self.run_agent(RewardEnvironment(favored))

    def test_contract_on_provided_local_environment(self):
        env = LocalEnvironment(*build_demo_data(), seed=73)
        campaigns = self.run_agent(env)
        metrics = env.evaluate(campaigns)
        self.assertLessEqual(metrics["contacts_used"], 15000)
        self.assertLessEqual(metrics["communication_cost"], 100000)

    def test_missing_openai_api_key(self):
        with patch.dict(os.environ):
            os.environ.pop("OPENAI_API_KEY", None)
            self.run_agent(RewardEnvironment("left"))

    def test_external_connection_errors_do_not_escape_act(self):
        # No real request is made. Current Agent has no external integration;
        # this is an offline guard, not a claim that an SDK fallback was exercised.
        for error in (TimeoutError("mock timeout"), ConnectionError("mock connection failure")):
            with self.subTest(error=type(error).__name__), patch.dict(os.environ), \
                 patch("socket.create_connection", side_effect=error), \
                 patch("socket.socket.connect", side_effect=error):
                os.environ.pop("OPENAI_API_KEY", None)
                self.run_agent(RewardEnvironment("right"))

    def test_campaign_name_is_optional(self):
        self.assert_contract(RewardEnvironment("left"), [{"target_tariff": "left", "channel": "push"}])

    def test_contract_rejects_invalid_campaigns(self):
        env = RewardEnvironment("left")
        valid = {"campaign_name": "Contract fixture", "target_tariff": "left", "channel": "push"}
        cases = [None, {}, [], [valid] * 11,
                 [{key: value for key, value in valid.items() if key != "target_tariff"}],
                 [{key: value for key, value in valid.items() if key != "channel"}],
                 [{**valid, "target_tariff": "missing"}], [{**valid, "channel": "missing"}],
                 [{**valid, "unexpected": 1}], [{**valid, "filter_arpu_segment": "MISSING"}],
                 [{**valid, "campaign_name": ""}], [{**valid, "campaign_name": " \t "}],
                 [valid, dict(valid)], [valid, {**valid, "campaign_name": "Renamed duplicate"}]]
        for index, campaigns in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(AssertionError):
                self.assert_contract(env, campaigns)


if __name__ == "__main__":
    unittest.main()
