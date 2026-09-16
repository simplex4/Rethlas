<!-- Added in 2026 to record the release boundary. -->

# Release manifest

## Included

- Apache 2.0 license and modification notice
- original Codex generator and verifier runtime code, prompts, skills, schemas, and dependency lists
- one original example problem used by the default launcher
- `run_example.sh`, including dry-run, pause, and resume controls
- optional Zola renderer files
- production `chatgpt_workflow` service, prompts, adapted skills, dependency pins, and launcher
- user and release documentation

## Excluded as unnecessary for runtime

- ChatGPT diagnostic probe and its tests
- unit and endpoint test suites
- development state/progress notes
- extra demonstration problems and references
- editor and OS metadata

## Excluded as local or run-specific

- `.local/` databases, credentials, locks, backups, and exports
- `.venv*` environments and caches
- logs, generated memory, results, and extracted references
- the current unpublished input and recovered research bundle
- all access keys, credential IDs, run IDs, session IDs, and live process state
- the run-specific recovery module and bundle-import CLI path

The archive is assembled from an explicit allowlist. It is not a snapshot of the working directory.
