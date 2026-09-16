<!-- Added in 2026 for the ChatGPT MCP source release. -->

# Rethlas ChatGPT MCP release — 2026-09-16

Suggested GitHub tag: `chatgpt-mcp-2026-09-16`.

This source release is based on upstream Rethlas commit `887cc46` and adds:

- a production two-conversation ChatGPT MCP proof workflow
- problem/role access keys, hashed at rest and revocable locally
- credential-bound run IDs with explicit finish/cancel boundaries
- immutable candidates, reviews, file evidence, paper archival, and readable research exports
- `gpt-6-astra` with `max` reasoning as the Codex generator, verifier, and subgoal-prover default
- Codex runner dry-run validation, safe log-based resumption, pause markers, explicit session selection, and overwrite refusal
- portable documentation for original Codex features and new ChatGPT MCP features

The release deliberately excludes local credentials, databases, virtual environments, logs, results, current problem data, recovered research, diagnostic probes, development progress notes, and test-only files. It contains one upstream example so both workflows have an out-of-box input.

ChatGPT MCP mode requires a separately configured ChatGPT-compatible MCP connector or private tunnel. Model selection in ordinary ChatGPT is manual and cannot be enforced by this server.

## Validation

- 26 implementation tests passed against the working source.
- 19 access-control and end-to-end MCP protocol tests passed against a temporary copy of the staged release.
- Python and TOML parsing, Bash/POSIX shell syntax, staged CLI help, and the Codex runner dry-run passed.
- The staged tree was scanned for databases, logs, bytecode, OS metadata, absolute developer paths, run-specific recovery identifiers, and credential/run/session token patterns; none were found.

No live Codex inference, ChatGPT conversation, tunnel connection, dependency installation, or service restart was performed while assembling the release.

See `NOTICE` for changed-file attribution and `RELEASE_MANIFEST.md` for the inclusion/exclusion policy.
