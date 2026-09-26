<!-- Added in 2026 to document the public multi-mode source release. -->

# Rethlas multi-mode source release — 2026-09-26

Suggested GitHub tag: `rethlas-2026-09-26`.

This release is based on upstream Rethlas commit `887cc46`, the last commit in
the history not authored by Yizhen Chen.

## Highlights

- Three documented workflows: original Codex, MCP-free sandboxed Codex, and
  two-conversation ChatGPT.
- `gpt-6-astra` with `max` reasoning as the default generator, verifier, and
  subgoal model, with documented environment overrides.
- Safe Codex runner continuation from append-only logs, explicit session
  selection, dry-run validation, cooperative pause markers, overwrite refusal,
  elapsed timing, and human-readable iteration boundaries and completion output.
- A standard-library sandboxed coordinator using `workspace-write`, managed
  `on-request` approvals, disabled MCP entries, isolated runs, frozen inputs,
  resumable generator sessions, fresh verifier sessions, and collision-safe
  exports.
- A ChatGPT MCP workflow with role-scoped local keys, credential-bound runs,
  immutable candidates and reviews, append-only research, bounded file and paper
  archival, readable exports, and read-only local `--run-status` snapshots.
- Fixed-statement verification by default and opt-in iterative improvement in all
  three modes. Improvements require an independently checked proof and a strict
  comparison with the immutable original question and every accepted result.
- Updated setup, recovery, capability-boundary, and reliability documentation.

## Important boundaries

- ChatGPT mode does not launch ChatGPT or select its model. It requires a separately
  configured ChatGPT-compatible MCP connector or private tunnel.
- Observed ChatGPT client behavior can be unreliable. Use the local status command
  and durable database state instead of treating a displayed response as proof that
  writes finished or stopped.
- Sandboxed Codex shell/write capability, source retrieval, model availability,
  native delegation, and mathematical correctness are separate checks.
- Accepted proofs are independently LLM-reviewed informal proofs, not formal proof
  certificates from Lean, Coq, or another proof kernel.

## Release scope

The Git release contains reusable source, documentation, examples, and offline
tests. It excludes local credentials and databases, virtual environments, caches,
logs, generated results, sandbox runs and probes, temporary diagnostics and support
bundles, downloaded site assets, and the current unpublished research inputs.

## Validation

- 47 sandbox workflow tests passed.
- 11 ChatGPT workflow and read-only status tests passed using the existing
  project environment.
- 4 verification-protocol tests, Python compilation, TOML parsing, shell syntax,
  CLI help and dry runs, local Markdown links, Apache notice coverage, tracked-file
  scans, and `git diff --check` passed.

Model wording was checked against official OpenAI documentation. No live Codex
inference, ChatGPT conversation, workflow theorem/paper retrieval, dependency
installation, service restart, tunnel connection, tag creation, or push was
performed while preparing this release.

See `NOTICE` for changed-file attribution and `RELEASE_MANIFEST.md` for the exact
inclusion and exclusion policy.
