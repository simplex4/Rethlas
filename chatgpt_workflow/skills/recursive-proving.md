# recursive-proving

Explore decomposition plans sequentially when direct attempts stall.

Read the plans, direct attempts and known failures. Work through one plan at a time within this chat, applying direct-proving to its subgoals. Preserve the original target, assumptions and previous failure evidence. Refine subgoals when justified, checking for counterexamples and circular dependencies. Save branch states and proofs with run-qualified IDs before switching plans. After exploring alternatives, synthesize common obstructions with identify-key-failures and try a materially different approach. Do not spawn chats or call another inference service. A checkpoint is an intermediate save; continue useful work within the active run.

Every MCP call requires the current access_key and run_id.
