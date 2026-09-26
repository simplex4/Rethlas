<!-- Added in 2026 to document the production two-conversation ChatGPT MCP workflow. -->

# ChatGPT mode

This mode uses two ordinary, user-started ChatGPT conversations: a generator and a verifier. A local MCP server stores immutable problem snapshots, research, candidates, checks, reviews, and exports. It does not start ChatGPT, choose a ChatGPT model, invoke Codex, or call an inference API.

If `gpt-6-astra` is available to the account, select it manually in each conversation. The Codex settings under `agents/` do not affect ChatGPT conversations, and the MCP server cannot verify the selected model.

Read [reliability and recovery](#reliability-and-recovery) before using this mode.
The local database records committed work; a ChatGPT response is not a reliable
completion signal.

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

Confirm generation is finished using the local status command below. If it submitted
a new candidate, open a run with the verifier credential ID. If it saved research
without a new candidate, there is nothing new to verify; a later generation run may
continue that research. Give the separate verifier conversation its own secret key,
new run ID, and the candidate ID confirmed in the database:

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

## Run lifecycle and search policy

Every MCP call requires two independent values:

- `access_key`: a persistent secret scoped to one problem and one role.
- `run_id`: a locally opened authorization boundary bound to that credential.

Open runs using credential IDs, not secret keys, as in step 5. Inspect run history with:

```sh
sh chatgpt_workflow/run.sh --list-runs example
```

Only one run is active per problem. A generator finishes with a checkpoint saved
during that run; a verifier finishes with its verification ID. Closing a run rejects
all later reads and writes using that ID. Do not share an active run between
conversations. A run is not a time limit and does not identify a particular internal
ChatGPT execution path: multiple calls carrying the same valid credentials can use it.

Generation sequences 1, 3, 5, and so on use independent reasoning and stored references.
Sequences 2, 4, 6, and so on permit external search. The server blocks theorem search
and paper downloads during independent runs. ChatGPT's own browser must follow the
same policy through the conversation instructions; the server cannot enforce its
browser behavior. Verification runs permit external source checks.

For cancellation and safe continuation, follow [reliability and recovery](#reliability-and-recovery).

## State and results

Private state lives under `.local/chatgpt-workflow/` and is ignored by Git. Accepted output is written under `agents/generation/results/<problem_id>/` as:

- `blueprint_verified.md`
- `verification.json`
- `chatgpt_manifest.json`

Full problem context and a local accepted-proof export are available with:

```sh
sh chatgpt_workflow/run.sh --status example
sh chatgpt_workflow/run.sh --export example
```

Use `--run-status` below for a concise view of authorization, saved progress, and
recent calls. `--status` retains the full problem-context output for compatibility.

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


## Reliability and recovery

**ChatGPT mode may be unreliable even when the MCP server is functioning correctly.**
Tests in September 2026 observed several distinct problems:

- **Safety blocks before server delivery.** An authorized write returned
  “This tool call was blocked by OpenAI because we couldn't determine the safety
  status of the request.” No corresponding request reached the local server. A
  later authorized retry succeeded; that does not establish a reliable workaround.
- **Missing write tools in discovery.** ChatGPT reported that an explicitly requested
  write tool was unavailable, while the same unchanged tool succeeded elsewhere in
  the recorded activity. Missing client exposure is not evidence that the server
  lacks the tool.
- **Inaccurate or contradictory responses.** A verifier reported zero saved checks
  although the database contained all checks, an accepted review, an export, and a
  finished run. Other messages combined a stopped account with a successful one.
- **Calls after the displayed final response.** The local transport received new
  requests and persisted research more than 15 minutes after a response saying no
  writes had occurred. The user had sent no continuation. These were new incoming
  requests, not local background jobs finishing an earlier write.
- **Repeated startup reads and incomplete call counts.** Final reports sometimes
  listed fewer calls than the local trace. A run ID prevents calls after closure,
  but cannot distinguish multiple upstream executions sharing an active run.
- **Permission changes and separate sandbox failures.** A user observed write
  permissions set to low-risk-only after a UI update. In another turn, ChatGPT's
  sandbox download failed even though Rethlas had already archived the paper.
  Neither observation, by itself, establishes a server defect.

The “our systems are thinking a bit more” notice linked to OpenAI's
[additional safety checks explanation](https://help.openai.com/en/articles/20001326-additional-safety-checks-for-biological-and-cybersecurity-requests-in-chatgpt-codex-and-the-api).
It indicates a check, not necessarily a blocked call. Successful writes occurred
while the notice was displayed. The tests do not establish that safety checks cause
all the discovery, permission, or reporting problems. No reliable client-side fix
was demonstrated. Repeated failures or post-response activity should be reported to
OpenAI Support with exact messages, model/account details, timestamps and time zone,
and redacted call identifiers. Do not send access keys or raw credential-bearing
exports. The diagnostic worktree/logging used in those tests is not part of the
standard launcher.

### Query current state locally

From the repository root, use the local CLI rather than asking ChatGPT to report
its own state:

```sh
sh chatgpt_workflow/run.sh --run-status example
```

This command queries `.local/chatgpt-workflow/workflow.sqlite3` directly in SQLite
read-only mode. It does not call MCP, require an access key or active run, initialize
or migrate a database, or add audit entries. Missing databases or unknown problems
produce an error instead of silently creating state. It works while the server is
running and after a run closes. No server restart or app refresh is needed to use
this local CLI option.

The JSON contains:

- `observed_at_utc`: time of a consistent snapshot of committed state.
- `active_run`, `latest_run`, `recent_runs`: role, sequence, search policy, status,
  final-record binding and timestamps; at most ten recent runs.
- `latest_checkpoint`, `saved_artifact_count`, `recent_artifacts`,
  `archived_file_count`: saved research metadata, without proof text or file bytes.
- `latest_candidate.review`: saved check IDs, missing items, completed review verdict
  and improvement verdict; `null` if no review exists. `accepted_candidate_ids`
  lists the accepted collection, including earlier accepted improvements.
- `recent_calls`: the newest twenty server-observed calls, including run ID,
  operation, outcome, arrival and completion timestamps.
- `recent_events` and `latest_accepted_export_event`: committed state changes and the
  most recent accepted-export event. An event is not a fresh check of exported files.
  Research exports are visible in the call audit when invoked through MCP; the local
  `--export-research` command does not create an MCP call entry.

`archived_file_count` is `null` if the file-library table has not been initialized.
Counts are problem-wide, not limited to the active run. Lists are bounded; for up to
200 audit entries and the full run history use:

```sh
sh chatgpt_workflow/run.sh --audit example
sh chatgpt_workflow/run.sh --list-runs example
```

Interpret the snapshot carefully:

- `active` means requests are still authorized, **not** that ChatGPT is known to be
  running. `finished` and `cancelled` close that authorization.
- Compare two snapshots to detect new calls or saved records. A quiet interval does
  not prove upstream execution has stopped. An incomplete `started` audit entry
  may reflect a crashed process rather than an operation still running.
- A client block before delivery and client-side discovery results cannot appear
  in the database. Failed calls do not always mean no state was committed; inspect
  the relevant record IDs before retrying.
- A checkpoint does not finish a run; a submitted candidate does not mean an
  accepting review; and an accepting review is LLM review, not formal certification.
  In improvement mode, `IMPROVING` can coexist with accepted results.

### Recover without overlapping runs

1. Save the exact ChatGPT response, any recovery files, and the relevant UI times.
   Treat sandbox recovery materials as unsubmitted until database state confirms
   their persistence.
2. Query `--run-status` and compare with the response. If new calls continue after
   the UI appears finished, do not start a concurrent continuation or retry writes.
3. If the run is finished, do not reuse its ID. Hand off a newly submitted candidate
   for verification, or open the next generation run when only research was saved.
4. If the run remains active, reuse its ID only after reconciling saved work and
   ensuring you are not starting a competing execution. If you need a definite
   local stop boundary, cancel it before opening another run:

   ```sh
   sh chatgpt_workflow/run.sh --cancel-run run-REPLACE_WITH_ID
   sh chatgpt_workflow/run.sh --run-status example
   ```

   Cancellation waits for an operation holding the run lock, preserves committed
   work, and rejects subsequent calls with that ID. It does not cancel ChatGPT's
   remote reasoning or guarantee that the UI stops. Already committed writes remain.
5. After cancellation, open a new run locally with the appropriate role credential
   and continue from the saved checkpoint or existing incomplete review. Do not
   automatically resubmit sandbox drafts, overwrite evidence, or claim a new
   improvement without verification.

Check the app's allowed actions and refresh discovery after actual schema changes.
Do not relabel write tools as read-only or disguise requests to avoid a block.
Permission checks and explicit tool discovery can help diagnose availability, but
are not a demonstrated fix for post-response execution or inaccurate reporting.

## Keys and audit

```sh
sh chatgpt_workflow/run.sh --list-keys
sh chatgpt_workflow/run.sh --audit example
sh chatgpt_workflow/run.sh --revoke-key credential-REPLACE_WITH_ID
```

Revocation affects later authorization checks without a restart. Reissue a key if it
leaks. Access logs record credential ID, problem, operation, outcome, and timestamps,
not raw keys or full arguments. Local files and ChatGPT tool exports can still contain
secrets; inspect them before sharing.

## Mathematical skills

`list_skills` and `read_skill` expose ten adapted mathematical-research skills. The
generator should load applicable skills through MCP rather than assume its ChatGPT
sandbox contains repository files. `chatgpt_workflow/skills/sources.json` records the
upstream source hashes and adaptation purpose.

## Files and papers

`save_file` archives immutable UTF-8 text or base64 bytes with provenance. `list_files`
and `read_file` provide paged access. The original file limit is 20 MB. Changed
contents require a new file ID.

Local files must be inside this checkout and can be imported with:

```sh
sh chatgpt_workflow/run.sh --import-file path/to/evidence.txt --problem example --file-id evidence-v1 --provenance 'Local evidence; describe origin and validation'
```

During an external-search run, `download_paper` accepts an arXiv ID, refuses redirects,
limits size, and archives the PDF. PDF extraction uses installed `pdftotext`; missing
tools, timeouts, empty/scanned documents, and extraction errors are reported explicitly.
No OCR is provided. Extracted equations require inspection, and downloaded code is
stored rather than executed. If a separate ChatGPT sandbox download fails after a
successful archive, inspect the archived source using `read_file` before downloading
it again; missing proof-essential evidence remains unresolved.

## Readable research export and backups

```sh
sh chatgpt_workflow/run.sh --export-research example
```

This creates a new directory under `.local/chatgpt-workflow/exports/` containing readable
memory, complete JSON, problem/reference snapshots, candidates, reviews, archived
original files, extracted text, and a SHA-256 manifest. The MCP `export_research` tool
provides the same operation during an active run.

The SQLite database and exports can contain private research. Keep
`.local/chatgpt-workflow/` out of Git. Use SQLite's backup API rather than copying a
database while it is being written. Research export is not a complete database backup
and does not itself finish the run.
