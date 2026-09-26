"""Added in 2026 for read-only status independent of ChatGPT and MCP authorization."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .store import inside, problem_key, stamp


def run_status(root: Path, problem_id: str) -> dict:
    problem_key(problem_id)
    path = inside(root, Path(root) / '.local/chatgpt-workflow/workflow.sqlite3').resolve()
    if not path.is_file():
        raise ValueError('No workflow database exists in this checkout')
    # Do not instantiate Store/Access: their constructors can initialize or migrate state.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')  # One consistent snapshot across all queries; no run lock.
        problem = db.execute('SELECT problem_id,state,latest_candidate FROM problems WHERE problem_id=?',
                             (problem_id,)).fetchone()
        if problem is None:
            raise ValueError(f'Unknown problem: {problem_id}')
        observed_at = stamp()
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(sql, args=(problem_id,)):
            return [dict(r) for r in db.execute(sql, args)]
        runs = rows('''SELECT run_id,role,sequence,search_mode,status,base_checkpoint,
                       final_record,created_at,finished_at FROM research_runs
                       WHERE problem_id=? ORDER BY rowid DESC LIMIT 10''')
        checkpoint = rows("""SELECT record_id,created_at FROM artifacts
                            WHERE problem_id=? AND channel='checkpoint' ORDER BY rowid DESC LIMIT 1""")
        candidate = db.execute('SELECT candidate_id,items_json,created_at FROM candidates WHERE candidate_id=?',
                               (problem['latest_candidate'],)).fetchone()
        latest_candidate = None
        if candidate:
            items = json.loads(candidate['items_json'])
            latest_candidate = {'candidate_id': candidate['candidate_id'],
                                'created_at': candidate['created_at'], 'item_count': len(items), 'review': None}
            review = db.execute('SELECT * FROM reviews WHERE candidate_id=?', (candidate['candidate_id'],)).fetchone()
            if review:
                checks = json.loads(review['checks_json'])
                report = json.loads(review['report_json']) if review['report_json'] else {}
                latest_candidate['review'] = {
                    'verification_id': review['verification_id'], 'created_at': review['created_at'],
                    'completed_at': review['completed_at'], 'checked_items': list(checks),
                    'missing_items': [i['item_id'] for i in items if i['item_id'] not in checks],
                    'additional_findings_count': db.execute('SELECT count(*) FROM review_findings WHERE verification_id=?',
                                                           (review['verification_id'],)).fetchone()[0],
                    'verdict': report.get('verdict'),
                    'improvement_verdict': (report.get('improvement_assessment') or {}).get('verdict')}
        calls = rows('''SELECT a.request_id,r.run_id,a.operation,a.outcome,a.created_at,a.completed_at
                        FROM access_log a LEFT JOIN request_runs r USING(request_id)
                        WHERE a.problem_id=? ORDER BY a.rowid DESC LIMIT 20''')
        events = rows('''SELECT event_id,operation,created_at FROM events
                         WHERE problem_id=? ORDER BY event_id DESC LIMIT 20''')
        accepted = rows('SELECT candidate_id FROM accepted_results WHERE problem_id=? ORDER BY rowid')
        # Older databases may not yet have the optional file library initialized.
        file_count = (db.execute('SELECT count(*) FROM research_files WHERE problem_id=?', (problem_id,)).fetchone()[0]
                      if 'research_files' in tables else None)
        exports = rows('''SELECT event_id,operation,created_at FROM events
                          WHERE problem_id=? AND operation='export_accepted'
                          ORDER BY event_id DESC LIMIT 1''')
        return {
            'observed_at_utc': observed_at, 'database': str(path), 'problem': dict(problem),
            'active_run': next((r for r in runs if r['status'] == 'active'), None),
            'latest_run': runs[0] if runs else None, 'recent_runs': runs,
            'latest_checkpoint': checkpoint[0] if checkpoint else None,
            'latest_candidate': latest_candidate, 'accepted_candidate_ids': [r['candidate_id'] for r in accepted],
            'saved_artifact_count': db.execute('SELECT count(*) FROM artifacts WHERE problem_id=?', (problem_id,)).fetchone()[0],
            'archived_file_count': file_count,
            'recent_artifacts': rows('''SELECT record_id,channel,created_at FROM artifacts
                                       WHERE problem_id=? ORDER BY rowid DESC LIMIT 20'''),
            'latest_accepted_export_event': exports[0] if exports else None,
            'recent_calls': calls, 'recent_events': events,
            'limits': [
                'Snapshot of committed local state, not ChatGPT execution status or mathematical certification.',
                'Active means calls remain authorized; it does not prove ChatGPT is currently working.',
                'New calls or records between snapshots show local activity; silence does not prove upstream execution stopped.',
                'Incomplete audit entries can reflect an interrupted process, not necessarily an operation still running.',
                'Client discovery and blocks before server delivery are absent from this database.',
                'Export events record a completed export operation, not a fresh integrity check of files on disk.',
                'Counts are problem-wide; lists contain at most 10 runs and 20 calls, events, or artifacts.'
            ]}
