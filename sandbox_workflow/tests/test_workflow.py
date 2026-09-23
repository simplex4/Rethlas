"""Coordinator acceptance tests use scripted Codex events, not model claims."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sandbox_workflow.core import (Workflow, WorkflowError, atomic, command, lock, mcp_overrides,
                                  read_json, settings, sha, validate_review)

STATEMENT = "Every finite group of prime order is cyclic.\n"


def proof(revision=0):
    return ("# theorem main\n\n## statement\n" + STATEMENT + "\n## proof\n"
            + f"A nonidentity element generates the group by Lagrange's theorem. Revision {revision}.\n")


def report(binding, correct=True):
    return {**binding, "verification_report": {"summary": "Checked all statements.",
            "critical_errors": [], "gaps": [] if correct else [{"location": "main", "issue": "Justify Lagrange."}]},
            "verdict": "correct" if correct else "wrong", "repair_hints": "" if correct else "Expand argument."}


class ScriptedCodex:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []
        self.session = "00000000-0000-0000-0000-000000000001"

    def __call__(self, cmd, cwd, env, prompt, events, stderr):
        self.calls.append((cmd, cwd, env))
        action = self.actions.pop(0)
        turn = read_json(cwd / "turn.json")
        output = Path(cmd[cmd.index("--output-last-message") + 1])
        sid = self.session if turn["role"] == "generator" else "fresh-verifier"
        atomic(events, (json.dumps({"type": "thread.started", "thread_id": sid}) + "\n" +
                        json.dumps({"type": "turn.completed"}) + "\n").encode())
        atomic(stderr, b"")
        if callable(action):
            return action(cmd, cwd, env, prompt, events, stderr)
        if action == "fail":
            return 1
        if action == "checkpoint":
            atomic(cwd / "checkpoint.md", b"Progress and next steps")
            atomic(cwd / "checkpoint.meta.json", {"run_id": turn["run_id"], "attempt": turn["attempt"],
                                                  "sha256": sha(b"Progress and next steps")})
        elif action.startswith("candidate"):
            data = proof(turn["attempt"]).encode()
            atomic(cwd / "blueprint.md", data)
            atomic(cwd / "submission.json", {"path": "blueprint.md", "sha256": sha(data),
                                              "run_id": turn["run_id"], "attempt": turn["attempt"]})
        elif action in ("correct", "wrong"):
            atomic(output, report(read_json(cwd / "binding.json"), action == "correct"))
        else:
            raise AssertionError(action)
        return 0


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.home = self.repo / "account"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.home), "CODEX_CLI_HOME": "/wrong-account"})
        self.env.start()
        (self.repo / "problem.md").write_text(STATEMENT)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def workflow(self, actions):
        runner = ScriptedCodex(actions)
        workflow = Workflow(self.repo, runner)
        run_id = workflow.create("problem.md")
        return workflow, runner, run_id

    def test_rejection_revision_acceptance_exact_export(self):
        w, runner, rid = self.workflow(["candidate", "wrong", "candidate", "correct"])
        result = w.resume(rid, 2)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual([c["status"] for c in result["candidates"]], ["rejected", "accepted"])
        self.assertEqual(len(runner.calls), 4)
        first, verifier, resumed, verifier2 = [c[0] for c in runner.calls]
        self.assertNotIn("resume", first)
        self.assertIn("resume", resumed)
        self.assertNotIn("resume", verifier)
        self.assertNotIn("resume", verifier2)
        self.assertIn('web_search="disabled"', resumed)
        for cmd, _, env in runner.calls:
            self.assertIn('approval_policy="on-request"', cmd)
            self.assertIn('sandbox_mode="workspace-write"', cmd)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
            self.assertEqual(env["CODEX_HOME"], str(self.home.resolve()))
        exported = w.export(rid)
        self.assertEqual((exported / "blueprint_verified.md").read_text(), proof(2))
        with self.assertRaises(WorkflowError):
            w.export(rid)

    def test_budget_continuation_and_alternation(self):
        w, runner, rid = self.workflow(["checkpoint"] * 3)
        self.assertEqual(w.resume(rid, 1)["status"], "incomplete")
        w.resume(rid, 2)
        self.assertEqual(len(runner.calls), 3)
        self.assertIn('web_search="cached"', runner.calls[0][0])
        self.assertIn('web_search="disabled"', runner.calls[1][0])
        self.assertIn('web_search="cached"', runner.calls[2][0])
        self.assertEqual(len(list((w.path(rid) / "logs").iterdir())), 3)

    def test_pause_between_generator_and_verifier(self):
        w, runner, rid = self.workflow(["candidate", "correct"])
        original = runner.__call__
        def pausing(*args):
            rc = original(*args)
            atomic(w.path(rid) / "PAUSE_AFTER_TURN", b"pause")
            return rc
        w.runner = pausing
        result = w.resume(rid, 1)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["phase"], "verification")
        self.assertEqual(len(runner.calls), 1)
        w.runner = runner
        self.assertEqual(w.resume(rid, 1, clear_pause=True)["status"], "accepted")

    def test_account_mismatch_and_lock(self):
        w, _, rid = self.workflow([])
        with patch.dict(os.environ, {"CODEX_HOME": str(self.repo / "other")}):
            with self.assertRaisesRegex(WorkflowError, "account home"):
                w.resume(rid)
        with lock(w.path(rid) / "coordinator.lock"):
            with self.assertRaisesRegex(WorkflowError, "Another coordinator"):
                w.resume(rid)

    def test_changed_inputs_and_unrelated_verified_file(self):
        w, _, rid = self.workflow(["checkpoint"])
        atomic(w.path(rid) / "results" / "blueprint_verified.md", b"forged")
        self.assertEqual(w.resume(rid, 1)["status"], "incomplete")
        with self.assertRaisesRegex(WorkflowError, "no accepted"):
            w.export(rid)
        atomic(w.path(rid) / "inputs" / "statement.md", b"different")
        with self.assertRaisesRegex(WorkflowError, "snapshot"):
            w.resume(rid)

    def test_failed_process_recovers_session_without_accepting_output(self):
        w, runner, rid = self.workflow(["fail", "candidate", "correct"])
        with self.assertRaises(WorkflowError):
            w.resume(rid, 1)
        self.assertEqual(w.load(rid)[1]["status"], "blocked")
        self.assertEqual(w.resume(rid, 1)["status"], "accepted")
        self.assertIn(runner.session, runner.calls[1][0])

    def test_stale_checkpoint_rejected(self):
        w, runner, rid = self.workflow(["checkpoint", lambda *args: 0])
        w.resume(rid, 1)
        with self.assertRaisesRegex(WorkflowError, "Stale"):
            w.resume(rid, 1)

    def test_schema_and_hash_validation_fail_closed(self):
        binding = {"statement_sha256": "a", "candidate_sha256": "b"}
        good = report(binding)
        self.assertEqual(validate_review(good, binding), good)
        mutations = [lambda r: r.update(verdict="wrong"),
                     lambda r: r.update(candidate_sha256="different"),
                     lambda r: r.update(repair_hints="unexpected"),
                     lambda r: r["verification_report"].update(gaps="none"),
                     lambda r: r["verification_report"].update(summary=""),
                     lambda r: r.update(unexpected=True)]
        for mutate in mutations:
            bad = report(binding)
            mutate(bad)
            with self.assertRaises(WorkflowError):
                validate_review(bad, binding)
        bad = report(binding, False)
        bad["repair_hints"] = ""
        with self.assertRaises(WorkflowError):
            validate_review(bad, binding)

    def test_changed_verifier_input_not_accepted(self):
        def tamper(cmd, cwd, env, prompt, events, stderr):
            output = Path(cmd[cmd.index("--output-last-message") + 1])
            atomic(output, report(read_json(cwd / "binding.json")))
            atomic(cwd / "candidate.md", b"different")
            return 0
        w, _, rid = self.workflow(["candidate", tamper])
        with self.assertRaisesRegex(WorkflowError, "input candidate changed"):
            w.resume(rid, 1)
        self.assertFalse((w.path(rid) / "results" / "blueprint_verified.md").exists())

    def test_completed_turn_recovery_collects_exact_candidate(self):
        w, runner, rid = self.workflow(["candidate", "correct"])
        original = w.collect_candidate
        with patch.object(w, "collect_candidate", side_effect=KeyboardInterrupt):
            with self.assertRaises(WorkflowError):
                w.resume(rid, 1)
        self.assertEqual(w.load(rid)[1]["attempts"][-1]["status"], "completed")
        self.assertEqual(w.resume(rid, 1)["status"], "accepted")
        self.assertEqual(len(runner.calls), 2)

    def test_missing_verifier_output_blocks_and_retries_fresh(self):
        w, runner, rid = self.workflow(["candidate", lambda *args: 0, "correct"])
        with self.assertRaises(WorkflowError):
            w.resume(rid, 1)
        # A completed process with missing output must not be replayed endlessly.
        self.assertEqual(w.load(rid)[1]["phase"], "verification")
        self.assertEqual(w.resume(rid, 1)["status"], "accepted")
        self.assertNotIn("resume", runner.calls[-1][0])

    def test_user_mcp_entries_disabled_and_models_pinned(self):
        (self.home / "config.toml").write_text('[mcp_servers.example]\ncommand="irrelevant"\n')
        config = settings()
        cmd = command(config, self.repo, "generator", "disabled", self.repo / "out", session="abc")
        self.assertIn('mcp_servers.example.enabled=false', cmd)
        self.assertIn("gpt-6-astra", cmd)
        self.assertIn('model_reasoning_effort="max"', cmd)
        self.assertNotIn("--last", cmd)
        self.assertNotIn("--ignore-rules", cmd)

    def test_mcp_discovery_keeps_accounts_separate_and_includes_project_layers(self):
        user_home = self.repo / "user"
        project = user_home / "Developer" / "Rethlas"
        workspace = project / ".local" / "probes" / "probe"
        personal = user_home / ".codex-cli"
        edu = user_home / ".codex"
        entries = {
            personal / "config.toml": "personal_only",
            edu / "config.toml": "cua_repl",
            user_home / "Developer" / ".codex" / "config.toml": "outside_project",
            project / ".codex" / "config.toml": "project_server",
            project / ".local" / ".codex" / "config.toml": "intermediate_server",
            workspace / ".codex" / "config.toml": "workspace_server",
        }
        for path, name in entries.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'[mcp_servers.{name}]\ncommand="unused"\n')
        project_names = {"project_server", "intermediate_server", "workspace_server"}
        with patch("sandbox_workflow.core.REPO", project):
            for home, own_server in ((personal, "personal_only"), (edu, "cua_repl")):
                config = {**settings(), "codex_home": str(home)}
                for session in (None, "existing-session"):
                    cmd = command(config, workspace, "generator", "disabled", workspace / "out", session=session)
                    overrides = {arg for arg in cmd if arg.startswith("mcp_servers.")}
                    self.assertEqual(overrides, {f"mcp_servers.{n}.enabled=false" for n in project_names | {own_server}})
                    self.assertIn('approvals_reviewer="auto_review"', cmd)

    def test_mcp_discovery_does_not_read_malformed_ancestor_config(self):
        project = self.repo / "project"
        ancestor = self.repo / ".codex" / "config.toml"
        ancestor.parent.mkdir()
        ancestor.write_text("not valid TOML [[[\n")
        # Also covers a dry-run workspace that has not been created yet.
        self.assertEqual(mcp_overrides(self.home, project / "future-workspace", repo=project), [])

    def test_modified_computational_evidence_rejected(self):
        w, runner, rid = self.workflow(["candidate"])
        run = w.path(rid)
        atomic(run / "generation" / "artifacts" / "result.txt", b"exact computation")
        def tamper(cmd, cwd, env, prompt, events, stderr):
            output = Path(cmd[cmd.index("--output-last-message") + 1])
            atomic(output, report(read_json(cwd / "binding.json")))
            atomic(cwd / "artifacts" / "result.txt", b"changed")
            return 0
        runner.actions.append(tamper)
        with self.assertRaisesRegex(WorkflowError, "candidate evidence changed"):
            w.resume(rid, 1)

    def test_configuration_failure_does_not_poison_retry_directories(self):
        w, runner, rid = self.workflow(["candidate", "correct"])
        with patch("sandbox_workflow.core.command", side_effect=WorkflowError("bad config")):
            with self.assertRaisesRegex(WorkflowError, "bad config"):
                w.resume(rid, 1)
        self.assertEqual(w.resume(rid, 1)["status"], "accepted")

    def test_missing_completion_never_accepts_finished_looking_events(self):
        w, runner, rid = self.workflow(["candidate", "candidate", "correct"])
        original = runner.__call__
        def interrupted(*args):
            original(*args)
            raise KeyboardInterrupt
        w.runner = interrupted
        with self.assertRaises(WorkflowError):
            w.resume(rid, 1)
        self.assertFalse(w.load(rid)[1]["candidates"])
        w.runner = runner
        result = w.resume(rid, 1)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["attempts"][0]["status"], "interrupted")

    def test_changed_accepted_export_rejected(self):
        w, _, rid = self.workflow(["candidate", "correct"])
        w.resume(rid, 1)
        atomic(w.path(rid) / "results" / "blueprint_verified.md", b"forged")
        with self.assertRaisesRegex(WorkflowError, "Published proof differs"):
            w.export(rid)


if __name__ == "__main__":
    unittest.main()
