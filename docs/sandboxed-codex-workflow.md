<!-- Added in 2026 for an MCP-free, sandboxed Codex workflow. -->

# Sandboxed Codex workflow

This third Rethlas mode uses a ChatGPT-authenticated Codex CLI, ordinary sandboxed shell commands, and durable local files. It does not require MCP, an inference API key, or the legacy HTTP verifier. A Python coordinator starts separate generator and verifier processes; only it publishes an accepted proof. Acceptance means independent mathematical model review, not formal proof certification.

## Requirements and account

Use Python 3.11 or newer and an installed, authenticated Codex CLI. No Python packages need installing. `pdftotext` is optional until a PDF reference needs extraction. The program never installs dependencies. The implementation targets the CLI's `exec`, `exec resume`, `--json`, and `--output-schema` interfaces (developed against 0.153.2).

Run from the repository root:

```sh
export CODEX_HOME="$HOME/.codex"
python3 -m sandbox_workflow doctor
python3 -m sandbox_workflow doctor --live
```

The first command checks installed tools and authentication. `--live` makes two small model calls to check shell execution, workspace file creation/readback, theorem retrieval, structured output, and session continuation. It retains commands and outputs under `.local/sandbox-workflow/probes/`. Inspect `core_passed` and `retrieval_passed` separately: a working shell does not establish network access. Overall `passed` requires both; a failed retrieval check returns exit code 1 while retaining the successful core checks. This probe does not test native delegation or prove mathematical correctness.

The mode defaults to `$HOME/.codex`, ignoring `CODEX_CLI_HOME` and the legacy runner's personal-account fallback. Explicit `CODEX_HOME` is supported and recorded; resume rejects a different resolved home. Do not change the login stored in that home between turns: path consistency is checked, not account identity from credentials. API-key environment variables are removed from child invocations. `doctor` reports the current authentication method without reading credentials.

Every invocation explicitly requests `workspace-write` and `approval_policy="on-request"`, including resumed turns. CLI 0.153.2 may still emit an exec-default `Never` warning before managed policy restores `OnRequest`; the workflow does not relax that requirement. Existing configured MCP servers are disabled for the invocation. Managed requirements and execution rules remain in force. The workflow never retries a denied operation in an unrestricted process. Noninteractive approval requests may block a run; preserve the logs and resolve permitted configuration in your terminal rather than changing to full access.

## Start, pause, and resume

```sh
python3 -m sandbox_workflow run \
  --problem agents/generation/data/example.md --dry-run

python3 -m sandbox_workflow run \
  --problem agents/generation/data/example.md --iterations 10
```

A new run snapshots the original statement and adjacent `example.refs/` directory and immediately prints its run ID. Existing research is not imported. Run state is under `.local/sandbox-workflow/runs/<run-id>/`; generator and verification workspaces have their own local instructions and skill documents.

```sh
python3 -m sandbox_workflow status --run-id RUN_ID
python3 -m sandbox_workflow pause --run-id RUN_ID
python3 -m sandbox_workflow resume --run-id RUN_ID --clear-pause --iterations 10
```

Pause waits for the active generator or verifier turn to finish. `resume` without `--clear-pause` respects an existing marker. Each launch permits ten **additional generator turns** by default. Pending verification can finish without consuming another generator turn. Budget exhaustion leaves an incomplete run, never an accepted one. Exit codes are 0 for accepted/paused runs, 2 for exhausted research budgets, and 1 for errors or blocked execution.

Only the exact recorded generator session is resumed; verifier sessions are fresh per review attempt. No legacy logs or `--last` session selection are used. Commands, JSON events, final responses, errors, and completion records remain in append-only attempt directories. A coordinator lock prevents concurrent execution of the same run. Interrupted model output is not accepted. If an orphaned agent is still running, resume reports its PID and waits for you to resolve that process rather than starting a concurrent writer.

Generator model settings for **new runs** use `MODEL` and `REASONING_EFFORT`; verifier settings use `CODEX_MODEL` and `CODEX_REASONING_EFFORT`. Both default to `gpt-6-astra` and `max`. `CODEX_BIN` selects the CLI executable. Resume uses the settings recorded when the run was created. Native subgoal agents use the generator's model and effort, with the original runtime limits of 10 threads and depth 3 where permitted. Unavailable delegation falls back to sequential research. The implementation assistant's temporary root `AGENTS.md` is not a runtime setting; remove that temporary file before research runs if it contains implementation-only instructions.

## Research and verification

Initial generator research permits external search. Search-enabled turns request cached built-in web search, which this Edu policy allows; direct theorem/PDF retrieval remains subject to network policy. Subsequent generator turns alternate independent reasoning and search-enabled work. Built-in web search is disabled on independent turns; local retrieval commands also reject calls unless `turn.json` permits live search. This is workflow enforcement, not a new operating-system network isolation boundary: agents must also obey the instruction not to use other network commands in independent turns.

Agents use their local `research.py` for append-only memory, token-ranked memory search, branch records, checkpoints, complete candidate submission, theorem search, bounded HTTPS downloads, and PDF extraction. These commands run inside the agent sandbox. Missing sources and failed retrieval are recorded as limitations, not evidence that a mathematical result does not exist. Source downloads and exact computations should preserve provenance. Place proof-essential reproducible computation scripts, inputs, outputs, and library/version notes in `artifacts/` so they accompany a candidate.

The generator must reproduce the entire original statement in the final theorem's `## statement` section. A submitted proof is frozen with its hash, references, and computational artifacts. A fresh verifier receives those files and the original problem, not the generator's conversation or private research memory. It checks every deduction and source application, independently replays proof-essential computations when feasible, and returns all errors and gaps. Rejection returns the full report for revision. Essential checks blocked by missing references remain gaps.

