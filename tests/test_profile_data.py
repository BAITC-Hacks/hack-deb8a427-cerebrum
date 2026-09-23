"""Checks for the standalone participant CSV profiler."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analysis import profile_data


class ProfileDataTests(unittest.TestCase):
    def run_report(self, *arguments):
        output = StringIO()
        with redirect_stdout(output):
            status = profile_data.main(list(arguments))
        return status, output.getvalue()

    def test_repository_root_does_not_profile_demo_or_submission_csv(self):
        root = Path(__file__).resolve().parents[1]
        status, report = self.run_report("--data-dir", str(root))

        self.assertEqual(status, 2)
        self.assertIn("Указан корень репозитория", report)
        self.assertNotIn("### submission.csv", report)

    def test_empty_participant_folder_is_not_checked(self):
        with TemporaryDirectory() as directory:
            status, report = self.run_report("--data-dir", directory)

        self.assertEqual(status, 2)
        self.assertIn("NOT_CHECKED", report)
        self.assertIn("нет доступных CSV участников", report)

    def test_explicit_folder_and_mappings_complete_profile(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "customer_profile.csv").write_text(
                "customer_id,current_tariff,arpu_segment,data_segment,call_segment,predicted_arpu\n"
                "001,T1,low,light,short,100.50\n"
                "002,T2,high,heavy,long,200.00\n",
                encoding="utf-8",
            )
            (root / "tariff_dictionary.csv").write_text("tariff_id\nT1\nT2\n", encoding="utf-8")
            (root / "segments.csv").write_text(
                "arpu,data,call\nlow,light,short\nhigh,heavy,long\n", encoding="utf-8"
            )
            (root / "history.csv").write_text("customer_id\n001\n002\n", encoding="utf-8")

            status, report = self.run_report(
                "--data-dir", directory,
                "--customer-id", "customer_id",
                "--current-tariff", "current_tariff",
                "--tariff-key", "tariff_dictionary.csv:tariff_id",
                "--id-column", "customer_profile.csv:customer_id",
                "--id-column", "history.csv:customer_id",
                "--segment-rule", "arpu_segment=segments.csv:arpu",
                "--segment-rule", "data_segment=segments.csv:data",
                "--segment-rule", "call_segment=segments.csv:call",
            )

        self.assertEqual(status, 0)
        self.assertIn("Уникальных непустых идентификаторов абонентов: **2**", report)
        self.assertIn("predicted_arpu: min=100.50, max=200.00", report)
        self.assertNotIn("| NOT_CHECKED |", report)
        self.assertNotIn("| 001 |", report)


if __name__ == "__main__":
    unittest.main()
