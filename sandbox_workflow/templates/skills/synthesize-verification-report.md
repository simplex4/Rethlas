# Synthesize the verification report

<!-- Adapted from agents/verification/.agents/skills/synthesize-verification-report/SKILL.md. -->

Collect every finding from statement and reference checks. Produce all fields below, with strings for hashes, summary, verdict, repair hints, and each issue's location and description. Copy hashes exactly from `binding.json`. Include all errors and gaps, including incomplete essential checks. `correct` is allowed only after checking the whole candidate with no errors and no gaps; otherwise use `wrong` and concrete nonempty repair hints.

```json
{
  "statement_sha256": "COPY_FROM_BINDING",
  "candidate_sha256": "COPY_FROM_BINDING",
  "verification_report": {
    "summary": "Describe what was checked and what remains unresolved.",
    "critical_errors": [{"location": "Lemma 1", "issue": "Concrete incorrect inference"}],
    "gaps": [{"location": "Theorem proof", "issue": "Concrete missing justification"}]
  },
  "verdict": "wrong",
  "repair_hints": "Explain the repairs required."
}
```

For a correct report both issue arrays must be empty; repair hints may be empty. Save the exact report through `python3 research.py append --channel verification_reports --file report.json` and return the same JSON as the final structured response. No unavailable validation or output-writing tool is needed: the coordinator validates schema, verdict consistency, and input hashes. Do not publish a verified proof, change candidate bytes, or substitute a new binding. Missing output, inability to complete review, or a blocked dependency must never be represented as successful verification.

When turn.json mode is improvement, also include improvement_assessment as required by
output.schema.json: baseline_sha256 from binding.json, verdict strict_improvement,
not_improvement, or unresolved, and an explanation comparing domains, hypotheses and
strict gain over the whole frozen baseline. Keep this comparison verdict independent of
the proof verdict. A correct but non-improving theorem is not an accepted improvement.
