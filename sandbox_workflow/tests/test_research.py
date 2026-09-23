import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sandbox_workflow import research


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.old = Path.cwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        Path("statement.md").write_text("Original theorem.")

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = research.main(list(args))
        return code, json.loads(out.getvalue() or err.getvalue())

    def live(self):
        Path("turn.json").write_text('{"search_mode":"live"}')

    def test_memory_branch_checkpoint_submission(self):
        Path("turn.json").write_text('{"run_id":"test", "attempt":1}')
        self.assertEqual(self.call("init")[0], 0)
        Path("entry.json").write_text('{"claim":"compact finite cover"}')
        self.assertEqual(self.call("append", "--channel", "proof_steps", "--file", "entry.json")[0], 0)
        result = self.call("search", "--query", "compact cover")[1]
        self.assertEqual(result["matches"][0]["record"]["entry"]["claim"], "compact finite cover")
        self.assertEqual(self.call("branch", "--id", "b1", "--file", "entry.json")[0], 0)
        Path("proof.md").write_text("# theorem main\n## statement\nOriginal theorem.\n## proof\nA complete candidate.")
        self.assertEqual(self.call("checkpoint", "--file", "proof.md")[0], 0)
        self.assertEqual(Path("checkpoint.md").read_bytes(), Path("proof.md").read_bytes())
        self.assertEqual(self.call("submit", "--file", "proof.md")[0], 0)
        submitted = json.loads(Path("submission.json").read_text())
        self.assertEqual(submitted, {"path": "proof.md", "sha256": hashlib.sha256(Path("proof.md").read_bytes()).hexdigest(), "run_id": "test", "attempt": 1})
        checkpoint = json.loads(Path("checkpoint.meta.json").read_text())
        self.assertEqual(checkpoint, {**submitted, "path": "checkpoint.md"})
        self.assertFalse(Path("blueprint_verified.md").exists())

    def test_outputs_require_current_turn_identity(self):
        Path("proof.md").write_text("# theorem main\n## statement\nOriginal theorem.\n## proof\ncandidate")
        for turn in (None, "{}", '{"run_id":"test","attempt":true}', '{"run_id":"test","attempt":-1}'):
            if turn is not None:
                Path("turn.json").write_text(turn)
            for command in ("checkpoint", "submit"):
                self.assertEqual(self.call(command, "--file", "proof.md")[0], 1)
        self.assertFalse(Path("submission.json").exists())
        self.assertFalse(Path("checkpoint.md").exists())
        Path("turn.json").write_text('{"run_id":"test","attempt":0}')
        for command in ("checkpoint", "submit"):
            code, result = self.call(command, "--file", "proof.md")
            self.assertEqual(code, 0)
            self.assertEqual(result["attempt"], 0)

    def test_improvement_submit_preflight_and_binding(self):
        Path("turn.json").write_text(json.dumps({"run_id":"test", "attempt":1, "mode":"improvement"}))
        Path("baseline.json").write_text('{"accepted_results":[]}')
        Path("claim.json").write_text('{"statement":"Bound >= 2.","improvement":"Strict gain."}')
        Path("proof.md").write_text("# theorem main\n## statement\nBound >= 3.\n## proof\nArgument.")
        self.assertEqual(self.call("submit","--file","proof.md","--improvement-file","claim.json")[0],1)
        self.assertFalse(Path("submission.json").exists())
        Path("proof.md").write_text("# theorem main\n## statement\nBound >= 2.\n## proof\nArgument.")
        code, submitted = self.call("submit","--file","proof.md","--improvement-file","claim.json")
        self.assertEqual(code,0)
        from sandbox_workflow.research_contract import digest
        self.assertEqual(submitted["baseline_sha256"],digest({"accepted_results":[]}))

    def test_theorem_request_and_response_validation(self):
        with patch.object(research, "retrieve") as retrieve:
            for query, limit in (("x" * 20001, "1"), ("x", "11")):
                self.assertEqual(self.call("theorem-search", "--query", query, "--limit", limit)[0], 1)
            retrieve.assert_not_called()
            retrieve.return_value = (b'["wrong"]', research.ENDPOINT)
            self.assertEqual(self.call("theorem-search", "--query", "x")[0], 1)

    def test_gate_denies_absent_malformed_and_independent(self):
        with patch.object(research, "build_opener") as opener:
            for turn in (None, "{}", '{"search_mode":"disabled"}', "broken"):
                if turn is not None:
                    Path("turn.json").write_text(turn)
                code, result = self.call("theorem-search", "--query", "compactness")
                self.assertEqual(code, 1)
                self.assertEqual(result["status"], "retrieval-unavailable")
            opener.assert_not_called()

    def test_paths_and_channel_escape_rejected(self):
        for path in ("../outside", "/etc/passwd"):
            with self.assertRaises(ValueError):
                research.local(path)
        Path("escape").symlink_to(self.old, target_is_directory=True)
        self.assertEqual(self.call("submit", "--file", "escape/README.md")[0], 1)
        Path("entry.json").write_text("{}")
        self.assertEqual(self.call("append", "--channel", "../x", "--file", "entry.json")[0], 1)
        Path("memory").mkdir()
        Path("memory/evil.jsonl").symlink_to("/etc/passwd")
        self.assertEqual(self.call("search", "--query", "anything")[0], 1)

    def test_download_and_theorem_provenance(self):
        self.live()
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.org/paper.pdf"
        response.read.return_value = b"paper bytes"
        with patch.object(research, "build_opener") as opener:
            opener.return_value.open.return_value = response
            code, result = self.call("download", "--url", "https://example.org/paper.pdf", "--name", "paper.pdf")
            self.assertEqual(code, 0)
            self.assertEqual(result["sha256"], hashlib.sha256(b"paper bytes").hexdigest())
            self.assertEqual(Path(result["path"]).read_bytes(), b"paper bytes")
            self.assertEqual(self.call("download", "--url", "https://example.org/paper.pdf", "--name", "paper.pdf")[0], 1)
            response.read.return_value = b'[{"theorem":"example"}]'
            response.geturl.return_value = research.ENDPOINT
            code, result = self.call("theorem-search", "--query", "example")
            self.assertEqual(code, 0)
            self.assertEqual(result["count"], 1)
            self.assertTrue(Path(result["path"]).exists())
        records = [json.loads(line) for line in Path("memory/events.jsonl").read_text().splitlines()]
        self.assertEqual([r["entry"]["type"] for r in records], ["download", "theorem-search"])

    def test_network_bounds_and_https(self):
        self.live()
        with self.assertRaises(ValueError):
            research.HTTPSRedirect().redirect_request(None, None, 302, "redirect", {}, "http://example.org")
        with patch.object(research, "build_opener") as opener:
            self.assertEqual(self.call("download", "--url", "http://example.org", "--name", "x")[0], 1)
            opener.assert_not_called()
            response = MagicMock()
            response.__enter__.return_value = response
            response.geturl.return_value = "https://example.org"
            response.read.return_value = b"12345"
            opener.return_value.open.return_value = response
            with patch.object(research, "MAX_DOWNLOAD", 4):
                self.assertEqual(self.call("download", "--url", "https://example.org", "--name", "x")[0], 1)
        self.assertFalse(Path("references/x").exists())

    def test_extract_missing_dependency_and_source_safety(self):
        with patch.object(research.shutil, "which", return_value=None):
            code, result = self.call("extract", "--file", "a.pdf")
            self.assertEqual(code, 1)
            self.assertIn("pdftotext is missing", result["error"])


if __name__ == "__main__":
    unittest.main()
