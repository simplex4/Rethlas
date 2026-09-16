---
name: propose-subgoal-decomposition-plans
description: Propose multiple subgoal decomposition plans for the current theorem using the information already gathered. Use when enough information has been collected from examples, counterexamples, search results, and previous failures to break the problem into several materially different plans.
---
## ChatGPT adaptation

Use only the current role and active run. Supply access_key and run_id on every MCP call. External research is allowed only when get_run reports external-search. For independent runs use stored sources and your own reasoning. Retrieve related skills with read_skill; $skill names below refer to that tool, not local filesystem access. Save meaningful outputs through record_artifacts with a unique run-qualified record_id and an allowed channel; any example event object is JSON inside its text, not an MCP argument schema. search_memory uses literal substring search with one channel per call and pagination; read_artifact retrieves full text. No autonomous agents or inference services are required.



# Propose Subgoal Decomposition Plans

Use this skill when the agent has enough context to propose several viable decomposition plans.

## Input Contract

Read:

- the current target theorem or branch goal
- relevant `immediate_conclusions`, `toy_examples`, and `counterexamples`
- relevant `failed_paths` and `branch_states`
- recent search results and useful references from saved sources and branch_states artifacts

## Procedure

1. Gather the current information that materially constrains the problem: useful examples, failed claims, known obstructions, and relevant search results.
2. Propose materially different decomposition plans.
3. For each plan, state:
   - the main idea of the plan
   - the ordered subgoals
   - why this plan is plausible given the current information
   - which earlier failures or counterexamples it tries to avoid
4. Hand each plan to `$direct-proving` for a quick screening pass.

## Output Contract

Append one record per plan to `subgoals`:

```json
{
  "plan_id": "...",
  "record_type": "decomposition_plan",
  "goal": "...",
  "plan_summary": "...",
  "subgoals": ["..."],
  "motivation": ["..."],
  "uses_information_from": {
    "examples": ["..."],
    "counterexamples": ["..."],
    "key_failures": ["..."],
    "search_results": ["..."]
  },
  "status": "proposed|screening|screened|selected|failed|solved",
  "branch_id": "optional"
}
```

Also save a branch_states artifact summarizing the new plan set.

## MCP Tools

- `search_memory`
- `record_artifacts`
- `branch_update`
- `search_arxiv_theorems`

## Failure Logging

If the agent cannot yet propose meaningful decomposition plans, save a branch_states artifact with:

- `event_type="decomposition_plans_not_ready"`
- the missing information
- the blockers that prevent proposing plans
