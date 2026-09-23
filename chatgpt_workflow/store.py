"""Transactional state and immutable snapshots. No legacy agent imports or execution."""

# Added in 2026 for the Rethlas ChatGPT MCP workflow; release wording was made portable.

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

from .models import AdditionalFinding, Artifact, ItemCheck, ProofItem, nonblank
from sandbox_workflow.research_contract import validate_claim, digest as baseline_digest, validate_comparison

MAX_DOCUMENT = 2_000_000


def stamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def problem_key(value):
    if not isinstance(value, str) or len(value) > 240 or not re.fullmatch(
        r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value
    ):
        raise ValueError("problem_id must contain letters, digits, _ or -, separated by /")
    return value


def inside(root, path):
    root, path = Path(root).resolve(), Path(path)
    if not path.resolve().is_relative_to(root):
        raise ValueError("Path escapes the configured Rethlas checkout")
    return path


def page(text, offset=0, limit=30_000):
    if type(offset) is not int or offset < 0 or offset > len(text):
        raise ValueError("Invalid character offset")
    if type(limit) is not int or not 1 <= limit <= 50_000:
        raise ValueError("limit must be between 1 and 50000 characters")
    end = min(offset + limit, len(text))
    return {"text": text[offset:end], "offset": offset, "next_offset": end if end < len(text) else None,
            "total_characters": len(text), "sha256": digest(text)}


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = inside(self.root, self.root / ".local/chatgpt-workflow")
        self.path = inside(self.root, self.directory / "workflow.sqlite3")
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported workflow database version: {version}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS problems (
                    problem_id TEXT PRIMARY KEY, statement TEXT NOT NULL,
                    problem_sha256 TEXT NOT NULL, references_json TEXT NOT NULL,
                    source TEXT NOT NULL, created_at TEXT NOT NULL,
                    state TEXT NOT NULL, latest_candidate TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    problem_id TEXT NOT NULL REFERENCES problems(problem_id),
                    record_id TEXT NOT NULL, channel TEXT NOT NULL, text TEXT NOT NULL,
                    provenance TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(problem_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY, problem_id TEXT NOT NULL REFERENCES problems(problem_id),
                    parent_candidate TEXT, candidate_sha256 TEXT NOT NULL, proof_sha256 TEXT NOT NULL,
                    items_json TEXT NOT NULL, markdown TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(problem_id, candidate_sha256)
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    verification_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL UNIQUE REFERENCES candidates(candidate_id),
                    candidate_sha256 TEXT NOT NULL, checks_json TEXT NOT NULL,
                    report_json TEXT, created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY, problem_id TEXT NOT NULL,
                    operation TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_findings (
                    verification_id TEXT NOT NULL REFERENCES reviews(verification_id),
                    record_id TEXT NOT NULL, finding_json TEXT NOT NULL,
                    PRIMARY KEY(verification_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS research_policies (
                    problem_id TEXT PRIMARY KEY REFERENCES problems(problem_id), mode TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_objectives (
                    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id),
                    claim_json TEXT NOT NULL, baseline_json TEXT NOT NULL, baseline_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accepted_results (
                    problem_id TEXT NOT NULL REFERENCES problems(problem_id),
                    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id)
                );
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        inside(self.root, self.path)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def row(db, table, key, value):
        # Table and column names are internal constants, never tool arguments.
        row = db.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown {key}: {value}")
        return dict(row)

    @staticmethod
    def event(db, problem_id, operation, data):
        from .access import actor
        if actor.get() is not None:
            data = {**data, "_access": actor.get()}
        db.execute("INSERT INTO events(problem_id,operation,data_json,created_at) VALUES (?,?,?,?)",
                   (problem_id, operation, encode(data), stamp()))

    def create_problem(self, problem_id, statement, references=None, source="chat", iterative_improvement=False):
        problem_key(problem_id)
        nonblank(statement, "statement")
        references = references or []
        snapshot = encode({"statement": statement, "references": references})
        if len(snapshot.encode("utf-8")) > MAX_DOCUMENT:
            raise ValueError("Problem and references exceed the 2 MB snapshot limit")
        sha = digest(snapshot)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM problems WHERE problem_id=?", (problem_id,)).fetchone()
            if old and old["problem_sha256"] != sha:
                raise ValueError("Problem snapshots are immutable; use a new problem_id for changed input")
            mode = 'improvement' if iterative_improvement else 'fixed'
            if old and self.policy(db, problem_id) != mode:
                raise ValueError('Research policy is immutable; use a new problem_id to select iterative improvement')
            if not old:
                db.execute("INSERT INTO problems VALUES (?,?,?,?,?,?,?,NULL)",
                           (problem_id, statement, sha, encode(references), source, stamp(), "PROVING"))
                self.event(db, problem_id, "create_problem", {"problem_sha256": sha, "source": source})
                db.execute('INSERT INTO research_policies VALUES (?,?)', (problem_id, mode))
        return self.context(problem_id)

    def import_problem(self, problem_id, iterative_improvement=False):
        problem_key(problem_id)
        data = inside(self.root, self.root / "agents/generation/data")
        path = inside(data, data / (problem_id + ".md"))
        if path.stat().st_size > 200_000:
            raise ValueError("Problem file exceeds 200 KB")
        statement = path.read_text(encoding="utf-8")
        refs_path = inside(data, path.with_suffix(".refs"))
        references, total = [], len(statement.encode("utf-8"))
        if refs_path.exists():
            for ref in sorted(refs_path.iterdir()):
                inside(data, ref)
                if not ref.is_file():
                    continue
                if ref.suffix.lower() not in (".md", ".tex", ".txt"):
                    raise ValueError(f"Unsupported reference {ref.name}; supply UTF-8 .md, .tex or .txt")
                total += ref.stat().st_size
                if total > MAX_DOCUMENT or len(references) >= 100:
                    raise ValueError("Reference snapshot exceeds 2 MB or 100 files")
                text = ref.read_text(encoding="utf-8")
                references.append({"reference_id": f"ref-{len(references)+1}",
                                   "source": str(ref.relative_to(data)), "text": text,
                                   "sha256": digest(text)})
        return self.create_problem(problem_id, statement, references, str(path.relative_to(self.root)), iterative_improvement)

    @staticmethod
    def policy(db, problem_id):
        row = db.execute('SELECT mode FROM research_policies WHERE problem_id=?', (problem_id,)).fetchone()
        return row[0] if row else 'fixed'

    def baseline(self, db, problem_id):
        p = self.row(db, 'problems', 'problem_id', problem_id)
        rows = db.execute('SELECT c.candidate_id,c.candidate_sha256 FROM accepted_results a JOIN candidates c USING(candidate_id) WHERE a.problem_id=? ORDER BY a.rowid', (problem_id,)).fetchall()
        # IDs and hashes bind immutable, paged candidates; avoid unbounded context responses.
        return {'original_question_sha256': p['problem_sha256'], 'accepted_results': [dict(r) for r in rows]}

    def list_problems(self, offset=0, limit=30):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid pagination")
        with self.connect() as db:
            rows = db.execute("SELECT problem_id,state,latest_candidate FROM problems "
                              "ORDER BY problem_id LIMIT ? OFFSET ?", (limit+1, offset)).fetchall()
        return {"problems": [dict(r) for r in rows[:limit]],
                "next_offset": offset+limit if len(rows) > limit else None}

    def context(self, problem_id):
        with self.connect() as db:
            p = self.row(db, "problems", "problem_id", problem_id)
            p['mode'] = self.policy(db, problem_id)
            p['baseline'] = self.baseline(db, problem_id)
            p['baseline_sha256'] = baseline_digest(p['baseline'])
            refs = json.loads(p.pop("references_json"))
            p["references"] = [{k: v for k, v in r.items() if k != "text"} for r in refs]
            p["statement"] = page(p["statement"])
            checkpoints = db.execute("SELECT record_id,text,created_at FROM artifacts WHERE "
                                     "problem_id=? AND channel='checkpoint' ORDER BY rowid DESC LIMIT 1",
                                     (problem_id,)).fetchall()
            p["checkpoint"] = ({"record_id": checkpoints[0]["record_id"],
                                "text": page(checkpoints[0]["text"], limit=8000)} if checkpoints else None)
            if p["latest_candidate"]:
                c = self.row(db, "candidates", "candidate_id", p["latest_candidate"])
                p["candidate"] = {k: c[k] for k in ("candidate_id", "candidate_sha256", "proof_sha256")}
                r = db.execute("SELECT verification_id,report_json FROM reviews WHERE candidate_id=?",
                               (c["candidate_id"],)).fetchone()
                p["verification_id"] = r["verification_id"] if r else None
                p["verdict"] = json.loads(r["report_json"])["verdict"] if r and r["report_json"] else None
            p["next_action"] = {
                "PROVING": "Generate a complete candidate, saving research and checkpoints with record_artifacts.",
                "AWAITING_REVIEW": "Ask the separate verifier chat to begin_review for latest_candidate.",
                "REVIEWING": "Continue the separate verifier chat using get_review and read_candidate.",
                "REVISION_REQUIRED": "Generation chat: get_review, fix all findings, submit a new candidate with parent_candidate=latest_candidate.",
                "ACCEPTED": "Call export_accepted to save the exact accepted proof and report; no revision needed.",
                "IMPROVING": "Export accepted results if desired. In a newly user-authorized generation run, seek a strict improvement over the original question and all accepted results.",
            }[p["state"]]
            return p

    def read_reference(self, problem_id, reference_id="statement", offset=0, limit=30_000):
        with self.connect() as db:
            p = self.row(db, "problems", "problem_id", problem_id)
        if reference_id == "statement":
            return page(p["statement"], offset, limit)
        for ref in json.loads(p["references_json"]):
            if ref["reference_id"] == reference_id:
                return {"source": ref["source"], **page(ref["text"], offset, limit)}
        raise ValueError("Unknown reference_id")

    def record_artifacts(self, problem_id, records):
        records = [Artifact.model_validate(r).model_dump() for r in records]
        if not 1 <= len(records) <= 30 or len(encode(records).encode("utf-8")) > MAX_DOCUMENT:
            raise ValueError("Use 1–30 records per call, at most 2 MB")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.row(db, "problems", "problem_id", problem_id)
            for r in records:
                nonblank(r["text"], "artifact text")
                old = db.execute("SELECT * FROM artifacts WHERE problem_id=? AND record_id=?",
                                 (problem_id, r["record_id"])).fetchone()
                if old:
                    if any(old[k] != r[k] for k in ("channel", "text", "provenance")):
                        raise ValueError("record_id already holds different content")
                    continue
                db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?)", (problem_id, r["record_id"],
                           r["channel"], r["text"], r["provenance"], stamp()))
            self.event(db, problem_id, "record_artifacts", {"record_ids": [r["record_id"] for r in records]})
        return {"saved": [r["record_id"] for r in records]}

    def search_memory(self, problem_id, query="", channel="", offset=0, limit=20):
        if offset < 0 or not 1 <= limit <= 50 or len(query) > 2000:
            raise ValueError("Invalid search pagination or query")
        with self.connect() as db:
            self.row(db, "problems", "problem_id", problem_id)
            rows = db.execute("SELECT * FROM artifacts WHERE problem_id=? "
                              "AND (?='' OR channel=?) AND (?='' OR instr(lower(text),lower(?))>0) "
                              "ORDER BY rowid DESC LIMIT ? OFFSET ?",
                              (problem_id, channel, channel, query, query, limit+1, offset)).fetchall()
        return {"records": [{**dict(r), "text": page(r["text"], limit=2000)} for r in rows[:limit]],
                "next_offset": offset+limit if len(rows) > limit else None,
                "search_method": "case-insensitive literal substring; use read_artifact for full text"}

    def read_artifact(self, problem_id, record_id, offset=0, limit=30_000):
        with self.connect() as db:
            r = db.execute("SELECT * FROM artifacts WHERE problem_id=? AND record_id=?",
                           (problem_id, record_id)).fetchone()
        if not r:
            raise ValueError("Unknown record_id for this problem")
        return {**dict(r), "text": page(r["text"], offset, limit)}

    @staticmethod
    def render(items):
        blocks = []
        for item in items:
            body = f"# {item['kind']} {item['item_id']}\n\n## statement\n{item['statement']}\n\n## proof\n{item['proof']}"
            for cite in item["citations"]:
                body += (f"\n\n### External result {cite['citation_id']}\n\n{cite['statement']}\n\n"
                         f"Source: {cite['source']}\n\nTheorem ID: {cite['theorem_id']}\n\n"
                         f"arXiv ID: {cite['arxiv_id']}\n\nApplicability: {cite['applicability']}")
            blocks.append(body)
        return "\n\n".join(blocks) + "\n"

    def submit_candidate(self, problem_id, items, parent_candidate=None, improvement=None, baseline_sha256=None):
        items = [ProofItem.model_validate(i).model_dump() for i in items]
        if not 1 <= len(items) <= 200 or len(encode(items).encode("utf-8")) > MAX_DOCUMENT:
            raise ValueError("A candidate must contain 1–200 items, totaling at most 2 MB")
        ids = [i["item_id"] for i in items]
        if len(set(ids)) != len(ids) or ids[-1] != "main" or items[-1]["kind"] != "theorem":
            raise ValueError("Unique item IDs required; final item must be theorem main")
        for item in items:
            nonblank(item["statement"], "item statement")
            nonblank(item["proof"], "item proof")
            refs = [c["citation_id"] for c in item["citations"]]
            if len(refs) != len(set(refs)):
                raise ValueError("Citation IDs must be unique within each item")
            for cite in item["citations"]:
                for field in ("statement", "source", "applicability"):
                    nonblank(cite[field], "citation " + field)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            p = self.row(db, "problems", "problem_id", problem_id)
            improving = self.policy(db, problem_id) == 'improvement'
            baseline = self.baseline(db, problem_id)
            if improving:
                validate_claim(improvement)
                if items[-1]['statement'] != improvement['statement']:
                    raise ValueError('Main statement must equal the proposed improvement statement exactly')
            elif improvement is not None or baseline_sha256 is not None:
                raise ValueError('Fixed-statement problems cannot accept improvement metadata')
            elif items[-1]["statement"] != p["statement"]:
                raise ValueError("Main statement must exactly equal the stored original, including whitespace")
            binding_data = {"problem_sha256": p["problem_sha256"], "items": items, "parent_candidate": parent_candidate}
            if improving:
                binding_data.update(improvement=improvement, baseline_sha256=baseline_sha256)
            binding = encode(binding_data)
            sha = digest(binding)
            old = db.execute("SELECT * FROM candidates WHERE problem_id=? AND candidate_sha256=?",
                             (problem_id, sha)).fetchone()
            if old:
                return {"candidate_id": old["candidate_id"], "candidate_sha256": sha,
                        "proof_sha256": old["proof_sha256"], "replayed": True}
            if improving and baseline_sha256 != baseline_digest(baseline):
                raise ValueError('Stale improvement baseline; read the current problem context')
            if p["state"] not in ("PROVING", "REVISION_REQUIRED", "IMPROVING"):
                raise ValueError("Finish the current review before submitting another candidate")
            if parent_candidate != p["latest_candidate"]:
                raise ValueError("parent_candidate must match the latest candidate (or null for the first)")
            markdown = self.render(items)
            if len(markdown.encode("utf-8")) > MAX_DOCUMENT:
                raise ValueError("Rendered proof exceeds 2 MB")
            cid = "candidate-" + uuid.uuid4().hex
            db.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?)", (cid, problem_id, parent_candidate,
                       sha, digest(markdown), encode(items), markdown, stamp()))
            if improving:
                db.execute('INSERT INTO candidate_objectives VALUES (?,?,?,?)', (cid, encode(improvement), encode(baseline), baseline_sha256))
            db.execute("UPDATE problems SET state='AWAITING_REVIEW',latest_candidate=? WHERE problem_id=?",
                       (cid, problem_id))
            self.event(db, problem_id, "submit_candidate", {"candidate_id": cid, "candidate_sha256": sha})
        return {"candidate_id": cid, "candidate_sha256": sha, "proof_sha256": digest(markdown),
                "next_action": "Ask the separate verifier chat to begin_review with this candidate_id."}

    def read_candidate(self, candidate_id, item_id="", offset=0, limit=30_000):
        with self.connect() as db:
            c = self.row(db, "candidates", "candidate_id", candidate_id)
            objective = db.execute('SELECT * FROM candidate_objectives WHERE candidate_id=?', (candidate_id,)).fetchone()
        items = json.loads(c["items_json"])
        result = {k: c[k] for k in ("candidate_id", "problem_id", "candidate_sha256", "proof_sha256", "parent_candidate")}
        if objective:
            result.update(improvement=json.loads(objective['claim_json']), baseline=json.loads(objective['baseline_json']), baseline_sha256=objective['baseline_sha256'])
        result["items"] = [{"item_id": i["item_id"], "kind": i["kind"],
                            "citation_ids": [r["citation_id"] for r in i["citations"]]} for i in items]
        if item_id:
            matching = [i for i in items if i["item_id"] == item_id]
            if not matching:
                raise ValueError("Unknown item_id")
            result["content"] = page(encode(matching[0]), offset, limit)
            result["format"] = "json proof item (concatenate all pages before parsing)"
        else:
            result["content"] = page(c["markdown"], offset, limit)
            result["format"] = "markdown"
        return result

    def begin_review(self, candidate_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            c = self.row(db, "candidates", "candidate_id", candidate_id)
            p = self.row(db, "problems", "problem_id", c["problem_id"])
            if p["latest_candidate"] != candidate_id:
                raise ValueError("This candidate is stale; use the problem's latest_candidate")
            old = db.execute("SELECT * FROM reviews WHERE candidate_id=?", (candidate_id,)).fetchone()
            if old:
                vid = old["verification_id"]
            else:
                if p["state"] != "AWAITING_REVIEW":
                    raise ValueError("Candidate is not awaiting review")
                vid = "review-" + uuid.uuid4().hex
                db.execute("INSERT INTO reviews VALUES (?,?,?,?,NULL,?,NULL)",
                           (vid, candidate_id, c["candidate_sha256"], "{}", stamp()))
                db.execute("UPDATE problems SET state='REVIEWING' WHERE problem_id=?", (c["problem_id"],))
                self.event(db, c["problem_id"], "begin_review", {"verification_id": vid})
        return self.get_review(vid)

    def get_review(self, verification_id, offset=0, limit=30_000):
        with self.connect() as db:
            r = self.row(db, "reviews", "verification_id", verification_id)
            c = self.row(db, "candidates", "candidate_id", r["candidate_id"])
            findings = [json.loads(x[0]) for x in db.execute(
                "SELECT finding_json FROM review_findings WHERE verification_id=? ORDER BY rowid",
                (verification_id,)).fetchall()]
        checks = json.loads(r.pop("checks_json"))
        raw_report = r.pop("report_json")
        report = json.loads(raw_report) if raw_report else None
        r["problem_id"] = c["problem_id"]
        r["checked_items"] = list(checks)
        r["missing_items"] = [i["item_id"] for i in json.loads(c["items_json"]) if i["item_id"] not in checks]
        r["content"] = page(encode({"checks": checks, "additional_findings": findings, "report": report}), offset, limit)
        r["format"] = "json; concatenate pages to read all checks and the report"
        return r

    def record_review_findings(self, verification_id, candidate_sha256, findings):
        findings = [AdditionalFinding.model_validate(x).model_dump() for x in findings]
        if not 1 <= len(findings) <= 100 or len(encode(findings).encode("utf-8")) > MAX_DOCUMENT:
            raise ValueError("Use 1–100 additional findings, at most 2 MB")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            r = self.row(db, "reviews", "verification_id", verification_id)
            c = self.row(db, "candidates", "candidate_id", r["candidate_id"])
            p = self.row(db, "problems", "problem_id", c["problem_id"])
            if candidate_sha256 != r["candidate_sha256"] or p["latest_candidate"] != c["candidate_id"]:
                raise ValueError("Review binding mismatch or stale candidate")
            if r["report_json"]:
                raise ValueError("Completed review is immutable")
            for finding in findings:
                nonblank(finding["location"], "location")
                nonblank(finding["issue"], "issue")
                old = db.execute("SELECT finding_json FROM review_findings WHERE verification_id=? AND record_id=?",
                                 (verification_id, finding["record_id"])).fetchone()
                if old and old[0] != encode(finding):
                    raise ValueError("Existing finding cannot be replaced")
                if not old:
                    db.execute("INSERT INTO review_findings VALUES (?,?,?)",
                               (verification_id, finding["record_id"], encode(finding)))
            self.event(db, c["problem_id"], "record_review_findings", {"verification_id": verification_id,
                       "record_ids": [x["record_id"] for x in findings]})
        return {"saved": [x["record_id"] for x in findings]}

    def record_review_checks(self, verification_id, candidate_sha256, checks):
        checks = [ItemCheck.model_validate(c).model_dump() for c in checks]
        if not 1 <= len(checks) <= 200 or len(encode(checks).encode("utf-8")) > MAX_DOCUMENT:
            raise ValueError("Checks must contain 1–200 entries, at most 2 MB per call")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            r = self.row(db, "reviews", "verification_id", verification_id)
            c = self.row(db, "candidates", "candidate_id", r["candidate_id"])
            p = self.row(db, "problems", "problem_id", c["problem_id"])
            if candidate_sha256 != r["candidate_sha256"] or p["latest_candidate"] != c["candidate_id"]:
                raise ValueError("Review binding mismatch or stale candidate")
            if r["report_json"]:
                raise ValueError("Review already completed; its checks are immutable")
            saved = json.loads(r["checks_json"])
            items = {i["item_id"]: i for i in json.loads(c["items_json"])}
            for check in checks:
                key = check["item_id"]
                nonblank(check["assessment"], "assessment")
                if key not in items:
                    raise ValueError("Unknown proof item in review")
                expected = {x["citation_id"] for x in items[key]["citations"]}
                actual = [x["citation_id"] for x in check["reference_checks"]]
                if set(actual) != expected or len(actual) != len(expected):
                    raise ValueError("Each declared citation requires exactly one reference check")
                for rc in check["reference_checks"]:
                    nonblank(rc["assessment"], "reference assessment")
                for finding in check["critical_errors"] + check["gaps"]:
                    nonblank(finding["location"], "finding location")
                    nonblank(finding["issue"], "finding issue")
                if key in saved and saved[key] != check:
                    raise ValueError("Recorded checks cannot be overwritten; preserve findings and revise the proof")
                saved[key] = check
            if len(encode(saved).encode("utf-8")) > MAX_DOCUMENT:
                raise ValueError("Complete review exceeds 2 MB")
            db.execute("UPDATE reviews SET checks_json=? WHERE verification_id=?", (encode(saved), verification_id))
            self.event(db, c["problem_id"], "record_review_checks", {"verification_id": verification_id,
                       "item_ids": [x["item_id"] for x in checks]})
        return {"verification_id": verification_id, "checked_items": list(saved),
                "missing_items": [key for key in items if key not in saved]}

    def submit_review(self, verification_id, candidate_sha256, summary, repair_hints="", improvement_assessment=None):
        nonblank(summary, "summary", 50_000)
        if not isinstance(repair_hints, str) or len(repair_hints) > 200_000:
            raise ValueError("Invalid repair_hints")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            r = self.row(db, "reviews", "verification_id", verification_id)
            c = self.row(db, "candidates", "candidate_id", r["candidate_id"])
            p = self.row(db, "problems", "problem_id", c["problem_id"])
            if candidate_sha256 != r["candidate_sha256"] or p["latest_candidate"] != c["candidate_id"]:
                raise ValueError("Review binding mismatch or stale candidate")
            checks = json.loads(r["checks_json"])
            ordered = [i["item_id"] for i in json.loads(c["items_json"])]
            if set(checks) != set(ordered):
                raise ValueError("Cannot complete review: every proof item requires a recorded check")
            errors, gaps, references = [], [], []
            for entry in db.execute("SELECT finding_json FROM review_findings WHERE verification_id=? ORDER BY rowid",
                                    (verification_id,)).fetchall():
                f = json.loads(entry[0])
                (errors if f["kind"] == "critical_error" else gaps).append(
                    {"location": f["location"], "issue": f["issue"]})
            for key in ordered:
                check = checks[key]
                errors.extend(check["critical_errors"])
                gaps.extend(check["gaps"])
                for rc in check["reference_checks"]:
                    references.append(f"{key}/{rc['citation_id']}: {rc['outcome']}: {rc['assessment']}")
                    finding = {"location": f"{key}/{rc['citation_id']}", "issue": rc["assessment"]}
                    if rc["outcome"] == "wrong":
                        errors.append(finding)
                    elif rc["outcome"] == "unresolved":
                        gaps.append(finding)
            verdict = "wrong" if errors or gaps else "correct"
            if verdict == "wrong":
                nonblank(repair_hints, "repair_hints")
            elif repair_hints != "":
                raise ValueError("repair_hints must be empty when no findings exist")
            report = {"verification_report": {"summary": summary, "critical_errors": errors, "gaps": gaps,
                       "checked_items": ordered, "external_reference_checks": references},
                      "verdict": verdict, "repair_hints": repair_hints}
            objective = db.execute('SELECT * FROM candidate_objectives WHERE candidate_id=?', (c['candidate_id'],)).fetchone()
            improving = objective is not None
            strict_gain = True
            if improving:
                strict_gain = validate_comparison(improvement_assessment, objective['baseline_sha256'])
                report['improvement_assessment'] = improvement_assessment
            elif improvement_assessment is not None:
                raise ValueError('Unexpected improvement assessment on a fixed theorem')
            promoted = verdict == 'correct' and strict_gain
            if r["report_json"]:
                if json.loads(r["report_json"]) != report:
                    raise ValueError("Completed review is immutable")
            else:
                if p["state"] != "REVIEWING":
                    raise ValueError("Problem is not under review")
                db.execute("UPDATE reviews SET report_json=?,completed_at=? WHERE verification_id=?",
                           (encode(report), stamp(), verification_id))
                db.execute("UPDATE problems SET state=? WHERE problem_id=?",
                           (("IMPROVING" if improving else "ACCEPTED") if promoted else "REVISION_REQUIRED", c["problem_id"]))
                if promoted:
                    db.execute('INSERT OR IGNORE INTO accepted_results VALUES (?,?)', (c['problem_id'], c['candidate_id']))
                self.event(db, c["problem_id"], "submit_review", {"verification_id": verification_id, "verdict": verdict})
        return {"verification_id": verification_id, "candidate_id": c["candidate_id"], "verdict": verdict,
                "accepted": promoted,
                "next_action": ("Export this result; finish this authorized run. User opens the next generation run to improve further." if improving and promoted else "export_accepted" if promoted else "Ask generation chat to get_review and revise")}

    @staticmethod
    def write_once(path, text):
        data = text.encode("utf-8")
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError(f"Export collision: {path.name} already contains different content")
            return
        fd, temporary = tempfile.mkstemp(prefix=".export-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temporary, path)  # Atomic creation without replacing existing files.
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError(f"Export collision: {path.name}") from None
        finally:
            os.unlink(temporary)

    def export_accepted(self, problem_id, candidate_id=None):
        problem_key(problem_id)
        with self.connect() as db:
            p = self.row(db, "problems", "problem_id", problem_id)
            improving = self.policy(db, problem_id) == 'improvement'
            if improving:
                accepted = db.execute('SELECT candidate_id FROM accepted_results WHERE problem_id=? ORDER BY rowid DESC', (problem_id,)).fetchall()
                ids = [r[0] for r in accepted]
                candidate_id = candidate_id or (ids[0] if ids else None)
                if candidate_id not in ids:
                    raise ValueError('Only an accepted improvement can be exported')
            else:
                if p['state'] != 'ACCEPTED' or candidate_id not in (None, p['latest_candidate']):
                    raise ValueError('Only an accepted current candidate can be exported')
                candidate_id = p['latest_candidate']
            c = self.row(db, "candidates", "candidate_id", candidate_id)
            r = db.execute("SELECT * FROM reviews WHERE candidate_id=?", (c["candidate_id"],)).fetchone()
            if not r or not r["report_json"] or json.loads(r["report_json"])["verdict"] != "correct":
                raise ValueError("Missing accepting review")
            if digest(c["markdown"]) != c["proof_sha256"] or r["candidate_sha256"] != c["candidate_sha256"]:
                raise ValueError("Stored proof/review binding is inconsistent")
            binding_data = {"problem_sha256": p["problem_sha256"], "items": json.loads(c["items_json"]), "parent_candidate": c["parent_candidate"]}
            if improving:
                objective = self.row(db, 'candidate_objectives', 'candidate_id', candidate_id)
                binding_data.update(improvement=json.loads(objective['claim_json']), baseline_sha256=objective['baseline_sha256'])
                if baseline_digest(json.loads(objective['baseline_json'])) != objective['baseline_sha256'] or not validate_comparison(json.loads(r['report_json'])['improvement_assessment'], objective['baseline_sha256']):
                    raise ValueError('Invalid accepted improvement binding')
            binding = encode(binding_data)
            if digest(binding) != c["candidate_sha256"] or self.render(json.loads(c["items_json"])) != c["markdown"]:
                raise ValueError("Stored candidate content does not match its binding")
            manifest = {"problem_id": problem_id, "problem_sha256": p["problem_sha256"],
                        "candidate_id": c["candidate_id"], "candidate_sha256": c["candidate_sha256"],
                        "proof_sha256": c["proof_sha256"], "verification_id": r["verification_id"],
                        "verification_kind": "LLM review, not formal proof certification"}
        output = inside(self.root, self.root / "agents/generation/results" / problem_id)
        if improving:
            output = inside(self.root, output / 'improvements' / candidate_id)
        output.mkdir(parents=True, exist_ok=True)
        files = {"verification.json": json.dumps(json.loads(r["report_json"]), ensure_ascii=False, indent=2)+"\n",
                 "chatgpt_manifest.json": json.dumps(manifest, indent=2)+"\n",
                 "blueprint_verified.md": c["markdown"]}
        # Preflight collisions, then publish the proof last. Retrying after a crash is safe.
        for name, text in files.items():
            path = inside(self.root, output / name)
            if path.exists() and path.read_bytes() != text.encode("utf-8"):
                raise ValueError(f"Export collision: {path}; existing outputs will not be overwritten")
        for name, text in files.items():
            self.write_once(inside(self.root, output / name), text)
        with self.connect() as db:
            self.event(db, problem_id, "export_accepted", manifest)
        return {**manifest, "files": [str(output / n) for n in files]}

    def events(self, problem_id, after_event_id=0, limit=50):
        if after_event_id < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid event pagination")
        with self.connect() as db:
            self.row(db, "problems", "problem_id", problem_id)
            rows = db.execute("SELECT * FROM events WHERE problem_id=? AND event_id>? "
                              "ORDER BY event_id LIMIT ?", (problem_id, after_event_id, limit+1)).fetchall()
        return {"events": [dict(r) for r in rows[:limit]],
                "next_after_event_id": rows[limit-1]["event_id"] if len(rows) > limit else None}
