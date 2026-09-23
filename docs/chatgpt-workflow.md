<!-- Added in 2026 to document the production two-conversation ChatGPT MCP workflow. -->

# ChatGPT MCP proof workflow

This mode uses two ordinary, user-started ChatGPT conversations: a generator and a verifier. A local MCP server stores immutable problem snapshots, research, candidates, checks, reviews, and exports. It does not start ChatGPT, choose a ChatGPT model, invoke Codex, or call an inference API.

If `gpt-6-astra` is available to the account, select it manually in each conversation. The Codex settings under `agents/` do not affect ChatGPT conversations, and the MCP server cannot verify the selected model.

## 1. Install the local service

From the repository root:

```sh
python3 -m venv .venv-chatgpt
.venv-chatgpt/bin/python -m pip install -r chatgpt_workflow/requirements.txt
```

The launcher intentionally uses only this repository-local environment:

```sh
sh chatgpt_workflow/run.sh --help
```

## 2. Import a problem

Place a UTF-8 Markdown problem under `agents/generation/data/`. Its data-relative path without `.md` is its ID:

```sh
sh chatgpt_workflow/run.sh --import-problem example
sh chatgpt_workflow/run.sh --import-problem algebra/my_problem
```

Direct sibling `.refs/` files in Markdown, text, or LaTeX are snapshotted. Existing snapshots are immutable. Use a new problem ID when the statement changes.

For a statement elsewhere inside the checkout:

```sh
sh chatgpt_workflow/run.sh --create-problem my_problem --statement-file path/to/statement.txt
```

## 3. Issue separate keys

```sh
sh chatgpt_workflow/run.sh --issue-key example --role generation --label example-generator
sh chatgpt_workflow/run.sh --issue-key example --role verification --label example-verifier
```

Each command prints a credential ID and creates a mode-0600 JSON file under `.local/chatgpt-workflow/credentials/` containing the secret `access_key`. The database stores only its SHA-256 hash. Give each conversation only its own role's key. Never place a key in research artifacts, prompts shared with the other role, or ChatGPT memory.

## 4. Connect ChatGPT

This release does not bundle an MCP connector or tunnel. Configure a private ChatGPT-compatible connector to launch the absolute equivalent of:

```text
/bin/sh /absolute/path/to/Rethlas/chatgpt_workflow/run.sh
```

For a connector that expects HTTP, start:

```sh
sh chatgpt_workflow/run.sh --transport streamable-http --port 8766
```

Point the connector at `http://127.0.0.1:8766/mcp`. The service binds only to loopback; do not expose it directly to the public Internet. After code or schema changes, restart this service and refresh the connector's discovered tools.

## 5. Start a generation run

List keys and open a run locally with the generator credential ID:

```sh
sh chatgpt_workflow/run.sh --list-keys
sh chatgpt_workflow/run.sh --open-run credential-REPLACE_WITH_GENERATOR_ID
```

Then send the generation conversation a prompt like:

```text
You are the Rethlas generation conversation for problem_id="example". Use my generation access_key and active run_id with every MCP call. First call get_run, then get_workflow with role="generation", and follow the returned instructions. Preserve research and failed paths. Submit a complete candidate when ready. Save and read back a final checkpoint, then call finish_run as the last tool call before responding. Do not perform the verifier role.
```

Intermediate checkpoints do not finish a run. `finish_run` closes the authorization boundary and makes every later call with that run ID fail.

## 6. Hand off to the verifier

After generation finishes, open a new run with the verifier credential ID. Give the separate verifier conversation its own secret key, new run ID, and returned candidate ID:

```text
You are the Rethlas verification conversation. Use my verification access_key and active run_id with every MCP call. First call get_run, then get_workflow with role="verification", and follow it. Review candidate-REPLACE_WITH_ID against the immutable original statement and references. Persist every item check, error, gap, and citation result. Submit the review; if accepted, export it. Call finish_run with the verification_id as the last tool call before responding.
```

Rejected candidates remain immutable. Open a later generation run, read the completed review, and submit a revised candidate with the previous candidate as parent.

## Enforced behavior

- Every protected tool call requires a valid problem/role `access_key` and matching active `run_id`.
- Only one run may be active for a problem at a time; operations are serialized across server processes.
- Generation can write research and candidates; verification can write checks and reviews.
- Candidates, review checks, findings, and source snapshots are immutable and retry-safe.
- Review completion requires full proof-item coverage; any stored error, gap, wrong citation, or unresolved citation yields `wrong`.
- Accepted export binds the exact statement, candidate, review, and hashes and refuses to overwrite different output.

The server enforces protocol consistency, not mathematical truth. An accepted proof is LLM-reviewed, not formally certified by Lean, Coq, or a proof kernel.

## State and results

Private state lives under `.local/chatgpt-workflow/` and is ignored by Git. Accepted output is written under `agents/generation/results/<problem_id>/` as:

- `blueprint_verified.md`
- `verification.json`
- `chatgpt_manifest.json`

Status and a safe local export are available with:

```sh
sh chatgpt_workflow/run.sh --status example
sh chatgpt_workflow/run.sh --export example
```

See [run authorization and research storage](chatgpt-runs.md) for cancellation, alternating search policy, files, papers, and readable research exports.

## Iterative improvement

Import or create an open problem with the option enabled:

```sh
sh chatgpt_workflow/run.sh --import-problem my_bound --iterative-improvement
# Or: --create-problem my_bound --statement-file path/to/question.md --iterative-improvement
```

Fixed-statement mode remains the default. The problem's mode and original snapshot are
immutable; use a new ID to change either. Existing databases gain the additional research
metadata tables automatically, and old problems remain fixed-mode.

In improvement mode, `get_problem_context` supplies the baseline and its hash. The final
main statement must equal `improvement.statement`; the generator additionally submits an
explicit `improvement.improvement` comparison and `baseline_sha256`. The verifier checks
every proof item as before and must also supply `improvement_assessment`, containing the
baseline hash, a verdict (`strict_improvement`, `not_improvement`, or `unresolved`), and
an explanation addressing domains, hypotheses, and strict gain. Only correct proofs with
strict improvement are promoted.

Acceptance sets state `IMPROVING` rather than terminal `ACCEPTED`. After the verifier
finishes its authorized run, open another generation run to seek the next improvement;
continue alternating the existing two conversations and run authorizations. The server
does not automatically launch ChatGPT conversations. The next candidate's parent is always
`latest_candidate`, even after an acceptance.

Accepted rounds remain exportable after later rejections or while another candidate is
pending. Exports use `results/<problem_id>/improvements/<candidate_id>/`. The `export_accepted`
tool accepts an optional accepted `candidate_id`; the CLI `--export` selects the latest
accepted result. Exhausted research is not a proof of optimality.
