---
name: obtain-immediate-conclusions
description: Derive immediate mathematical consequences from a theorem statement or subgoal. Use when starting a new problem, branch, or subgoal, or when cheap progress or a cleaner reformulation is needed before deeper proof search.
---
## ChatGPT adaptation

Use only the current role and active run. Supply access_key and run_id on every MCP call. External research is allowed only when get_run reports external-search. For independent runs use stored sources and your own reasoning. Retrieve related skills with read_skill; $skill names below refer to that tool, not local filesystem access. Save meaningful outputs through record_artifacts with a unique run-qualified record_id and an allowed channel; any example event object is JSON inside its text, not an MCP argument schema. search_memory uses literal substring search with one channel per call and pagination; read_artifact retrieves full text. No autonomous agents or inference services are required.



# Obtain Immediate Conclusions

Extract direct implications before speculative reasoning.

## Input Contract

Read from memory and current context:

- `problem_id`
- current theorem/subgoal statement
- memory

## Procedure

1. Normalize notation and restate the claim in equivalent forms.
2. List direct consequences that follow from definitions and basic algebraic/logical manipulations.
3. Split consequences into necessary conditions and candidate sufficient conditions.
4. Mark each consequence with confidence and justification type.
5. For every conclusion, explicitly decide whether it is likely fragile and should be stress-tested by counterexample.
6. If a conclusion is fragile, record why it is fragile and indicate that `$construct-counterexamples` should be considered next.

## Output Contract

Append each conclusion to `immediate_conclusions` with JSON object payload:

```json
{
  "statement": "...",
  "justification_type": "by_definition|calculation|known_fact|logical_equivalence",
  "confidence": 0.0,
  "is_fragile": false,
  "fragility_reason": "",
  "suggested_followup": "none|construct-counterexamples",
  "scope": "global|branch|subgoal",
  "branch_id": "optional",
  "subgoal_id": "optional"
}
```

Rules:

- `is_fragile` must always be present.
- If `is_fragile=true`, then `fragility_reason` must explain the risk and `suggested_followup` should be `construct-counterexamples`.
- If `is_fragile=false`, use `fragility_reason=""` and `suggested_followup="none"`.

## MCP Tools

- `record_artifacts`
- `search_memory`
- `search_arxiv_theorems` for nontrivial consequences
- Codex built-in web search for background definitions/terminology

## Failure Logging

If no meaningful consequence is found, save a branch_states artifact with:

- `event_type="immediate_conclusions_stalled"`
- missing assumptions and suspected blockers
