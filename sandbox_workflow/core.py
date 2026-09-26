"""Durable, fail-closed orchestration of separate sandboxed Codex processes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from .research_contract import (digest, validate_candidate, validate_claim, comparison_schema,
                               validate_comparison, IMPROVEMENT_INSTRUCTIONS,
                               VERIFIER_IMPROVEMENT_INSTRUCTIONS)

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent
VERSION = 1


class WorkflowError(Exception):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(data, bytes):
        data = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def inside(root, value):
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise WorkflowError(f"Path escapes workspace: {value}")
    return path


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkflowError("Another coordinator owns this run") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def codex_home():
    # Deliberately do not consult CODEX_CLI_HOME (the legacy personal account).
    return str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve())


def settings():
    return {
        "codex_home": codex_home(),
        "codex_bin": os.environ.get("CODEX_BIN", "codex"),
        "generator_model": os.environ.get("MODEL", "gpt-6-astra"),
        "generator_effort": os.environ.get("REASONING_EFFORT", "max"),
        "verifier_model": os.environ.get("CODEX_MODEL", "gpt-6-astra"),
        "verifier_effort": os.environ.get("CODEX_REASONING_EFFORT", "max"),
        "sandbox": "workspace-write", "approval_policy": "on-request",
        "approvals_reviewer": "auto_review",
        "web_search_mode": "cached",
    }


def mcp_overrides(home, cwd, repo=None):
    """Disable configured MCP entries without skipping managed policy or exec rules."""
    paths = [Path(home).expanduser().resolve() / "config.toml"]
    root = Path(repo if repo is not None else REPO).resolve()
    workspace = Path(cwd).resolve()
    # Project layers stop at the repository root. Walking beyond it can import
    # ~/.codex from another account and invent transport-less server entries.
    if workspace.is_relative_to(root):
        directories = [workspace]
        while directories[-1] != root:
            directories.append(directories[-1].parent)
        paths += [p / ".codex" / "config.toml" for p in reversed(directories)]
    names = set()
    for path in paths:
        if path.is_file():
            with path.open("rb") as stream:
                config = tomllib.load(stream)
            names.update(config.get("mcp_servers", {}))
    # Codex's -c dotted-key parser does not unquote individual path components.
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names):
        raise WorkflowError("An MCP server name cannot be safely disabled with this Codex CLI; use simple alphanumeric server names")
    return [f"mcp_servers.{name}.enabled=false" for name in sorted(names)]


def command(config, cwd, role, search_mode, output, schema=None, session=None):
    model = config[f"{role}_model"]
    effort = config[f"{role}_effort"]
    # Global options precede exec so fresh and resume use the same CLI parser.
    cmd = [config["codex_bin"], "-C", str(cwd), "--sandbox", "workspace-write",
           "-c", 'sandbox_mode="workspace-write"',
           "-c", 'approval_policy="on-request"',
           "-c", 'approvals_reviewer="auto_review"',
           "-c", f"model_reasoning_effort={json.dumps(effort)}",
           "-c", f"web_search={json.dumps(config.get('web_search_mode', 'cached') if search_mode == 'live' else 'disabled')}"]
    for override in mcp_overrides(config["codex_home"], cwd):
        cmd += ["-c", override]
    if role == "generator" and (Path(cwd) / "subgoal.toml").is_file():
        cmd += ["-c", "agents.max_threads=10", "-c", "agents.max_depth=3",
                "-c", 'agents.subgoal-prover.description="Bounded mathematical subgoal researcher"',
                "-c", "agents.subgoal-prover.config_file=" + json.dumps(str(Path(cwd) / "subgoal.toml"))]
    cmd += ["exec"]
    if session:
        cmd += ["resume", session]
    cmd += ["-m", model, "--json", "--output-last-message", str(output)]
    if schema:
        cmd += ["--output-schema", str(schema)]
    cmd += ["-"]
    return cmd


def invoke(cmd, cwd, env, prompt, events, stderr):
    """Never execute an agent's proposed command in the coordinator process."""
    with events.open("xb") as out, stderr.open("xb") as err:
        process = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=out, stderr=err, start_new_session=True)
        atomic(events.parent / "process.json", {"pid": process.pid})
        try:
            process.communicate(prompt.encode())
        except BaseException:
            # An explicit interruption must not leave an untracked writer behind.
            import signal
            os.killpg(process.pid, signal.SIGTERM)
            process.wait()
            raise
    return process.returncode


