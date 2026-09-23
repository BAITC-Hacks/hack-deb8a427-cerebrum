import tempfile
import unittest
from pathlib import Path

import pandas as pd

from generate_case_data import build_case_data, validate_case_data, write_case_data


class CaseDataTests(unittest.TestCase):
    def test_history_links_and_populations(self):
        tables = build_case_data(n_customers=120, n_history=40, n_changes=70)
        validate_case_data(tables)
        self.assertEqual(len(tables["data/change_tariff.csv"]), 70)
        self.assertEqual(len(tables["data/arpu_monthly.csv"]), 40 * 8)
        self.assertEqual(len(tables["data/traffic.csv"]), 40 * 8)
        dictionary = tables["feature_dictionary.csv"]
        for name, table in tables.items():
            if name != "feature_dictionary.csv":
                self.assertEqual(set(dictionary.loc[dictionary["file"] == name, "column"]), set(table.columns))

    def test_seed_is_reproducible_and_changes_data(self):
        first = build_case_data(seed=10, n_customers=120, n_history=40, n_changes=70)
        same = build_case_data(seed=10, n_customers=120, n_history=40, n_changes=70)
        other = build_case_data(seed=11, n_customers=120, n_history=40, n_changes=70)
        for name in first:
            pd.testing.assert_frame_equal(first[name], same[name])
        self.assertFalse(first["data/change_tariff.csv"].equals(other["data/change_tariff.csv"]))

    def test_published_default_counts_and_baseline(self):
        tables = build_case_data()
        validate_case_data(tables)
        self.assertEqual(len(tables["customer_profile.csv"]), 23_441)
        self.assertEqual(len(tables["data/change_tariff.csv"]), 14_824)
        self.assertEqual(len(tables["data/dict_tariff.csv"]), 21)
        cents = (tables["customer_profile.csv"]["predicted_arpu"] * 100).round().astype("int64")
        self.assertEqual(int(cents.sum()), 150_641_084_00)

    def test_existing_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            sentinel = Path(temporary) / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_case_data(temporary)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
