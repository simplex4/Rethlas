"""Local run authorization and cross-process serialization of MCP operations."""
import fcntl
from contextlib import contextmanager
import uuid
from .store import stamp

class Runs:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS research_runs (
                run_id TEXT PRIMARY KEY, credential_id TEXT NOT NULL, problem_id TEXT NOT NULL,
                role TEXT NOT NULL, sequence INTEGER NOT NULL, search_mode TEXT NOT NULL,
                status TEXT NOT NULL, base_checkpoint TEXT, final_record TEXT,
                created_at TEXT NOT NULL, finished_at TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON research_runs(problem_id)
                WHERE status='active';''')

    @contextmanager
    def lock(self):
        with (self.store.directory / 'run.lock').open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def open(self, credential_id):
        with self.lock(), self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            key = db.execute('SELECT * FROM access_keys WHERE credential_id=? AND revoked_at IS NULL', (credential_id,)).fetchone()
            if not key:
                raise ValueError('Unknown or revoked credential ID')
            pid, role = key['problem_id'], key['role']
            if db.execute("SELECT 1 FROM research_runs WHERE problem_id=? AND status='active'", (pid,)).fetchone():
                raise ValueError('Problem already has an active run; finish or cancel it first')
            seq = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM research_runs WHERE problem_id=? AND role=?', (pid,role)).fetchone()[0]
            checkpoint = db.execute("SELECT record_id FROM artifacts WHERE problem_id=? AND channel='checkpoint' ORDER BY rowid DESC LIMIT 1",(pid,)).fetchone()
            rid = 'run-' + uuid.uuid4().hex
            mode = 'independent' if role == 'generation' and seq % 2 else 'external-search'
            db.execute('INSERT INTO research_runs VALUES (?,?,?,?,?,?,?, ?,NULL,?,NULL)',
                       (rid,credential_id,pid,role,seq,mode,'active',checkpoint[0] if checkpoint else None,stamp()))
            return dict(db.execute('SELECT * FROM research_runs WHERE run_id=?',(rid,)).fetchone())

    def require(self, rid, principal, operation):
        with self.store.connect() as db:
            r = db.execute('SELECT * FROM research_runs WHERE run_id=?',(rid,)).fetchone()
        if not r or r['status'] != 'active' or r['credential_id'] != principal['credential_id']:
            raise PermissionError('Run is missing, closed, or belongs to another credential. Stop; only the user can open a new run locally.')
        if r['search_mode']=='independent' and operation in {'search_arxiv_theorems','download_paper'}:
            raise PermissionError('This run permits independent reasoning and stored references only; external search is disabled.')
        return dict(r)

    def finish(self, rid, final_record):
        with self.store.connect() as db:
            r = db.execute('SELECT * FROM research_runs WHERE run_id=?',(rid,)).fetchone()
            if r['role']=='generation':
                a=db.execute("SELECT 1 FROM artifacts WHERE problem_id=? AND record_id=? AND channel='checkpoint' AND created_at>=?",(r['problem_id'],final_record,r['created_at'])).fetchone()
                if not a: raise ValueError('Finish with a checkpoint saved during this run')
            else:
                a=db.execute('SELECT 1 FROM reviews r JOIN candidates c ON r.candidate_id=c.candidate_id WHERE r.verification_id=? AND c.problem_id=?',(final_record,r['problem_id'])).fetchone()
                if not a: raise ValueError('Finish with the verification_id for this problem')
            db.execute("UPDATE research_runs SET status='finished',final_record=?,finished_at=? WHERE run_id=? AND status='active'",(final_record,stamp(),rid))
            self.store.event(db,r['problem_id'],'finish_run',{'run_id':rid,'final_record':final_record})
        return {'run_id':rid,'status':'finished','final_record':final_record,'next_action':'Stop. Wait for the user to provide a newly opened run_id.'}

    def cancel(self,rid):
        with self.lock(), self.store.connect() as db:
            if not db.execute('SELECT 1 FROM research_runs WHERE run_id=?',(rid,)).fetchone(): raise ValueError('Unknown run')
            db.execute("UPDATE research_runs SET status='cancelled',finished_at=? WHERE run_id=? AND status='active'",(stamp(),rid))
        return {'run_id':rid,'status':'closed'}