def parse_events(path):
    result = {"session": None, "completed": False, "failed": False, "mcp_used": False}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # An interrupted final write is not a completed turn.
            result["failed"] = True
            continue
        if not isinstance(event, dict):
            result["failed"] = True
            continue
        if event.get("type") == "thread.started":
            session = event.get("thread_id")
            if not isinstance(session, str) or not session.strip() or (result["session"] and result["session"] != session):
                result["failed"] = True
            else:
                result["session"] = session
        if event.get("type") == "turn.completed":
            result["completed"] = True
        if event.get("type") == "turn.failed":
            result["failed"] = True
        if isinstance(event.get("item"), dict) and event["item"].get("type") == "mcp_tool_call":
            result["mcp_used"] = True
    return result


def verification_schema(improvement=False):
    finding = {"type": "object", "additionalProperties": False,
               "properties": {"location": {"type": "string"}, "issue": {"type": "string"}},
               "required": ["location", "issue"]}
    report = {"type": "object", "additionalProperties": False,
              "properties": {"summary": {"type": "string"},
                             "critical_errors": {"type": "array", "items": finding},
                             "gaps": {"type": "array", "items": finding}},
              "required": ["summary", "critical_errors", "gaps"]}
    props = {"statement_sha256": {"type": "string"}, "candidate_sha256": {"type": "string"},
             "verification_report": report, "verdict": {"type": "string", "enum": ["correct", "wrong"]},
             "repair_hints": {"type": "string"}}
    if improvement:
        props["improvement_assessment"] = comparison_schema()
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}


def validate_review(value, binding):
    improvement = binding.get("mode") == "improvement"
    if not isinstance(value, dict) or set(value) != set(verification_schema(improvement)["properties"]):
        raise WorkflowError("Verifier output has missing or unexpected fields")
    for key in ("candidate_sha256", "statement_sha256"):
        if value[key] != binding[key]:
            raise WorkflowError(f"Verifier output has stale or mismatched {key}")
    report = value["verification_report"]
    if not isinstance(report, dict) or set(report) != {"summary", "critical_errors", "gaps"}:
        raise WorkflowError("Invalid verification report fields")
    if not isinstance(report["summary"], str) or not report["summary"].strip():
        raise WorkflowError("Verification summary is empty")
    for channel in ("critical_errors", "gaps"):
        if not isinstance(report[channel], list):
            raise WorkflowError(f"{channel} must be a list")
        for finding in report[channel]:
            if not isinstance(finding, dict) or set(finding) != {"location", "issue"}:
                raise WorkflowError("Invalid finding")
            if any(not isinstance(v, str) or not v.strip() for v in finding.values()):
                raise WorkflowError("Finding location and issue must be nonblank text")
    correct = not report["critical_errors"] and not report["gaps"]
    if value["verdict"] != ("correct" if correct else "wrong"):
        raise WorkflowError("Verdict contradicts findings")
    hints = value["repair_hints"]
    if not isinstance(hints, str) or (correct and hints != "") or (not correct and not hints.strip()):
        raise WorkflowError("Repair hints contradict verdict")
    if improvement:
        validate_comparison(value["improvement_assessment"], binding["baseline_sha256"])
    return value


