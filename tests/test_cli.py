import unittest
from unittest.mock import Mock, patch

from contactanalyzer_app.cli import build_parser, run_subject_command


class CLITests(unittest.TestCase):
    def test_run_parser_exposes_both_codex_review_modes(self):
        interactive = build_parser().parse_args(["run", "Subject", "--with-codex"])
        unattended = build_parser().parse_args(["run", "Subject", "--with-codex-exec"])

        self.assertTrue(interactive.with_codex)
        self.assertFalse(interactive.with_codex_exec)
        self.assertFalse(unattended.with_codex)
        self.assertTrue(unattended.with_codex_exec)

    @patch("contactanalyzer_app.cli.run_codex", return_value=0)
    @patch("contactanalyzer_app.cli.run_subject", return_value=0)
    def test_run_with_codex_hands_completed_run_to_existing_assistant(
        self,
        collect: Mock,
        codex: Mock,
    ):
        db = Mock()
        config = {"cdp_endpoint": "http://127.0.0.1:9222"}
        subject = {"id": 7, "name": "Subject"}

        result = run_subject_command(
            db,
            config,
            Mock(),
            subject,
            with_codex=True,
            codex_non_interactive=False,
        )

        self.assertEqual(result, 0)
        collect.assert_called_once()
        codex.assert_called_once()
        self.assertFalse(codex.call_args.kwargs["non_interactive"])
        self.assertEqual(
            codex.call_args.kwargs["browser_endpoint"],
            "http://127.0.0.1:9222",
        )

    @patch("contactanalyzer_app.cli.run_codex")
    @patch("contactanalyzer_app.cli.run_subject", return_value=2)
    def test_codex_is_not_started_after_failed_collection(self, collect: Mock, codex: Mock):
        result = run_subject_command(
            Mock(),
            {},
            Mock(),
            {"id": 7},
            with_codex=True,
        )

        self.assertEqual(result, 2)
        collect.assert_called_once()
        codex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
