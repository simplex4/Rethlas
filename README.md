<!-- Modified in 2026 for ChatGPT integration, GPT-6 Astra defaults, resumable Codex execution, sandboxed mode, iterative improvement, and release documentation. -->

# Rethlas

Rethlas is a natural-language mathematics research workflow with three modes:

- **Codex mode** runs a proof-generation agent and a separate verification service locally.
- **Sandboxed Codex mode** uses a ChatGPT-authenticated Codex CLI with workspace-limited shell commands and files, without MCP or full-access permissions.
- **ChatGPT mode** uses two user-started ChatGPT conversations, one for generation and one for verification, with durable local state shared through MCP.

All three modes preserve the original informal-proof output format. ChatGPT mode does not call an inference API or launch ChatGPT by itself.

## Requirements

### Codex mode

- Codex CLI
- Python 3.11 or newer
- the Python packages listed under `agents/generation/mcp/` and `agents/verification/`
- `pdftotext` only when using PDF references
- Zola only when using the optional result website

### Sandboxed Codex mode

- Codex CLI, authenticated in the account that will own the run
- Python 3.11 or newer; no third-party Python packages are required
- a managed policy that permits `workspace-write`, `approval_policy="on-request"`,
  and `approvals_reviewer="auto_review"`; automatic review routes eligible
  approval requests but does not widen the sandbox or grant network access
- `pdftotext` only when supplied references include PDFs

### ChatGPT mode

- Python 3.11 or newer
- the packages in `chatgpt_workflow/requirements.txt`
- a ChatGPT-compatible MCP connector or private tunnel for connecting ChatGPT to the local server
- `pdftotext` only for PDF text extraction

The repository does not bundle Codex, Python environments, Zola, `pdftotext`, ChatGPT, or an MCP tunnel.

## Codex mode

The generator, verifier, and subgoal prover default to [`gpt-6-astra`](https://developers.openai.com/api/docs/models/gpt-6-astra) with `max` reasoning. Generator overrides are `MODEL` and `REASONING_EFFORT`; verifier overrides are `CODEX_MODEL` and `CODEX_REASONING_EFFORT`.

Start the verifier:

```sh
cd agents/verification
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn api.server:app --host 127.0.0.1 --port 8091
```

In another terminal, run the included example:

```sh
cd agents/generation
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r mcp/requirements.txt
./tests/run_example.sh
```

The runner alternates search-disabled and search-enabled continuation turns, writes append-only iteration logs, and, by default, stops when `results/<problem_id>/blueprint_verified.md` exists. It supports dry-run validation, pausing, and automatic continuation from existing logs. See [the Codex workflow guide](docs/codex-workflow.md).

## Sandboxed Codex mode (managed accounts)

This mode uses Python 3.11+ and the installed Codex CLI, without third-party Python packages. It preserves managed policy and uses `workspace-write` with `on-request` approvals.

```sh
export CODEX_HOME="$HOME/.codex"
python3 -m sandbox_workflow doctor --live
python3 -m sandbox_workflow run --problem agents/generation/data/example.md
```

Each run prints its settings and ID, reports generator and verifier turn boundaries, and ends with human-readable next steps. Use `status`, `pause`, `resume`, and `export` with `--run-id` to manage it. It retains the GPT-6 Astra / max defaults, independent verification, local research memory, and search alternation. Runs are isolated from existing mode state. See [setup, capability checks, and recovery](docs/sandboxed-codex-workflow.md). Live account compatibility must be checked in the terminal that will run the workflow.

## ChatGPT mode

> **Reliability warning:** ChatGPT mode may be unreliable. Tests observed safety
> blocks, missing write tools, inaccurate final responses, and new tool calls
> continuing after ChatGPT displayed a response saying it had stopped. Do not use
> the response alone to decide whether to retry or start another run. Check local
> state with `sh chatgpt_workflow/run.sh --run-status PROBLEM_ID`; see
> [status checks and recovery](docs/chatgpt-workflow.md#reliability-and-recovery).

Create its isolated environment at the repository root:

```sh
python3 -m venv .venv-chatgpt
.venv-chatgpt/bin/python -m pip install -r chatgpt_workflow/requirements.txt
```

Import the included example and issue separate role keys:

```sh
sh chatgpt_workflow/run.sh --import-problem example
sh chatgpt_workflow/run.sh --issue-key example --role generation --label example-generator
sh chatgpt_workflow/run.sh --issue-key example --role verification --label example-verifier
```

Open a run locally with a printed credential ID, then give the returned `run_id` and that role's secret `access_key` only to its intended ChatGPT conversation:

```sh
sh chatgpt_workflow/run.sh --open-run credential-REPLACE_WITH_ID
```

Start the MCP server over stdio:

```sh
sh chatgpt_workflow/run.sh
```

For a connector that expects local HTTP instead:

```sh
sh chatgpt_workflow/run.sh --transport streamable-http --port 8766
```

The HTTP server binds to `127.0.0.1`. Keep any tunnel private and access-controlled. Every workflow call requires both the role/problem key and an active, credential-bound run ID. Finished and cancelled runs reject later calls.

See [the complete ChatGPT workflow](docs/chatgpt-workflow.md) for setup, run authorization, research storage, and recovery.

## Repository layout

- `agents/generation/`: original Codex generation agent, skills, MCP memory server, example, runner, and site renderer
- `agents/verification/`: original Codex verification agent and HTTP/MCP services
- `sandbox_workflow/`: sandboxed Codex coordinator, local research commands, adapted skills, and tests
- `chatgpt_workflow/`: production two-conversation MCP state and authorization service
- `docs/`: setup, handoff, pause, resume, security, and export guidance

## Output and rendering

The original Codex and ChatGPT modes export accepted proofs under `agents/generation/results/<problem_id>/`. Sandboxed mode keeps its own results and can explicitly export to a new directory there. To render results with Zola:

```sh
cd agents/generation
./site/serve.sh
```

The first run downloads the MATbook theme. Open `http://localhost:3264` after Zola starts.

## Open questions and improving bounds

All three modes offer opt-in iterative improvement. Fixed-theorem mode remains the default
and preserves the original complete statement. In improvement mode, the generator states
a precise new result and explains its gain; the verifier must confirm both the proof and
a strict improvement over the original known results and all accepted rounds.

- Sandboxed Codex: add `--iterative-improvement` to `run` (or explicitly upgrade with `resume`).
- Original Codex: set `ITERATIVE_IMPROVEMENT=1` on the generation runner.
- ChatGPT: add `--iterative-improvement` when importing or creating the problem; open the
  next authorized generation run after each accepted review.

Codex runners continue generation within their iteration budgets after acceptance. All
accepted rounds are preserved. Pausing or exhausting a budget does not imply optimality.
Sandboxed Codex now prints elapsed time every 30 seconds and total time on exit. See the
mode-specific guides above for commands, result locations and recovery.

## License

This distribution is licensed under Apache License 2.0. See `LICENSE` and `NOTICE`. Existing upstream files changed by this release carry a prominent modification notice.
