"""Checks for the static preflight, using temporary local text fixtures only."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.preflight import Result, check_repository, contains_secret, has_agent_act, main


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.files = {
            "agent.py": "class Agent:\n def act(self, env): return []\n",
            "README.md": "Run python local_eval.py\n",
            "local_eval.py": "# Local fixture\n",
            ".env.example": "OPENAI_API_KEY=\n",
        }
        for name, content in self.files.items():
            (self.root / name).write_text(content, encoding="utf-8")

    def check(self, extra=()):
        names = set(self.files) | set(extra)
        with patch("scripts.preflight.repository_paths", return_value=(names, names)):
            return check_repository(self.root)

    def test_minimum_repository_passes_without_generated_submission(self):
        results = self.check()
        self.assertFalse(any(item.status == "FAIL" for item in results))
        self.assertIn(Result("SKIP", "submission.csv", "not-generated"), results)

    def test_missing_agent_readme_evaluator_and_tracked_submission_fail(self):
        for name in ("agent.py", "README.md", "local_eval.py"):
            with self.subTest(name=name):
                path = self.root / name
                path.unlink()
                self.assertTrue(any(item.status == "FAIL" and item.path == name for item in self.check()))
                path.write_text(self.files[name], encoding="utf-8")
        self.assertIn(Result("FAIL", "submission.csv", "generated-submission-present"), self.check(["submission.csv"]))

    def test_agent_entry_point_and_documented_command_are_required(self):
        self.assertTrue(has_agent_act(self.files["agent.py"]))
        self.assertFalse(has_agent_act("class Agent:\n def act(self): return []\n"))
        (self.root / "README.md").write_text("No command here\n", encoding="utf-8")
        self.assertIn(Result("FAIL", "README.md", "evaluation-command-documented"), self.check())

    def test_tracked_env_is_rejected_without_reading_it(self):
        # No .env exists; preflight must reject its listed name before opening it.
        for name in (".env", ".env.local", ".ENV"):
            results = self.check([name])
            self.assertIn(Result("FAIL", name, "env-file-in-git-file-list"), results)
            self.assertNotIn(Result("FAIL", name, "unreadable-text"), results)

    def test_empty_example_and_environment_lookup_are_allowed(self):
        self.assertFalse(contains_secret("OPENAI_API_KEY=\n", ".example"))
        self.assertFalse(contains_secret('api_key = os.environ["OPENAI_API_KEY"]', ".py"))

    def test_secrets_in_code_docs_logs_and_submission_are_detected(self):
        value = "sk-" + "a" * 32
        for name in ("extra.py", "extra.md", "debug.log", "submission.csv", "credentials"):
            with self.subTest(name=name):
                (self.root / name).write_text(value, encoding="utf-8")
                results = self.check([name])
                self.assertIn(Result("FAIL", name, "possible-secret"), results)
                self.assertNotIn(value, repr(results))
                (self.root / name).unlink()

    def test_plaintext_password_assignment_is_detected(self):
        source = 'password = "' + "fixture" * 3 + '"'
        self.assertTrue(contains_secret(source, ".py"))
        self.assertTrue(contains_secret(source, ".txt"))

    def test_diagnostics_never_print_exception_text(self):
        value = "private-" + "fixture-value"
        output = io.StringIO()
        with patch("scripts.preflight.check_repository", side_effect=OSError(value)), redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertNotIn(value, output.getvalue())
        self.assertIn("preflight-could-not-complete", output.getvalue())


if __name__ == "__main__":
    unittest.main()
