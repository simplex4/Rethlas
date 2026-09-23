<!-- Modified in 2026 for ChatGPT MCP integration, GPT-6 Astra defaults, resumable Codex execution, and sandboxed Edu mode. -->

# Rethlas

Rethlas is a natural-language mathematics research workflow with three modes:

- **Codex mode** runs a proof-generation agent and a separate verification service locally.
- **Sandboxed Codex mode** uses a ChatGPT-authenticated Codex CLI with workspace-limited shell commands and files, without MCP or full-access permissions.
- **ChatGPT MCP mode** uses two user-started ChatGPT conversations, one for generation and one for verification, with durable local state shared through MCP.

All three modes preserve the original informal-proof output format. ChatGPT MCP mode does not call an inference API or launch ChatGPT by itself.

## Requirements

### Codex mode

- Codex CLI
- Python 3.11 or newer
- the Python packages listed under `agents/generation/mcp/` and `agents/verification/`
- `pdftotext` only when using PDF references
- Zola only when using the optional result website

### ChatGPT MCP mode

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

The runner alternates search-disabled and search-enabled continuation turns, writes append-only iteration logs, and stops when `results/<problem_id>/blueprint_verified.md` exists. It supports dry-run validation, pausing, and automatic continuation from existing logs. See [the Codex workflow guide](docs/codex-workflow.md).

## Sandboxed Codex mode (managed Edu accounts)

This mode uses Python 3.11+ and the installed Codex CLI, without third-party Python packages. It preserves managed policy and uses `workspace-write` with `on-request` approvals.

```sh
export CODEX_HOME="$HOME/.codex"
python3 -m sandbox_workflow doctor --live
python3 -m sandbox_workflow run --problem agents/generation/data/example.md
```

Each run prints its ID. Use `status`, `pause`, `resume`, and `export` with `--run-id` to manage it. It retains the GPT-6 Astra / max defaults, independent verification, local research memory, and search alternation. Runs are isolated from existing mode state. See [setup, capability checks, and recovery](docs/sandboxed-codex-workflow.md). Live account compatibility must be checked in the terminal that will run the workflow.

## ChatGPT MCP mode

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

See [the complete ChatGPT workflow](docs/chatgpt-workflow.md) and [run authorization and research storage](docs/chatgpt-runs.md).

## Repository layout

- `agents/generation/`: original Codex generation agent, skills, MCP memory server, example, runner, and site renderer
- `agents/verification/`: original Codex verification agent and HTTP/MCP services
- `sandbox_workflow/`: sandboxed Codex coordinator, local research commands, adapted skills, and tests
- `chatgpt_workflow/`: production two-conversation MCP state and authorization service
- `docs/`: setup, handoff, pause, resume, security, and export guidance

## Output and rendering

The original Codex and ChatGPT MCP modes export accepted proofs under `agents/generation/results/<problem_id>/`. Sandboxed mode keeps its own results and can explicitly export to a new directory there. To render results with Zola:

```sh
cd agents/generation
./site/serve.sh
```

The first run downloads the MATbook theme. Open `http://localhost:3264` after Zola starts.

## License

This distribution is licensed under Apache License 2.0. See `LICENSE` and `NOTICE`. Existing upstream files changed by this release carry a prominent modification notice.