The coordinator validates output structure, strict verdict consistency, original statement and candidate hashes, and successful process completion. Stale submissions, missing reports, altered inputs, or contradictory verdicts cannot publish a proof. Agents are instructed not to alter coordinator state. The directories and hashes detect accidental cross-run/stale output; they are not an adversarial security boundary against a process with access to the same user account.

## Export and validation

```sh
python3 -m sandbox_workflow export --run-id RUN_ID
# Optional: choose a NEW result directory for the existing site renderer:
python3 -m sandbox_workflow export --run-id RUN_ID \
  --output agents/generation/results/sandbox-example
```

Export requires an accepted proof and refuses existing destinations. It includes `blueprint_verified.md`, `verification.json`, the manifest, original inputs, candidates/reviews, and research files. Default exports remain under `.local/sandbox-workflow/exports/`. Neither existing mode's results are overwritten.

Run offline acceptance tests:

```sh
python3 -m unittest discover -s sandbox_workflow/tests -v
```

They simulate Codex events and cover revision/acceptance, malformed output, hash mismatches, stale checkpoints, account settings, pause, recovery, retrieval restrictions, and export collisions. They do not substitute for `doctor --live` or a real small-theorem run under your account. A release claiming full account compatibility additionally needs successful live generator/verifier execution, a deliberately invalid candidate rejection, pause/resume, and optional native-delegation checks.


## Observed validation (2026-09-21)

On this checkout with Codex CLI 0.153.2, Python 3.14.7, and the Edu login at `$HOME/.codex`:

- All 24 offline tests passed; existing Codex and ChatGPT runtime files remained unchanged.
- Live shell execution, fresh and resumed workspace writes, structured output, and same-session continuation passed.
- The included finite-group example completed generation, a cooperative pause, resumed independent verification, acceptance, and an export whose hash matches the frozen candidate.
- Native subgoal work was observed during generation. Maximum-depth recursive delegation was not separately exercised.
- A separate live verifier rejected a deliberately invalid proof and published no verified file.
- The initial theorem retrieval probe returned `403 Forbidden` followed by an approval-request failure. The user subsequently resolved this with `-c 'approvals_reviewer="auto_review"'`; this was not a ChatGPT Edu account restriction. Fresh and resumed sandbox commands include that setting. The initial doctor result remains historical evidence, not a current diagnosis.
- Managed requirements permit cached built-in search but prohibit live built-in search. Source-dependent research can still be blocked; supplied references and self-contained reasoning remain usable.

Local, ignored evidence is recorded in `.local/sandbox-workflow/acceptance/summary.json`, with the corresponding run and probe directories. These observations do not establish unrestricted network access or formal mathematical certification.

## Fixed theorems and iterative improvement

Fixed-statement mode remains the default: the final theorem must contain the original
complete statement, with no added or removed hypotheses. Submission preflight reports
format errors before verification; non-mathematical appendices after the theorem are allowed.

For an open problem, enable iterative improvement explicitly:

```sh
CODEX_HOME="$HOME/.codex" python3 -m sandbox_workflow run \
  --problem agents/generation/data/my_bound.md --iterative-improvement --iterations 10
```

The generator writes a precise new theorem and an `improvement.json` object containing
`statement` and `improvement` (the comparison argument). The original question stays
unchanged. Each fresh verifier receives the candidate and a frozen baseline containing
the original question and every accepted result. Promotion requires a correct proof and
an independent `strict_improvement` assessment, with matching hashes. Equivalent bounds,
unresolved comparisons, and incorrect proofs are not promoted.

After acceptance, the same generator session resumes to seek the next improvement.
`--iterations` bounds additional generation turns; pause/resume works between turns.
Exhausting the budget leaves status `incomplete`, even if useful results were accepted;
it is not a claim of optimality. The CLI prints elapsed time every 30 seconds and total
invocation time on exit to stderr, keeping stdout JSON-readable.

`export` remains available while improvement research is incomplete or paused. It exports
the latest accepted proof plus the full candidate archive to
`.local/sandbox-workflow/exports/RUN_ID/CANDIDATE_INDEX/`. Every accepted round remains in
the run's `candidates/`; the run-local `results/` points to the latest accepted round.
A rejected later attempt never replaces an accepted result.

An existing run can explicitly opt in with:

```sh
CODEX_HOME="$HOME/.codex" python3 -m sandbox_workflow resume \
  --run-id RUN_ID --iterative-improvement --clear-pause --iterations 10
```

Finish any pending verification before switching modes. Ordinary `resume` preserves the
recorded mode. This upgrade refreshes protocol helpers and instructions while preserving
research, checkpoints, logs, candidates, and accepted results.

## Iterative-improvement validation

The feature's offline checks cover multiple accepted rounds, non-improvements, incorrect
proofs, missing or stale comparisons, changed baselines, pause/resume, retained exports,
fixed-statement enforcement, submission preflight, and timer cleanup. The original shell
runner and MCP-to-HTTP verification path are exercised with simulated model responses.
ChatGPT store tests also cover authorization and retry behavior.

```sh
python3 -m unittest discover -s sandbox_workflow/tests -q
# Use existing environments with the appropriate project dependencies:
python3 -m unittest discover -s chatgpt_workflow/tests -q
python3 -m unittest discover -s agents/verification/tests -q
```

On 2026-09-23, 53 offline tests passed. No live mathematical improvement run was started.
ChatGPT's MCP transport was not tested: the available agent environments contained MCP 2.x,
while `chatgpt_workflow/requirements.txt` pins MCP 1.29.1, and the dedicated ChatGPT environment
was absent. Its database and authorization tests ran with the existing verification Python.
No dependencies were installed or changed.
