import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from candidates import candidate_orders, cohort_priority, load_history, prepare_tables


class CandidateTests(unittest.TestCase):
    def test_official_public_columns_are_normalized_without_mutating_inputs(self):
        profile = pd.DataFrame({"ID_NUMBER": [17, 29], "current_tariff": ["old", "old"],
                                "arpu_segment": ["HIGH", "LOW"], "data_segment": ["HEAVY", "LITE"],
                                "call_segment": ["MEDIUM", "LOW"], "ARPU_3m_avg": [1200, 900]})
        tariffs = pd.DataFrame({"tariff_plan_code": ["old", "next"], "price_tariff": [700, 1100]})
        normalized, offers = prepare_tables(profile, tariffs)
        self.assertEqual(normalized["customer_id"].tolist(), [17, 29])
        self.assertEqual(normalized["predicted_arpu"].tolist(), [1200, 900])
        self.assertEqual(offers["tariff_id"].tolist(), ["old", "next"])
        self.assertEqual(offers["monthly_fee"].tolist(), [700, 1100])
        self.assertNotIn("customer_id", profile)
        self.assertNotIn("tariff_id", tariffs)

    def test_partial_missing_segments_preserve_other_values_and_full_audience(self):
        profile = pd.DataFrame({"current_tariff": ["old"] * 4, "arpu_segment": ["HIGH", None, "LOW", "HIGH"],
                                "data_segment": ["HEAVY", "LITE", "LITE", None],
                                "call_segment": ["LOW", "MEDIUM", None, "LOW"]})
        normalized, _ = prepare_tables(profile, pd.DataFrame({"tariff_id": ["next"]}))
        self.assertEqual(len(normalized), 4)
        self.assertEqual(normalized.loc[0, "arpu_segment"], "HIGH")
        self.assertEqual(normalized.loc[2, "arpu_segment"], "LOW")
        self.assertTrue(pd.isna(normalized.loc[1, "arpu_segment"]))
        groups = normalized.groupby(["arpu_segment", "data_segment"])
        self.assertEqual({tuple(part.index) for _, part in groups}, {(0,), (2,)})
        # A broad HIGH filter also includes the row with missing data_segment.
        self.assertEqual(int(normalized["arpu_segment"].eq("HIGH").sum()), 2)

    def test_nonrepresentable_filter_tokens_are_missing_not_unrestricted(self):
        profile = pd.DataFrame({"arpu_segment": ["HIGH", "HIGH;LOW", "", 1],
                                "data_segment": ["HEAVY", None, "LITE", " "],
                                "current_tariff": ["old", "old;next", "old", "old"]})
        normalized, _ = prepare_tables(profile, pd.DataFrame({"tariff_id": ["next"]}))
        self.assertEqual(normalized["arpu_segment"].notna().tolist(), [True, False, False, False])
        self.assertTrue(pd.isna(normalized.loc[1, "current_tariff"]))
        # A wholly unavailable feature can be omitted for the entire population.
        self.assertEqual(normalized["call_segment"].tolist(), [""] * 4)

    def test_revenue_and_price_invalid_values_do_not_break_ranking(self):
        profile = pd.DataFrame({"predicted_arpu": [100, float("inf"), -2, "bad"]})
        tariffs = pd.DataFrame({"tariff_plan_code": ["next", "next", None, "bad_price"],
                                "price_tariff": [1100, 900, 1000, float("nan")]})
        normalized, offers = prepare_tables(profile, tariffs)
        self.assertEqual(normalized["predicted_arpu"].tolist(), [100, 0, 0, 0])
        self.assertEqual(offers["tariff_id"].tolist(), ["next", "bad_price"])
        self.assertEqual(offers["monthly_fee"].tolist(), [1100, 0])

    def test_history_only_orders_a_bounded_tariff_shortlist(self):
        profile = pd.DataFrame({"current_tariff": ["current"] * 20, "arpu_segment": ["HIGH"] * 20,
                                "data_segment": ["HEAVY"] * 20})
        tariffs = pd.DataFrame({"tariff_id": ["current", "close", "history", "too_expensive"],
                                "monthly_fee": [900, 1000, 1300, 100000]})
        cohort = SimpleNamespace(indices=profile.index, mean_arpu=1000)
        history = pd.DataFrame({"to_tariff": ["history"] * 60, "from_tariff": ["current"] * 60,
                                "arpu_segment": ["HIGH"] * 60, "relative_change": [.2] * 60})
        order = candidate_orders(profile, tariffs, [cohort], history)[0]
        self.assertEqual(order[0], "history")
        self.assertIn("close", order)
        self.assertNotIn("current", order)
        self.assertNotIn("too_expensive", order)
        self.assertLessEqual(len(order), 3)

    def test_unrelated_history_and_matching_ids_do_not_change_priority(self):
        profile = pd.DataFrame({"customer_id": [1, 2], "current_tariff": ["current"] * 2,
                                "arpu_segment": ["HIGH"] * 2})
        tariffs = pd.DataFrame({"tariff_id": ["close", "history", "unrelated"], "monthly_fee": [1000, 1300, 1500]})
        cohort = SimpleNamespace(indices=profile.index, mean_arpu=1000)
        history = pd.DataFrame({"customer_id": [999] * 60 + [1] * 60,
                                "to_tariff": ["history"] * 60 + ["unrelated"] * 60,
                                "from_tariff": ["current"] * 60 + ["other"] * 60,
                                "arpu_segment": ["HIGH"] * 120,
                                "relative_change": [.2] * 60 + [1.] * 60})
        order = candidate_orders(profile, tariffs, [cohort], history)
        self.assertEqual(order[0][0], "history")
        history["customer_id"] = 2
        self.assertEqual(candidate_orders(profile, tariffs, [cohort], history), order)

    def test_historical_priority_matches_pre_change_arpu_segment(self):
        profile = pd.DataFrame({"current_tariff": ["current", "current"], "arpu_segment": ["LOW", "HIGH"]})
        cohorts = [SimpleNamespace(indices=[index], mean_arpu=1000) for index in profile.index]
        tariffs = pd.DataFrame({"tariff_id": ["low_offer", "high_offer"], "monthly_fee": [900, 1100]})
        history = pd.DataFrame({"from_tariff": ["current"] * 120,
                                "to_tariff": ["low_offer"] * 60 + ["high_offer"] * 60,
                                "arpu_segment": ["LOW"] * 60 + ["HIGH"] * 60,
                                "relative_change": [.8] * 60 + [.2] * 60})
        orders = candidate_orders(profile, tariffs, cohorts, history)
        self.assertEqual([order[0] for order in orders], ["low_offer", "high_offer"])
        self.assertEqual(cohort_priority(profile, cohorts, history), [0, 1])

    def test_pilot_priority_discounts_sparse_evidence_and_has_stable_fallback(self):
        profile = pd.DataFrame({"current_tariff": ["sparse", "supported", "unknown"],
                                "arpu_segment": ["MID"] * 3})
        cohorts = [SimpleNamespace(indices=[index], mean_arpu=1000) for index in profile.index]
        history = pd.DataFrame({"from_tariff": ["sparse"] + ["supported"] * 60,
                                "to_tariff": ["next"] * 61, "arpu_segment": ["MID"] * 61,
                                "relative_change": [.9] + [.2] * 60})
        original = history.copy(deep=True)
        self.assertEqual(cohort_priority(profile, cohorts, history), [1, 2, 0])
        self.assertEqual(cohort_priority(profile, cohorts), [0, 1, 2])
        pd.testing.assert_frame_equal(history, original)

    def test_pilot_priority_accounts_for_total_audience_revenue(self):
        profile = pd.DataFrame({"current_tariff": ["current"] * 4, "arpu_segment": ["MID"] * 4})
        cohorts = [SimpleNamespace(indices=[0, 1], mean_arpu=100),
                   SimpleNamespace(indices=[2], mean_arpu=1000),
                   SimpleNamespace(indices=[3], mean_arpu=100)]
        history = pd.DataFrame({"from_tariff": ["current"] * 60, "to_tariff": ["next"] * 60,
                                "arpu_segment": ["MID"] * 60, "relative_change": [.2] * 60})
        self.assertEqual(cohort_priority(profile, cohorts, history), [1, 0, 2])

    def test_missing_or_invalid_history_is_optional(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(load_history(temporary))
            (Path(temporary) / "data").mkdir()
            (Path(temporary) / "data/change_tariff.csv").write_text("wrong_column\nvalue\n", encoding="utf-8")
            self.assertIsNone(load_history(temporary))

    def test_official_history_is_read_without_manifest_or_subscriber_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            pd.DataFrame({"tariff_plan_code_from": ["old"], "tariff_plan_code_to": ["next"],
                          "AVG_ARPU_PREV_3M": [100], "AVG_ARPU_NEXT_3M": [120],
                          "TIME_KEY": ["2026-10-01 00:00:00.0"], "ID_NUMBER": [999]}).to_csv(
                              root / "data/change_tariff.csv", index=False)
            # The published data is authoritative; no synthetic snapshot is applied.
            with patch("candidates.__file__", str(root / "candidates.py")):
                history = load_history()
            self.assertEqual(history["from_tariff"].tolist(), ["old"])
            self.assertEqual(history["to_tariff"].tolist(), ["next"])
            self.assertAlmostEqual(history.iloc[0]["relative_change"], .2)
            self.assertNotIn("ID_NUMBER", history)
            self.assertNotIn("customer_id", history)

    def test_history_segments_use_public_thresholds_on_pre_change_revenue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            pd.DataFrame({"tariff_plan_code_from": ["old"] * 4, "tariff_plan_code_to": ["next"] * 4,
                          "AVG_ARPU_PREV_3M": [999.99, 1000, 5000, 5000.01],
                          "AVG_ARPU_NEXT_3M": [9000, 9000, 500, 500]}).to_csv(
                              root / "data/change_tariff.csv", index=False)
            self.assertEqual(load_history(root)["arpu_segment"].tolist(), ["LOW", "MID", "MID", "HIGH"])

    def test_invalid_history_revenue_and_identifiers_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            pd.DataFrame({"tariff_plan_code_from": ["old"] * 7,
                          "tariff_plan_code_to": ["next"] * 6 + [None],
                          "AVG_ARPU_PREV_3M": [100, 100, 0, -1, float("inf"), 100, 100],
                          "AVG_ARPU_NEXT_3M": [120, 10000, 30, 10, 20, -5, 110]}).to_csv(
                              root / "data/change_tariff.csv", index=False)
            history = load_history(root)
            self.assertEqual(len(history), 2)
            self.assertAlmostEqual(history.iloc[0]["relative_change"], .2)
            self.assertEqual(history.iloc[1]["relative_change"], 1)
