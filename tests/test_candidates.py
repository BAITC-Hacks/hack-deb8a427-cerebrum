import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from candidates import candidate_orders, load_history


class CandidateTests(unittest.TestCase):
    def test_history_only_orders_a_bounded_tariff_shortlist(self):
        profile = pd.DataFrame({"current_tariff": ["current"] * 20, "arpu_segment": ["HIGH"] * 20,
                                "data_segment": ["HEAVY"] * 20})
        tariffs = pd.DataFrame({"tariff_id": ["current", "close", "history", "too_expensive"],
                                "monthly_fee": [900, 1000, 1300, 100000]})
        cohort = SimpleNamespace(indices=profile.index, mean_arpu=1000)
        history = pd.DataFrame({"to_tariff": ["history"] * 60, "arpu_segment": ["HIGH"] * 60,
                                "data_segment": ["HEAVY"] * 60, "relative_change": [.2] * 60})
        order = candidate_orders(profile, tariffs, [cohort], history)[0]
        self.assertEqual(order[0], "history")
        self.assertIn("close", order)
        self.assertNotIn("current", order)
        self.assertNotIn("too_expensive", order)
        self.assertLessEqual(len(order), 3)

    def test_missing_or_unlabelled_history_is_optional(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(load_history(temporary))
            (Path(temporary) / "manifest.json").write_text('{"source":"unknown"}', encoding="utf-8")
            self.assertIsNone(load_history(temporary))
            (Path(temporary) / "manifest.json").write_text('[]', encoding="utf-8")
            self.assertIsNone(load_history(temporary))

    def test_history_after_snapshot_and_invalid_revenue_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            (root / "manifest.json").write_text(json.dumps({"source": "team_generated_synthetic_case",
                                                            "snapshot_date": "2026-09-01"}), encoding="utf-8")
            pd.DataFrame({"to_tariff": ["next"] * 3, "arpu_before": [100, 100, 0],
                          "arpu_after": [120, 10000, 30], "arpu_segment": ["LOW"] * 3,
                          "data_segment": ["LITE"] * 3,
                          "event_date": ["2026-08-01", "2026-09-01", "2026-08-01"]}).to_csv(root / "data/change_tariff.csv", index=False)
            history = load_history(root)
            self.assertEqual(len(history), 1)
            self.assertAlmostEqual(history.iloc[0]["relative_change"], .2)
