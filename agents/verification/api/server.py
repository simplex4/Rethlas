from __future__ import annotations

# Modified in 2026 to use GPT-6 Astra and independently verify runner-bound
# fixed statements and iterative improvements.

import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from sandbox_workflow.core import verification_schema, validate_review, atomic, WorkflowError
from sandbox_workflow.legacy import binding
from sandbox_workflow.research_contract import validate_candidate, digest, VERIFIER_IMPROVEMENT_INSTRUCTIONS


REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = REPO_ROOT.resolve()
RESULTS_ROOT = WORK_DIR / "results"

CODEX_BIN = os.getenv("CODEX_BIN", "codex")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-6-astra")
CODEX_REASONING_EFFORT = os.getenv("CODEX_REASONING_EFFORT", "max")
CODEX_TIMEOUT_SECONDS = int(os.getenv("CODEX_TIMEOUT_SECONDS", "0")) or None
VERIFICATION_FILENAMES = ("verification.json", "verificationt.json")


class VerifyRequest(BaseModel):
    statement: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)
    research_context: Optional[Dict[str, Any]] = None


def run_context_verification(run_id, proof, context):
    """New bound protocol; legacy requests without a context keep their old API."""
    mode = context['mode']
    validate_candidate(proof, context['original_question'], mode, context.get('claim'))
    if digest(context['baseline']) != context['baseline_sha256']:
        raise ValueError('Invalid frozen baseline hash')
    directory = _results_dir(run_id)
    directory.mkdir(parents=True, exist_ok=False)
    expected = binding(proof, context)
    schema = directory / 'research.schema.json'
    output = directory / 'research_verification.json'
    atomic(schema, verification_schema(mode == 'improvement'))
    atomic(directory / 'research_context.json', context)
    atomic(directory / 'candidate.md', proof.encode())
    atomic(directory / 'binding.json', expected)
    prompt = (f'Research context verification for run {run_id}. Read results/{run_id}/research_context.json, '
              f'candidate.md and binding.json in that same directory. Follow the mathematical checking '
              'method in AGENTS.md. For this request return the final structured JSON matching the provided '
              'research schema; do not use write_verification_output, whose older schema is different. '
              'statement_sha256 binds the immutable original question, candidate_sha256 binds this proof. '
              'The proof must establish its exact final theorem; never modify the original question. ')
    if mode == 'improvement':
        prompt += VERIFIER_IMPROVEMENT_INSTRUCTIONS
    cmd = [CODEX_BIN, 'exec', '-C', str(WORK_DIR), '-m', CODEX_MODEL,
           '--config', f'model_reasoning_effort={CODEX_REASONING_EFFORT}',
           '--dangerously-bypass-approvals-and-sandbox', '--output-schema', str(schema),
           '--output-last-message', str(output), prompt]
    with _log_path(run_id).open('w') as log:
        log.write('command: '+shlex.join(cmd)+'\n')
        log.flush()
        completed = subprocess.run(cmd, cwd=WORK_DIR, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=CODEX_TIMEOUT_SECONDS, check=False)
    if completed.returncode:
        raise ValueError(f'Verifier exited {completed.returncode}; see {_log_path(run_id)}')
    report = json.loads(output.read_text())
    validate_review(report, expected)
    return report


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _statement_hash(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]


def generate_run_id(statement: str) -> str:
    return f"{_utc_timestamp()}_{_statement_hash(statement)}"


def _allocate_run_id(statement: str) -> str:
    base = generate_run_id(statement)
    run_id = base
    suffix = 1
    while (RESULTS_ROOT / run_id).exists():
        suffix += 1
        run_id = f"{base}_{suffix}"
    return run_id


def _results_dir(run_id: str) -> Path:
    return RESULTS_ROOT / run_id


def _log_path(run_id: str) -> Path:
    return _results_dir(run_id) / "log.md"


def _verification_path(run_id: str) -> Optional[Path]:
    for filename in VERIFICATION_FILENAMES:
        path = _results_dir(run_id) / filename
        if path.exists():
            return path
    return None


def build_prompt(run_id: str, statement: str, proof: str) -> str:
    return (
        f"Run_id: {run_id}. "
        f"Statement: {statement}. "
        f"Proof:\n{proof}\n\n"
        "Use AGENTS.md to verify the above proof for the statement."
    )


def build_codex_command(run_id: str, statement: str, proof: str) -> List[str]:
    return [
        CODEX_BIN,
        "exec",
        "-C",
        str(WORK_DIR),
        "-m",
        CODEX_MODEL,
        "--config",
        f"model_reasoning_effort={CODEX_REASONING_EFFORT}",
        "--dangerously-bypass-approvals-and-sandbox",
        build_prompt(run_id=run_id, statement=statement, proof=proof),
    ]


def run_codex_verification(run_id: str, statement: str, proof: str) -> Dict[str, Any]:
    results_dir = _results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = _log_path(run_id)
    cmd = build_codex_command(run_id=run_id, statement=statement, proof=proof)

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            log_handle.write(f"started_at_utc: {started_at}\n")
            log_handle.write(f"command: {shlex.join(cmd)}\n\n")
            log_handle.flush()

            completed = subprocess.run(
                cmd,
                cwd=WORK_DIR,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=CODEX_TIMEOUT_SECONDS,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail=f"codex exec timed out after {exc.timeout} seconds. See log at {log_path}",
        ) from exc

    verification_path = _verification_path(run_id)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=(
                f"codex exec failed with exit code {completed.returncode}. "
                f"See log at {log_path}"
            ),
        )

    if verification_path is None:
        expected_primary = _results_dir(run_id) / VERIFICATION_FILENAMES[0]
        raise HTTPException(
            status_code=500,
            detail=(
                f"verification output was not found at {expected_primary}. "
                f"See log at {log_path}"
            ),
        )

    try:
        payload = json.loads(verification_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} must be a JSON object",
        )

    return payload


app = FastAPI(title="Verification Agent API", version="0.1.0")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/verify")
def verify(request: VerifyRequest) -> Dict[str, Any]:
    run_id = _allocate_run_id(request.statement)
    if request.research_context is not None:
        try:
            if request.statement != request.research_context['original_question']:
                raise ValueError('Original question binding mismatch')
            return run_context_verification(run_id, request.proof, request.research_context)
        except (ValueError, OSError, KeyError, WorkflowError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run_codex_verification(
        run_id=run_id,
        statement=request.statement,
        proof=request.proof,
    )
