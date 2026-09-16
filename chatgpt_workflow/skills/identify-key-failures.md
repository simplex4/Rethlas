---
name: identify-key-failures
description: Synthesize the common stuck points across failed decomposition plans and sequential branch reports. Use when the current batch of decomposition plans has failed.
---
## ChatGPT adaptation

Use only the current role and active run. Supply access_key and run_id on every MCP call. External research is allowed only when get_run reports external-search. For independent runs use stored sources and your own reasoning. Retrieve related skills with read_skill; $skill names below refer to that tool, not local filesystem access. Save meaningful outputs through record_artifacts with a unique run-qualified record_id and an allowed channel; any example event object is JSON inside its text, not an MCP argument schema. search_memory uses literal substring search with one channel per call and pagination; read_artifact retrieves full text. No autonomous agents or inference services are required.



# Identify Key Failures

Use this skill to turn many failed attempts into reusable guidance for the next planning round.

## Input Contract

Read:

- the failed decomposition plans
- direct-proving stuck points
- sequential branch reports
- existing `failed_paths`
- relevant `counterexamples` and `toy_examples`

## Procedure

1. Gather the reports from all failed plans and sub-agents.
2. List the key stuck points for each plan.
3. Identify common points across those failures:
   - recurring obstructions or counterexamples
   - decomposition patterns that keep breaking
   - search gaps or missing background facts
4. Summarize what the failures suggest for the next generation of decomposition plans.
5. Save the synthesized failure knowledge to `failed_paths` so later planning skills can use it.
6. After recording the failure synthesis, return control to `$propose-subgoal-decomposition-plans`.

## Output Contract

Append to `failed_paths`:

```json
{
  "record_type": "key_failures_summary",
  "failed_plan_ids": ["..."],
  "plan_failures": [
    {
      "plan_id": "...",
      "stuck_points": ["..."]
    }
  ],
  "common_failures": ["..."],
  "implications_for_next_plans": ["..."]
}
```

Also save a branch_states artifact indicating that a new planning round is needed.

## MCP Tools

- `search_memory`
- `record_artifacts`
- `branch_update`

## Failure Logging

If the reports are too weak to identify meaningful common failures, save a branch_states artifact with `event_type="key_failures_inconclusive"` and state what information is still missing.
