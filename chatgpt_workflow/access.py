"""Locally issued bearer capabilities scoped to one problem and one workflow role."""

from contextvars import ContextVar
from functools import wraps
import hashlib
import inspect
import secrets
import uuid

from .store import stamp
from .runs import Runs

actor = ContextVar("rethlas_actor", default=None)
READ_TOOLS = {"get_workflow", "get_problem_context", "read_reference", "search_memory",
              "read_artifact", "search_arxiv_theorems", "read_candidate", "get_review", "get_events",
              "export_accepted", "get_run", "finish_run", "list_skills", "read_skill", "save_file", "list_files", "read_file", "download_paper", "export_research"}
ROLE_TOOLS = {"generation": {"record_artifacts", "submit_candidate"},
              "verification": {"begin_review", "record_review_checks", "record_review_findings", "submit_review"}}


class Access:
    def __init__(self, store):
        self.store = store
        self.runs = Runs(store)
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS request_runs (request_id TEXT PRIMARY KEY, run_id TEXT);
                CREATE TABLE IF NOT EXISTS access_keys (
                    credential_id TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE,
                    problem_id TEXT NOT NULL REFERENCES problems(problem_id), role TEXT NOT NULL,
                    label TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS access_log (
                    request_id TEXT PRIMARY KEY, credential_id TEXT, problem_id TEXT,
                    operation TEXT NOT NULL, outcome TEXT NOT NULL, created_at TEXT NOT NULL,
                    completed_at TEXT
                );
            """)

    def issue(self, problem_id, role, label=""):
        if role not in ROLE_TOOLS or len(label) > 200:
            raise ValueError("Use generation or verification role and a label up to 200 characters")
        key = "rth_" + secrets.token_urlsafe(32)
        cid = "credential-" + uuid.uuid4().hex
        with self.store.connect() as db:
            self.store.row(db, "problems", "problem_id", problem_id)
            db.execute("INSERT INTO access_keys VALUES (?,?,?,?,?,?,NULL)",
                       (cid, hashlib.sha256(key.encode()).hexdigest(), problem_id, role, label, stamp()))
        return {"credential_id": cid, "problem_id": problem_id, "role": role, "access_key": key}

    def revoke(self, credential_id):
        with self.store.connect() as db:
            self.store.row(db, "access_keys", "credential_id", credential_id)
            db.execute("UPDATE access_keys SET revoked_at=COALESCE(revoked_at,?) WHERE credential_id=?",
                       (stamp(), credential_id))
        return {"credential_id": credential_id, "revoked": True}

    def list_keys(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT credential_id,problem_id,role,label,created_at,revoked_at "
                                                "FROM access_keys ORDER BY created_at")]

    def audit(self, problem_id):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT a.*,r.run_id FROM access_log a LEFT JOIN request_runs r USING(request_id) WHERE problem_id=? "
                                                "ORDER BY created_at DESC LIMIT 200", (problem_id,))]

    def authorize(self, operation, key, arguments):
        principal = None
        allowed = False
        request_id = "request-" + uuid.uuid4().hex
        with self.store.connect() as db:
            if isinstance(key, str) and key.startswith("rth_") and len(key) == 47:
                row = db.execute("SELECT * FROM access_keys WHERE key_hash=? AND revoked_at IS NULL",
                                 (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
                if row:
                    principal = dict(row)
            if principal:
                pid, role = principal["problem_id"], principal["role"]
                allowed = operation in READ_TOOLS | ROLE_TOOLS[role]
                if "problem_id" in arguments:
                    allowed = allowed and arguments["problem_id"] == pid
                if "candidate_id" in arguments:
                    row = db.execute("SELECT problem_id FROM candidates WHERE candidate_id=?",
                                     (arguments["candidate_id"],)).fetchone()
                    allowed = allowed and bool(row) and row[0] == pid
                if "verification_id" in arguments:
                    row = db.execute("SELECT c.problem_id FROM reviews r JOIN candidates c "
                                     "ON c.candidate_id=r.candidate_id WHERE r.verification_id=?",
                                     (arguments["verification_id"],)).fetchone()
                    allowed = allowed and bool(row) and row[0] == pid
                if operation == "get_workflow":
                    allowed = allowed and arguments.get("role") == role
            db.execute("INSERT INTO access_log VALUES (?,?,?,?,?,?,?)", (
                request_id, principal["credential_id"] if principal else None,
                principal["problem_id"] if principal else None, operation,
                "started" if allowed else "denied", stamp(), None if allowed else stamp()))
        if not allowed:
            raise PermissionError("Access denied. A valid locally issued key for this problem and role is required. "
                                  "Do not guess keys or request access from another tool.")
        return {"credential_id": principal["credential_id"], "problem_id": principal["problem_id"],
                "role": principal["role"], "request_id": request_id}

    def protect(self, fn):
        signature = inspect.signature(fn)
        params = list(signature.parameters.values())
        params.append(inspect.Parameter("access_key", inspect.Parameter.KEYWORD_ONLY, annotation=str))
        params.append(inspect.Parameter("run_id", inspect.Parameter.KEYWORD_ONLY, annotation=str))

        @wraps(fn)
        def locked(*args, **kwargs):
            with self.runs.lock():
                return guarded(*args, **kwargs)

        def guarded(*args, **kwargs):
            rid = kwargs.pop("run_id", None)
            key = kwargs.pop("access_key", None)
            arguments = signature.bind(*args, **kwargs).arguments
            principal = self.authorize(fn.__name__, key, arguments)
            with self.store.connect() as db:
                db.execute("INSERT INTO request_runs VALUES (?,?)", (principal["request_id"], rid))
            try:
                run = self.runs.require(rid, principal, fn.__name__)
            except Exception:
                with self.store.connect() as db:
                    db.execute("UPDATE access_log SET outcome='denied',completed_at=? WHERE request_id=?", (stamp(),principal["request_id"]))
                raise
            principal["run_id"] = rid
            principal["search_mode"] = run["search_mode"]
            token = actor.set(principal)
            outcome = "failed"
            try:
                result = fn(*args, **kwargs)
                outcome = "succeeded"
                return result
            finally:
                actor.reset(token)
                with self.store.connect() as db:
                    db.execute("UPDATE access_log SET outcome=?,completed_at=? WHERE request_id=?",
                               (outcome, stamp(), principal["request_id"]))

        locked.__signature__ = signature.replace(parameters=params)
        locked.__annotations__ = {**fn.__annotations__, "access_key": str, "run_id": str}
        locked.__doc__ = (fn.__doc__ or "") + "\nRequires your locally issued problem/role access_key."
        return locked
