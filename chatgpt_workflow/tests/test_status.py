"""Added in 2026 to test read-only ChatGPT workflow status snapshots."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chatgpt_workflow.access import Access
from chatgpt_workflow.status import run_status
from chatgpt_workflow.store import Store


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.store.create_problem('example', 'Theorem.')
        self.access = Access(self.store)
        self.key = self.access.issue('example', 'generation')

    def test_active_then_finished_and_cancelled_without_status_mutations(self):
        run = self.access.runs.open(self.key['credential_id'])
        self.store.record_artifacts('example', [{'record_id': 'checkpoint', 'channel': 'checkpoint',
                                               'text': 'PRIVATE RESEARCH', 'provenance': 'private'}])
        before = self.store.path.read_bytes()
        status = run_status(self.root, 'example')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(status['active_run']['run_id'], run['run_id'])
        self.assertEqual(status['latest_checkpoint']['record_id'], 'checkpoint')
        self.assertEqual(status['saved_artifact_count'], 1)
        self.assertNotIn(self.key['access_key'], json.dumps(status))
        self.assertNotIn('PRIVATE RESEARCH', json.dumps(status))
        self.access.runs.finish(run['run_id'], 'checkpoint')
        status = run_status(self.root, 'example')
        self.assertIsNone(status['active_run'])
        self.assertEqual(status['latest_run']['status'], 'finished')
        self.assertEqual(status['latest_run']['final_record'], 'checkpoint')
        next_run = self.access.runs.open(self.key['credential_id'])
        self.access.runs.cancel(next_run['run_id'])
        self.assertEqual(run_status(self.root, 'example')['latest_run']['status'], 'cancelled')

    def test_snapshot_observes_later_writes_without_claiming_execution_state(self):
        self.assertEqual(run_status(self.root, 'example')['saved_artifact_count'], 0)
        self.store.record_artifacts('example', [{'record_id': 'later', 'channel': 'checkpoint', 'text': 'Saved later'}])
        self.assertEqual(run_status(self.root, 'example')['saved_artifact_count'], 1)
        self.access.authorize('get_run', self.key['access_key'], {})
        calls = run_status(self.root, 'example')['recent_calls']
        self.assertEqual(calls[0]['outcome'], 'started')
        self.assertIsNone(calls[0]['completed_at'])
        self.assertIsNone(calls[0]['run_id'])

    def test_review_progress_and_acceptance(self):
        c = self.store.submit_candidate('example', [{'item_id': 'main', 'kind': 'theorem',
                                                    'statement': 'Theorem.', 'proof': 'Argument.'}])
        review = self.store.begin_review(c['candidate_id'])
        r = run_status(self.root, 'example')['latest_candidate']['review']
        self.assertEqual(r['missing_items'], ['main'])
        self.assertIsNone(r['verdict'])
        self.store.record_review_checks(review['verification_id'], c['candidate_sha256'],
                                        [{'item_id': 'main', 'assessment': 'Checked.'}])
        self.store.submit_review(review['verification_id'], c['candidate_sha256'], 'Checked.')
        self.store.export_accepted('example')
        status = run_status(self.root, 'example')
        self.assertEqual(status['latest_candidate']['review']['missing_items'], [])
        self.assertEqual(status['latest_candidate']['review']['verdict'], 'correct')
        self.assertEqual(status['accepted_candidate_ids'], [c['candidate_id']])
        self.assertEqual(status['latest_accepted_export_event']['operation'], 'export_accepted')

    def test_unknown_problem_and_missing_database_fail_without_initializing(self):
        with self.assertRaisesRegex(ValueError, 'Unknown problem'):
            run_status(self.root, 'missing')
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaisesRegex(ValueError, 'No workflow database'):
                run_status(Path(other), 'example')
            self.assertEqual(list(Path(other).iterdir()), [])

    def test_cli_bypasses_store_initialization_and_rejects_other_operations(self):
        from chatgpt_workflow import server
        with patch.object(server, 'ROOT', self.root), patch.object(server, 'Store', side_effect=AssertionError('must not initialize')):
            with patch('sys.argv', ['server', '--run-status', 'example']), contextlib.redirect_stdout(io.StringIO()) as output:
                server.main()
            self.assertEqual(json.loads(output.getvalue())['problem']['problem_id'], 'example')
            with patch('sys.argv', ['server', '--run-status', 'example', '--cancel-run', 'any']), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    server.main()
                self.assertEqual(error.exception.code, 2)
