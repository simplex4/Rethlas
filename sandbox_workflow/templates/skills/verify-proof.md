# Submit a complete proof for independent verification

<!-- Adapted from agents/generation/.agents/skills/verify-proof/SKILL.md. -->

Use only when `blueprint.md` contains a full candidate proof of the original problem, not isolated lemmas, a partial branch, or exploratory notes. Read the entire draft, the original statement, and previous reports. Check that every required argument is supplied. In fixed mode the main theorem must reproduce the complete input statement; in improvement mode apply the additional contract below.

Before submission, collect all proof-essential computation scripts, inputs, outputs, replay instructions, and dependency/version notes in `artifacts/`; preserve cited source documents in `references/`. Cite relevant artifact paths in the proof and explain why the exact computations support the claims. Evidence stored only in private research memory is unavailable to the verifier.

Run `python3 research.py submit --file blueprint.md`, which records the submission for the coordinator, then end the turn. Do not modify the submitted draft, references, or computational artifacts after submission in this turn. The coordinator freezes the candidate together with `references/` and `artifacts/` and launches a fresh verifier with those files, without private research memory. Do not invoke a verifier yourself or create `blueprint_verified.md`.

A later generator turn may supply the report with `verification_report` (summary, critical errors, gaps), `verdict`, `repair_hints`, and input hashes. Preserve the report exactly. Any `wrong` verdict, any critical error, any gap, or absent/invalid report is not acceptance. Resolve critical errors first, then every gap, changing strategy if necessary. Persist branch invalidations and repairs. Resubmit a complete revised candidate for independent review. Only the coordinator can accept a validated report bound to the exact original statement and candidate; never claim acceptance based on self-review or a submission acknowledgment.

For turn.json mode=improvement, the original question remains immutable context but the final theorem states the precise new claim. Read baseline.json and submit with --improvement-file improvement.json (statement and improvement strings). For fixed mode, the final theorem statement must exactly match statement.md. Correct submission errors in this turn; submission success is not verification. Appendices may follow the final mathematical theorem.
