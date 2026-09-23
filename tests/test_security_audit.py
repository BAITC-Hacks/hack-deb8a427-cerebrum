"""Safe-output and coverage checks for the local audit helper."""

from contextlib import redirect_stdout
import io
import subprocess
import unittest
from unittest.mock import patch

from scripts.security_audit import history_findings, main, requirement_findings, scan_text


class SecurityAuditTests(unittest.TestCase):
    def test_secret_findings_never_include_value(self):
        value = "sk-" + "a" * 32
        issues = scan_text("submission.csv", value.encode())
        self.assertEqual(issues, ["possible-secret"])
        self.assertNotIn(value, repr(issues))

    def test_personal_data_header_warns_without_exporting_rows(self):
        issues = scan_text("data.csv", b"phone,email\nredacted,redacted\n", personal_data=True)
        self.assertEqual(issues, ["possible-personal-data-columns"])

    def test_binary_content_is_reported_as_unscanned(self):
        self.assertEqual(scan_text("data.bin", b"abc\0def"), ["non-utf8-or-binary-not-scanned"])

    def test_history_scans_deleted_blob_and_commit_message(self):
        oid_blob, oid_commit = b"1" * 40, b"2" * 40
        value = ("sk-" + "b" * 32).encode()
        outputs = [
            oid_blob + b" removed.log\n" + oid_commit + b"\n",
            oid_blob + b" blob 35\n" + oid_commit + b" commit 35\n",
            value, value,
        ]
        with patch("scripts.security_audit.git", side_effect=[subprocess.CompletedProcess([], 0, stdout=o) for o in outputs]):
            findings, counts = history_findings()
        self.assertEqual(counts["text_blobs"], 1)
        self.assertEqual(counts["commit_tag_objects"], 1)
        self.assertEqual({name for name, _ in findings}, {"removed.log", "commit-or-tag"})
        self.assertNotIn(value.decode(), repr(findings))

    def test_dependency_pins_usage_and_installed_versions(self):
        issues, _ = requirement_findings("numpy==2.3.5\n", {"numpy"}, lambda _: "2.3.5")
        self.assertEqual(issues, [])
        issues, _ = requirement_findings("numpy\npandas==3.0.1\n", set(), lambda _: "missing")
        self.assertEqual(len(issues), 3)

    def test_audit_errors_do_not_leak_exception_content(self):
        output = io.StringIO()
        with patch("scripts.security_audit.audit", side_effect=OSError("private diagnostic")), redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertNotIn("private diagnostic", output.getvalue())


if __name__ == "__main__":
    unittest.main()
