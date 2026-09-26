<!-- Added in 2026 to record the public source-release boundary. -->

# Release manifest

## Boundary

- Upstream comparison commit: `887cc46`.
- Release form: the Git-tracked source tree in the release commit; no generated
  archive, database, result tree, or vendored environment is committed.
- License: Apache License 2.0, with attribution in `NOTICE` and prominent notices
  in every file modified relative to the upstream comparison commit.

## Included

- `LICENSE`, `NOTICE`, `README.md`, `RELEASE_NOTES.md`, and this manifest.
- Original Codex generator/verifier source, prompts, skills, schemas, dependency
  lists, reusable examples and references, runner, and optional Zola source files.
- `sandbox_workflow/`: coordinator, research contracts and commands, templates,
  offline tests, and terminal-output tests.
- `chatgpt_workflow/`: MCP service, authorization and run lifecycle, transactional
  store, read-only status snapshots, research/file helpers, instructions, adapted
  skills, dependency pins, launcher, and offline tests.
- `docs/`: complete Codex, sandboxed Codex, and ChatGPT setup and recovery guides.
- Verification protocol and iterative-improvement regression tests.

The former `docs/chatgpt-runs.md` content is consolidated into
`docs/chatgpt-workflow.md`; the obsolete duplicate file is intentionally removed.

## Excluded by `.gitignore`

- `.local/`: ChatGPT databases, credentials, locks and exports, plus sandboxed
  Codex runs, exports, and capability probes.
- `.venv*`, `venv/`, `env/`, Python bytecode, test/type/lint caches, and coverage.
- Agent logs, memory, results, downloads, verifier state, and extracted references.
- Zola output, downloaded themes, generated site content, editor files, OS metadata,
  local environment files, private keys, and certificates.
- `tmp/`: diagnostics, debug logs, support bundles, worktrees, recovery drafts, and
  other temporary material.
- New untracked inputs under `agents/generation/data/`: current unpublished research
  problems and references. Already tracked reusable examples remain included. Local
  research files are not deleted by the release process.

## Release checks

Before committing, the candidate tree is checked for ignored/untracked research,
credentials and local state, absolute developer paths, debug/database/log artifacts,
broken local documentation links, missing Apache modification notices, syntax errors,
test failures, and whitespace errors. The release commit is local only; pushing and
tagging are outside this preparation step.
