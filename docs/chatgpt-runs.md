<!-- Added in 2026 to document access keys, run IDs, and durable research storage. -->

# ChatGPT MCP runs and research storage

## Run lifecycle

Every MCP call requires two independent values:

- `access_key`: a persistent secret scoped to one problem and one role
- `run_id`: a locally opened, temporary authorization boundary bound to that credential

Open runs by credential ID, not by secret key:

```sh
sh chatgpt_workflow/run.sh --list-keys
sh chatgpt_workflow/run.sh --open-run credential-REPLACE_WITH_ID
sh chatgpt_workflow/run.sh --list-runs example
```

Only one active run is allowed per problem. A generator must finish with a checkpoint saved during that run; a verifier must finish with its verification ID. `finish_run` rejects every later read or write with that run ID.

If a conversation ends without finishing, either reuse the still-active run ID or cancel it locally before opening another:

```sh
sh chatgpt_workflow/run.sh --cancel-run run-REPLACE_WITH_ID
```

Cancellation waits for an active operation, preserves saved work, and blocks later calls. Run IDs prevent stale continuation writes after closure; they cannot prevent additional ChatGPT messages while a run remains active. Do not share one active run between conversations.

## Alternating generation policy

Generation run sequences 1, 3, 5, and so on use independent reasoning; sequences 2, 4, 6, and so on permit external search. The MCP server blocks theorem search and paper download during independent runs. Stored references and skills remain available. ChatGPT's own browser is governed by the conversation instructions and must follow the same policy.

Verification runs permit external source checks.

## Keys and audit

```sh
sh chatgpt_workflow/run.sh --list-keys
sh chatgpt_workflow/run.sh --audit example
sh chatgpt_workflow/run.sh --revoke-key credential-REPLACE_WITH_ID
```

Revocation affects later checks without a restart. Reissue a key if it leaks. Access logs record credential ID, problem, operation, outcome, and timestamps—not raw keys or full arguments.

## Mathematical skills

`list_skills` and `read_skill` expose ten adapted mathematical-research skills. The generator should load applicable skills through MCP rather than assume its ChatGPT sandbox contains repository files. `skills/sources.json` records the upstream skill source hashes and adaptation purpose.

## Files and papers

`save_file` archives immutable UTF-8 text or base64 bytes with provenance. `list_files` and `read_file` provide paged access. The original file limit is 20 MB. Changed contents require a new file ID.

Local files must be inside this checkout and can be imported with:

```sh
sh chatgpt_workflow/run.sh --import-file path/to/evidence.txt --problem example --file-id evidence-v1 --provenance 'Local evidence; describe origin and validation'
```

During an external-search run, `download_paper` accepts an arXiv ID, refuses redirects, limits size, and archives the PDF. PDF extraction uses the installed `pdftotext`; missing tools, timeouts, empty/scanned documents, and extraction errors are reported explicitly. No OCR is provided. Extracted equations require inspection, and downloaded code is stored rather than executed.

## Readable research export

```sh
sh chatgpt_workflow/run.sh --export-research example
```

This creates a new directory under `.local/chatgpt-workflow/exports/` containing readable memory, complete JSON, problem/reference snapshots, candidates, reviews, archived original files, extracted text, and a SHA-256 manifest. The MCP `export_research` tool provides the same operation during an active run.

The authoritative SQLite database and exports can contain private research. Keep `.local/chatgpt-workflow/` out of Git and use SQLite's backup API rather than copying a database while it is being written.
