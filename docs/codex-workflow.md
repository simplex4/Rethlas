<!-- Added in 2026 to document the original Codex workflow and new runner controls. -->

# Codex generation and verification workflow

This is the original two-agent Rethlas path. The verifier runs as a local HTTP service; the generator runs through Codex and calls the verifier through MCP.

## Install and start

Install the verifier in its own environment:

```sh
cd agents/verification
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn api.server:app --host 127.0.0.1 --port 8091
```

Install the generator MCP requirements in a separate terminal:

```sh
cd agents/generation
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r mcp/requirements.txt
```

The generator, verifier, and subgoal prover default to [`gpt-6-astra`](https://developers.openai.com/api/docs/models/gpt-6-astra) with `max` reasoning. Official OpenAI documentation lists `max` as a supported reasoning effort. Override generator settings with `MODEL` and `REASONING_EFFORT`; override verifier settings with `CODEX_MODEL` and `CODEX_REASONING_EFFORT` before starting the relevant process.

## Run the example or your own problem

From `agents/generation/`:

```sh
./tests/run_example.sh
PROBLEM_FILE=data/my_problem.md ./tests/run_example.sh
```

Problem paths must be relative Markdown paths under `data/`. A problem at `data/algebra/p1.md` has problem ID `algebra/p1`. Optional references go in `data/algebra/p1.refs/`. Text, Markdown, and LaTeX can be read directly; PDFs require `pdftotext` and are extracted to the ignored `.extracted/` directory.

The runner writes:

- iterations to `logs/<problem_id>/iter/`
- durable memory to `memory/<problem_id>/`
- drafts and accepted output to `results/<problem_id>/`

It never overwrites an existing iteration log.

## Dry run

Validate paths, settings, prior logs, recovered session ID, next iteration, and pause/stop locations without starting Codex or contacting the verifier:

```sh
DRY_RUN=1 PROBLEM_FILE=data/example.md ./tests/run_example.sh
```

`DRY_RUN` must be `0` or `1`.

## Resume

Run the same command again. The runner scans existing iteration logs, finds the next unused iteration number, recovers the Codex session ID, and resumes that session. `MAX_ITERATIONS` is the number of additional iterations for this invocation.

```sh
MAX_ITERATIONS=4 PROBLEM_FILE=data/example.md ./tests/run_example.sh
```

If logs contain no recoverable session ID or conflicting IDs, the run fails closed. Supply the intended session explicitly only when you have checked it:

```sh
SESSION_ID=REPLACE_WITH_SESSION_ID PROBLEM_FILE=data/example.md ./tests/run_example.sh
```

Use `LOG_DIR` only when deliberately selecting a different log history. `CODEX_CLI_HOME` selects the Codex home used by the wrapper; it otherwise uses an existing `CODEX_HOME`, then `$HOME/.codex-cli`.

## Pause after the active iteration

While the runner is active, create its pause marker from another terminal:

```sh
mkdir -p agents/generation/results/example
touch agents/generation/results/example/PAUSE_AFTER_ITERATION
```

The current Codex invocation is allowed to finish, then the loop stops before another iteration. Remove the marker before resuming:

```sh
rm agents/generation/results/example/PAUSE_AFTER_ITERATION
```

Set `PAUSE_FILE` to use a different marker. A marker already present at startup is treated as an error so a stale pause cannot silently look like a successful run.

## Search schedule and completion

After the initial turn, odd-numbered iterations disable web and arXiv search; even-numbered iterations allow search. Resumed runs retain this iteration-number schedule. The runner exits successfully when `blueprint_verified.md` exists, or when a requested pause is observed. Exhausting the added iteration budget without a verified proof exits nonzero.

The wrapper invokes Codex with approval and sandbox bypass. Run it only in a checkout and environment you trust, and review the agent instructions and MCP configuration first.

## Iterative improvement

The default remains a fixed theorem: the complete original statement is immutable.
For an open question, set `ITERATIVE_IMPROVEMENT=1` on both the initial invocation and
later continuations, using the same runner options and account as usual:

```sh
ITERATIVE_IMPROVEMENT=1 PROBLEM_FILE=data/my_bound.md MAX_ITERATIONS=10 \
  bash agents/generation/tests/run_example.sh
```

The runner snapshots the original question and mode in
`results/<problem_id>/.research/policy.json`. The generator proposes a precise new theorem
and an explicit comparison. The independent HTTP verifier checks both proof correctness
and strict improvement over the original known results and all accepted rounds. The MCP
service stores a bound review receipt. Only a proof matching an accepting receipt can be
archived as verified.

After each accepted round, the runner archives the proof, comparison context, and review
under `results/<problem_id>/improvements/<proof_sha256>/`, removes the transient completion
marker, and resumes generation. Rejections preserve earlier results. The existing iteration
budget, append-only logs, elapsed timer and pause marker still apply. Budget exhaustion is
reported as incomplete (exit 1), not as optimality or absence of progress.

The HTTP verification service must be restarted to load the new protocol before new runs.
Dry-run output includes the improvement setting. Existing policies cannot silently change
mode or question: use a new problem ID for such a change. Old verified files without a bound
review receipt are preserved and cannot automatically be imported as verified improvements.
