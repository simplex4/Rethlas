"""Added in 2026 to test human-readable sandbox run and resume output."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sandbox_workflow.__main__ import main, print_run_result, print_run_settings, terminal_progress


class TerminalOutputTests(unittest.TestCase):
    def manifest(self, status="incomplete", mode="fixed"):
        return {
            "settings": {
                "codex_bin": "codex",
                "codex_home": "/account",
                "generator_model": "generator-model",
                "generator_effort": "max",
                "verifier_model": "verifier-model",
                "verifier_effort": "high",
                "sandbox": "workspace-write",
                "approval_policy": "on-request",
            },
            "problem": "agents/generation/data/example.md",
            "run_id": "a" * 32,
            "next_iteration": 3,
            "generator_session": "session-1",
            "mode_policy": mode,
            "status": status,
        }

    def capture(self, function, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            function(*args)
        return stream.getvalue()

    def test_settings_banner_is_human_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = self.capture(print_run_settings, self.manifest(), Path(temporary), 10, "resume")
        self.assertIn(" Generator:  generator-model", output)
        self.assertIn(" Run ID:     " + "a" * 32, output)
        self.assertIn(" Next iter:  3", output)
        self.assertIn(" Max iters:  10", output)
        self.assertNotIn('"settings"', output)

    def test_progress_reports_generator_and_verifier_boundaries(self):
        details = {"log": Path("/run/logs/00000-generator"),
                   "turn": {"role": "generator", "iteration": 3, "attempt": 4}}
        started = self.capture(terminal_progress, "attempt_started", details)
        finished = self.capture(terminal_progress, "attempt_finished", details)
        self.assertIn("Starting iter=3 role=generator", started)
        self.assertIn("Finished iter=3 role=generator", finished)

        details["turn"] = {"role": "verifier", "iteration": 4, "attempt": 5}
        verifier = self.capture(terminal_progress, "attempt_started", details)
        self.assertIn("Starting verifier attempt=5", verifier)

    def test_final_output_uses_shell_style_sentences(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            accepted = self.capture(print_run_result, self.manifest("accepted"), run, 10)
            incomplete = self.capture(print_run_result, self.manifest(), run, 10)
            paused = self.capture(print_run_result, self.manifest("paused"), run, 10)
        self.assertIn("Solved problem_id=example", accepted)
        self.assertIn("To export results, run:", accepted)
        self.assertIn("without verified blueprint", incomplete)
        self.assertIn("Run the resume command below", incomplete)
        self.assertIn("Run paused.", paused)
        self.assertNotIn('"status"', accepted + incomplete + paused)

    def test_run_cli_orders_banner_progress_and_summary_without_json(self):
        manifest = self.manifest("accepted")
        run = Path("/repo/.local/sandbox-workflow/runs") / manifest["run_id"]

        class FakeWorkflow:
            def __init__(self, reporter=None):
                self.reporter = reporter
                self.root = run.parent

            def create(self, problem, iterative_improvement=False):
                return manifest["run_id"]

            def load(self, run_id):
                return run, manifest

            def path(self, run_id):
                return run

            def resume(self, run_id, iterations, *args):
                details = {"log": run / "logs/00000-generator",
                           "turn": {"role": "generator", "iteration": 3, "attempt": 4}}
                self.reporter("attempt_started", details)
                self.reporter("attempt_finished", details)
                return manifest

        class NoTimer:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        stream = io.StringIO()
        with patch("sandbox_workflow.__main__.Workflow", FakeWorkflow), \
                patch("sandbox_workflow.__main__.ElapsedTimer", NoTimer), \
                contextlib.redirect_stdout(stream):
            self.assertEqual(main(["run", "--problem", "problem.md", "--iterations", "2"]), 0)
        output = stream.getvalue()
        self.assertLess(output.index(" Run ID:"), output.index("Starting iter=3"))
        self.assertLess(output.index("Starting iter=3"), output.index("Finished iter=3"))
        self.assertLess(output.index("Finished iter=3"), output.index("Solved problem_id="))
        self.assertNotIn('"status"', output)


if __name__ == "__main__":
    unittest.main()