class Workflow:
    def __init__(self, repo=REPO, runner=invoke, reporter=None):
        self.repo = Path(repo).resolve()
        self.root = self.repo / ".local" / "sandbox-workflow" / "runs"
        self.runner = runner
        self.reporter = reporter

    def report(self, event, **details):
        if self.reporter:
            self.reporter(event, details)

    def path(self, run_id):
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise WorkflowError("Invalid run ID")
        return inside(self.root, run_id)

    def load(self, run_id):
        run = self.path(run_id)
        manifest = read_json(run / "manifest.json")
        if manifest.get("version") != VERSION or manifest.get("run_id") != run_id:
            raise WorkflowError("Unsupported or mismatched run manifest")
        return run, manifest

    def save(self, run, manifest):
        manifest["updated_at"] = stamp()
        atomic(run / "manifest.json", manifest)

    def prepare_workspace(self, run, destination, role):
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copy2(run / "inputs" / "statement.md", destination / "statement.md")
        shutil.copytree(run / "inputs" / "references", destination / "references")
        shutil.copy2(PACKAGE / "research.py", destination / "research.py")
        shutil.copy2(PACKAGE / "research_contract.py", destination / "research_contract.py")
        shutil.copy2(PACKAGE / "templates" / f"{role}.md", destination / "AGENTS.md")
        shutil.copy2(PACKAGE / "templates" / "subgoal.md", destination / "subgoal.md")
        shutil.copytree(PACKAGE / "templates" / "skills", destination / "skills")

    def create(self, problem, config=None, iterative_improvement=False):
        problem = inside(self.repo, problem)
        if not problem.is_file() or problem.suffix != ".md":
            raise WorkflowError("Problem must be an existing Markdown file inside the repository")
        statement = problem.read_bytes()
        if not statement.decode("utf-8").strip():
            raise WorkflowError("Empty problem statement")
        run_id = uuid.uuid4().hex
        run = self.path(run_id)
        (run / "inputs" / "references").mkdir(parents=True)
        atomic(run / "inputs" / "statement.md", statement)
        refs = problem.with_suffix(".refs")
        if refs.exists():
            if not refs.resolve().is_relative_to(self.repo):
                raise WorkflowError("Reference directory escapes repository")
            for path in sorted(refs.rglob("*")):
                if path.is_symlink():
                    raise WorkflowError(f"Reference symlinks are not supported: {path}")
                if path.is_file() and ".extracted" not in path.relative_to(refs).parts:
                    target = run / "inputs" / "references" / path.relative_to(refs)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
        hashes = {str(p.relative_to(run / "inputs")): sha(p.read_bytes())
                  for p in (run / "inputs").rglob("*") if p.is_file()}
        manifest = {"version": VERSION, "mode": "sandboxed-codex", "run_id": run_id,
                    "created_at": stamp(), "problem": str(problem.relative_to(self.repo)),
                    "input_hashes": hashes, "statement_sha256": sha(statement),
                    "settings": config or settings(), "status": "ready", "phase": "generation",
                    "next_iteration": 0, "generator_session": None, "attempts": [],
                    "candidates": [], "pending_candidate": None, "error": None}
        manifest.update(mode_policy="improvement" if iterative_improvement else "fixed", accepted_candidates=[])
        self.prepare_workspace(run, run / "generation", "generation")
        child = (PACKAGE / "templates" / "subgoal.md").read_text()
        cfg = manifest["settings"]
        atomic(run / "generation" / "subgoal.toml", (
            'name = "subgoal-prover"\n'
            + 'model = ' + json.dumps(cfg["generator_model"]) + '\n'
            + 'model_reasoning_effort = ' + json.dumps(cfg["generator_effort"]) + '\n'
            + 'developer_instructions = ' + json.dumps(child) + '\n').encode())
        self.save(run, manifest)
        return run_id

    def check_inputs(self, run, manifest):
        actual = {str(p.relative_to(run / "inputs")): sha(p.read_bytes())
                  for p in (run / "inputs").rglob("*") if p.is_file()}
        if actual != manifest["input_hashes"]:
            raise WorkflowError("Frozen input snapshot was changed")

    @staticmethod
    def check_workspace_inputs(workspace, manifest):
        for name, digest in manifest["input_hashes"].items():
            path = inside(workspace, name)
            if not path.is_file() or sha(path.read_bytes()) != digest:
                raise WorkflowError(f"Agent modified an original input: {name}")

    def attempt(self, run, manifest, role, workspace, search_mode, prompt, schema=None):
        index = len(manifest["attempts"])
        log = run / "logs" / f"{index:05d}-{role}-{uuid.uuid4().hex[:8]}"
        log.mkdir(parents=True, exist_ok=False)
        output = log / "final.txt"
        turn = {"run_id": manifest["run_id"], "role": role, "attempt": index,
                "iteration": manifest["next_iteration"], "search_mode": search_mode,
                "mode": manifest.get("mode_policy", "fixed")}
        atomic(workspace / "turn.json", turn)
        session = manifest["generator_session"] if role == "generator" else None
        cmd = command(manifest["settings"], workspace, role, search_mode, output, schema, session)
        atomic(log / "invocation.json", {"command": cmd, "cwd": str(workspace), "turn": turn})
        atomic(log / "prompt.txt", prompt.encode())
        attempt = {"role": role, "workspace": str(workspace.relative_to(run)),
                   "log": str(log.relative_to(run)), "status": "running"}
        manifest["attempts"].append(attempt)
        if role == "generator":
            manifest["next_iteration"] += 1
        manifest["status"] = "running"
        self.save(run, manifest)
        self.report("attempt_started", run=run, log=log, turn=turn)
        env = os.environ.copy()
        env["CODEX_HOME"] = manifest["settings"]["codex_home"]
        # Do not let API-key environment overrides change the authentication context.
        env.pop("CODEX_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
        rc = self.runner(cmd, workspace, env, prompt, log / "events.jsonl", log / "stderr.log")
        atomic(log / "completion.json", {"returncode": rc})
        completed_log = self.finish_attempt(run, manifest, attempt)
        self.report("attempt_finished", run=run, log=log, turn=turn)
        return completed_log

    def finish_attempt(self, run, manifest, attempt):
        log = inside(run, attempt["log"])
        events = parse_events(log / "events.jsonl")
        if attempt["role"] == "generator" and events["session"]:
            prior = manifest["generator_session"]
            if prior and events["session"] != prior:
                raise WorkflowError("Resumed generator returned a different session ID")
            manifest["generator_session"] = events["session"]
        completion = log / "completion.json"
        valid = (completion.exists() and read_json(completion).get("returncode") == 0
                 and events["completed"] and not events["failed"] and not events["mcp_used"])
        if not valid:
            attempt["status"] = "interrupted" if not completion.exists() else "failed"
            self.save(run, manifest)
            raise WorkflowError(f"Agent turn incomplete or failed; inspect {log}")
        if attempt["role"] == "generator" and not manifest["generator_session"]:
            raise WorkflowError("Generator emitted no resumable session ID")
        attempt["status"] = "completed"
        self.save(run, manifest)
        return log

    def collect_candidate(self, run, manifest):
        workspace = run / "generation"
        self.check_workspace_inputs(workspace, manifest)
        submission = workspace / "submission.json"
        if not submission.exists():
            checkpoint = workspace / "checkpoint.md"
            if not checkpoint.is_file() or not checkpoint.read_text().strip():
                raise WorkflowError("Generator returned neither a candidate nor a durable checkpoint")
            meta = read_json(workspace / "checkpoint.meta.json")
            self.check_turn_artifact(manifest, meta)
            if meta["sha256"] != sha(checkpoint.read_bytes()):
                raise WorkflowError("Checkpoint changed after persistence")
            manifest["status"] = "incomplete"
            manifest["attempts"][-1]["status"] = "consumed"
            self.save(run, manifest)
            return
        request = read_json(submission)
        self.check_turn_artifact(manifest, request)
        # research.py's submission is a local path plus exact content hash.
        if not isinstance(request.get("path"), str) or Path(request["path"]).is_absolute():
            raise WorkflowError("Submission path must be a relative workspace path")
        proof_path = inside(workspace, request["path"])
        data = proof_path.read_bytes()
        if sha(data) != request["sha256"] or not data.decode("utf-8").strip():
            raise WorkflowError("Candidate changed after submission or is empty")
        statement = (run / "inputs" / "statement.md").read_text()
        proof = data.decode("utf-8")
        mode = manifest.get("mode_policy", "fixed")
        claim = request.get("claim")
        validate_candidate(proof, statement, mode, claim)
        baseline = self.baseline(run, manifest)
        if mode == "improvement" and request.get("baseline_sha256") != digest(baseline):
            raise WorkflowError("Submission refers to a stale accepted-results baseline")
        candidate_hash = sha(data)
        if any(c["candidate_sha256"] == candidate_hash for c in manifest["candidates"]):
            raise WorkflowError("Candidate repeats an already submitted proof; revise it before resubmission")
        number = len(manifest["candidates"])
        relative = f"candidates/{number:05d}"
        dest = run / relative
        dest.mkdir(parents=True, exist_ok=True)
        atomic(dest / "candidate.md", data)
        binding = {"statement_sha256": manifest["statement_sha256"], "candidate_sha256": candidate_hash}
        if mode == "improvement":
            binding.update(mode=mode, claim=claim, baseline_sha256=digest(baseline))
            atomic(dest / "baseline.json", baseline)
        atomic(dest / "binding.json", binding)
        # Freeze cited/downloaded sources and reproducible computational evidence,
        # but do not expose private generator conversation or research memory.
        for name in ("references", "artifacts"):
            source = workspace / name
            if source.exists():
                if any(p.is_symlink() for p in source.rglob("*")) or source.is_symlink():
                    raise WorkflowError("Candidate evidence must not contain symlinks")
                if (dest / name).exists():
                    shutil.rmtree(dest / name)
                shutil.copytree(source, dest / name)
        evidence_hashes = {str(p.relative_to(dest)): sha(p.read_bytes())
                           for name in ("references", "artifacts")
                           for p in (dest / name).rglob("*") if p.is_file()}
        candidate = {**binding, "directory": relative, "status": "awaiting_review"}
        candidate["evidence_hashes"] = evidence_hashes
        manifest["candidates"].append(candidate)
        manifest["pending_candidate"] = number
        manifest["phase"] = "verification"
        manifest["attempts"][-1]["status"] = "consumed"
        self.save(run, manifest)

    def baseline(self, run, manifest):
        accepted = manifest.get("accepted_candidates", [])
        if not accepted and manifest["phase"] == "accepted":
            accepted = [manifest["pending_candidate"]]
        results = []
        for i in accepted:
            candidate = manifest["candidates"][i]
            frozen = inside(run, candidate["directory"])
            data = (frozen / "candidate.md").read_bytes()
            if sha(data) != candidate["candidate_sha256"]:
                raise WorkflowError("Accepted baseline proof changed")
            self.check_evidence(frozen, candidate)
            review = read_json(frozen / "verification.json")
            validate_review(review, candidate)
            if review["verdict"] != "correct":
                raise WorkflowError("Baseline proof lacks a correct review")
            if candidate.get("mode") == "improvement":
                if digest(read_json(frozen / "baseline.json")) != candidate["baseline_sha256"] or not validate_comparison(review["improvement_assessment"], candidate["baseline_sha256"]):
                    raise WorkflowError("Accepted baseline comparison changed")
            results.append({"candidate_id": i, "candidate_sha256": candidate["candidate_sha256"],
                            "claim": candidate.get("claim"), "proof": data.decode()})
        return {"original_question": (run / "inputs" / "statement.md").read_text(),
                "accepted_results": results}

    @staticmethod
    def check_turn_artifact(manifest, meta):
        if (not isinstance(meta, dict) or type(meta.get("attempt")) is not int
                or not isinstance(meta.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])):
            raise WorkflowError("Malformed checkpoint or submission metadata")
        if meta.get("run_id") != manifest["run_id"] or meta.get("attempt") != len(manifest["attempts"]) - 1:
            raise WorkflowError("Stale checkpoint or submission from another run/turn")

    def verify(self, run, manifest):
        candidate = manifest["candidates"][manifest["pending_candidate"]]
        frozen = inside(run, candidate["directory"])
        data = (frozen / "candidate.md").read_bytes()
        if sha(data) != candidate["candidate_sha256"]:
            raise WorkflowError("Frozen candidate changed")
        workspace = run / "verification" / f"attempt-{len(manifest['attempts']):05d}-{uuid.uuid4().hex[:8]}"
        self.prepare_workspace(run, workspace, "verification")
        self.check_evidence(frozen, candidate)
        for name in ("references", "artifacts"):
            if (frozen / name).exists():
                shutil.copytree(frozen / name, workspace / name, dirs_exist_ok=True)
        atomic(workspace / "candidate.md", data)
        binding = {key: candidate[key] for key in ("statement_sha256", "candidate_sha256")}
        improving = candidate.get("mode") == "improvement"
        if improving:
            binding.update({key: candidate[key] for key in ("mode", "claim", "baseline_sha256")})
            baseline = read_json(frozen / "baseline.json")
            if digest(baseline) != candidate["baseline_sha256"]:
                raise WorkflowError("Frozen improvement baseline changed")
            atomic(workspace / "baseline.json", baseline)
        atomic(workspace / "binding.json", binding)
        schema = workspace / "output.schema.json"
        atomic(schema, verification_schema(improving))
        prompt = ("Follow AGENTS.md and read the verification skills. Independently verify the entire "
                  "candidate.md against statement.md and references/. Read binding.json and return the "
                  "required structured verification report with those exact hashes. Do not read the "
                  "generator's conversation or other workspaces. Unresolved necessary checks are gaps.")
        if improving:
            prompt += "\n" + VERIFIER_IMPROVEMENT_INSTRUCTIONS + " Read baseline.json and binding.json."
        log = self.attempt(run, manifest, "verifier", workspace, "live", prompt, schema)
        self.collect_review(run, manifest, log)

    def collect_review(self, run, manifest, log):
        self.check_inputs(run, manifest)
        candidate = manifest["candidates"][manifest["pending_candidate"]]
        frozen = inside(run, candidate["directory"])
        data = (frozen / "candidate.md").read_bytes()
        if sha(data) != candidate["candidate_sha256"]:
            raise WorkflowError("Frozen candidate changed during review")
        self.check_evidence(frozen, candidate)
        workspace = inside(run, manifest["attempts"][-1]["workspace"])
        self.check_workspace_inputs(workspace, manifest)
        for name, expected_digest in candidate["evidence_hashes"].items():
            path = inside(workspace, name)
            if not path.is_file() or sha(path.read_bytes()) != expected_digest:
                raise WorkflowError(f"Verifier's candidate evidence changed: {name}")
        if (workspace / "candidate.md").read_bytes() != data:
            raise WorkflowError("Verifier's input candidate changed during review")
        if sha((workspace / "statement.md").read_bytes()) != manifest["statement_sha256"]:
            raise WorkflowError("Verifier's input statement changed during review")
        report = validate_review(read_json(log / "final.txt"), candidate)
        improving = candidate.get("mode") == "improvement"
        strict_gain = True
        if improving:
            if digest(read_json(frozen / "baseline.json")) != candidate["baseline_sha256"] or digest(read_json(workspace / "baseline.json")) != candidate["baseline_sha256"]:
                raise WorkflowError("Improvement baseline changed during review")
            strict_gain = validate_comparison(report["improvement_assessment"], candidate["baseline_sha256"])
        atomic(frozen / "verification.json", report)
        atomic(run / "generation" / "review.json", report)
        atomic(run / "generation" / "reviewed_candidate.md", data)
        candidate["status"] = ("accepted" if strict_gain else "not_improved") if report["verdict"] == "correct" else "rejected"
        candidate["review_log"] = str(log.relative_to(run))
        if candidate["status"] == "accepted":
            # Never use an agent-created filename as evidence of verification.
            atomic(run / "results" / "blueprint_verified.md", data)
            atomic(run / "results" / "verification.json", report)
            manifest["phase"] = "accepted"
            manifest["status"] = "accepted"
            manifest.setdefault("accepted_candidates", []).append(manifest["pending_candidate"])
            if improving:
                manifest["phase"] = "generation"
                manifest["status"] = "incomplete"
                manifest["pending_candidate"] = None
        else:
            manifest["phase"] = "generation"
            manifest["status"] = "incomplete"
            manifest["pending_candidate"] = None
        manifest["attempts"][-1]["status"] = "consumed"
        self.save(run, manifest)

    def resume(self, run_id, iterations=10, clear_pause=False, iterative_improvement=False):
        if iterations <= 0:
            raise WorkflowError("Iterations must be positive")
        run = self.path(run_id)
        with lock(run / "coordinator.lock"):
            run, manifest = self.load(run_id)
            if manifest["settings"]["codex_home"] != codex_home():
                raise WorkflowError("CODEX_HOME differs from the account home recorded for this run")
            self.check_inputs(run, manifest)
            if iterative_improvement and manifest.get("mode_policy", "fixed") != "improvement":
                if manifest["phase"] == "verification":
                    raise WorkflowError("Finish pending verification before changing research policy")
                if manifest["phase"] == "accepted":
                    self.check_accepted(run, manifest)
                    manifest.setdefault("accepted_candidates", [manifest["pending_candidate"]])
                    manifest["phase"] = "generation"
                    manifest["pending_candidate"] = None
                manifest["mode_policy"] = "improvement"
                self.save(run, manifest)
            # Refresh protocol helpers/instructions on explicit resume; retain all research artifacts.
            for name in ("research.py", "research_contract.py"):
                shutil.copy2(PACKAGE / name, run / "generation" / name)
            shutil.copy2(PACKAGE / "templates/generation.md", run / "generation/AGENTS.md")
            shutil.copytree(PACKAGE / "templates/skills", run / "generation/skills", dirs_exist_ok=True)
            if clear_pause:
                (run / "PAUSE_AFTER_TURN").unlink(missing_ok=True)
            if manifest["phase"] == "accepted":
                self.check_accepted(run, manifest)
                return manifest
            # A durable completion may have landed just before coordinator interruption.
            if manifest["attempts"] and manifest["attempts"][-1]["status"] in ("running", "completed"):
                attempt = manifest["attempts"][-1]
                process_file = inside(run, attempt["log"]) / "process.json"
                if process_file.exists() and not (process_file.parent / "completion.json").exists():
                    pid = read_json(process_file)["pid"]
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise WorkflowError(f"An interrupted coordinator left agent process {pid} alive; wait for it to exit before resuming")
                try:
                    log = self.finish_attempt(run, manifest, attempt)
                except WorkflowError:
                    # Record the interruption and recover session identity, but never accept its output.
                    if attempt["status"] not in ("failed", "interrupted"):
                        raise
                else:
                    try:
                        if attempt["role"] == "generator":
                            self.collect_candidate(run, manifest)
                        else:
                            self.collect_review(run, manifest, log)
                    except (WorkflowError, OSError, ValueError, KeyError) as exc:
                        attempt["status"] = "invalid_output"
                        manifest["status"] = "blocked"
                        manifest["error"] = str(exc)
                        self.save(run, manifest)
                        raise WorkflowError(f"Recovered turn has invalid output: {exc}") from exc
            previous_error = manifest.get("error")
            manifest["error"] = None
            remaining = iterations
            try:
                while manifest["phase"] != "accepted":
                    if (run / "PAUSE_AFTER_TURN").exists():
                        manifest["status"] = "paused"
                        break
                    if manifest["phase"] == "verification":
                        self.verify(run, manifest)
                        continue
                    if remaining <= 0:
                        manifest["status"] = "incomplete"
                        break
                    workspace = run / "generation"
                    atomic(workspace / "baseline.json", self.baseline(run, manifest))
                    # A stale submission from a prior turn must never count as a new candidate.
                    (workspace / "submission.json").unlink(missing_ok=True)
                    index = manifest["next_iteration"]
                    mode = "live" if index % 2 == 0 else "disabled"
                    prompt = ("Follow AGENTS.md and applicable skills/*.md. Read the entire statement.md, "
                              "supplied references, turn.json, checkpoint.md if present, and review.json if present. "
                              "Continue substantive research from durable memory and address every review finding. "
                              f"This turn's external search mode is {mode}. "
                              "Use research.py for memory and checkpoints. Submit only a complete candidate meeting "
                              "the recorded research policy with `python3 research.py submit --file blueprint.md`; otherwise "
                              "save a durable checkpoint. Do not invoke Codex or verify your own candidate.")
                    if manifest.get("mode_policy") == "improvement":
                        prompt += "\n" + IMPROVEMENT_INSTRUCTIONS + " Read baseline.json. Submit with --improvement-file improvement.json."
                    if previous_error:
                        prompt += "\nPrevious submission validation error: " + previous_error
                        previous_error = None
                    self.attempt(run, manifest, "generator", workspace, mode, prompt)
                    self.check_inputs(run, manifest)
                    self.collect_candidate(run, manifest)
                    remaining -= 1
            except (WorkflowError, OSError, ValueError, KeyError, KeyboardInterrupt) as exc:
                if (not isinstance(exc, KeyboardInterrupt) and manifest["attempts"]
                        and manifest["attempts"][-1]["status"] == "completed"):
                    manifest["attempts"][-1]["status"] = "invalid_output"
                manifest["status"] = "blocked"
                manifest["error"] = str(exc) or "Interrupted"
                self.save(run, manifest)
                raise WorkflowError(f"Run {run_id} blocked: {manifest['error']}") from exc
            self.save(run, manifest)
            return manifest

    def check_accepted(self, run, manifest):
        self.check_inputs(run, manifest)
        accepted = manifest.get("accepted_candidates", [])
        if not accepted and manifest["phase"] == "accepted":
            accepted = [manifest["pending_candidate"]]
        if not accepted:
            raise WorkflowError("Run has no accepted proof")
        self.baseline(run, manifest)
        candidate = manifest["candidates"][accepted[-1]]
        frozen = inside(run, candidate["directory"])
        data = (frozen / "candidate.md").read_bytes()
        if sha(data) != candidate["candidate_sha256"]:
            raise WorkflowError("Accepted candidate was changed")
        self.check_evidence(frozen, candidate)
        validate_review(read_json(frozen / "verification.json"), candidate)
        if candidate.get("mode") == "improvement":
            if not validate_comparison(read_json(frozen / "verification.json")["improvement_assessment"], candidate["baseline_sha256"]):
                raise WorkflowError("Candidate was not verified to improve the baseline")
        if read_json(frozen / "verification.json")["verdict"] != "correct":
            raise WorkflowError("Accepted candidate has no correct review")
        if (run / "results" / "blueprint_verified.md").read_bytes() != data:
            raise WorkflowError("Published proof differs from accepted candidate")
        return frozen

    @staticmethod
    def check_evidence(directory, candidate):
        actual = {str(p.relative_to(directory)): sha(p.read_bytes())
                  for name in ("references", "artifacts")
                  for p in (directory / name).rglob("*") if p.is_file()}
        if actual != candidate["evidence_hashes"]:
            raise WorkflowError("Frozen candidate evidence changed")

    def export(self, run_id, output=None):
        run = self.path(run_id)
        with lock(run / "coordinator.lock"):
            run, manifest = self.load(run_id)
            frozen = self.check_accepted(run, manifest)
            suffix = f"/{frozen.name}" if manifest.get("mode_policy") == "improvement" else ""
            target = inside(self.repo, output or f".local/sandbox-workflow/exports/{run_id}{suffix}")
            if target.exists() or target.is_relative_to(run):
                raise WorkflowError("Export destination already exists or is inside the run")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Stage then rename: incomplete exports are never mistaken for finished ones.
            temporary = Path(tempfile.mkdtemp(prefix=".export-", dir=target.parent))
            try:
                shutil.copy2(frozen / "candidate.md", temporary / "blueprint_verified.md")
                shutil.copy2(frozen / "verification.json", temporary / "verification.json")
                shutil.copy2(run / "manifest.json", temporary / "manifest.json")
                shutil.copytree(run / "inputs", temporary / "inputs")
                shutil.copytree(run / "generation", temporary / "research")
                shutil.copytree(run / "candidates", temporary / "candidates")
                os.rename(temporary, target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
            return target
