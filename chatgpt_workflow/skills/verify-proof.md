# verify-proof

Hand a complete candidate to the separate verifier and persist its assessment.

Generation: assemble a complete proof of the original statement using get_workflow's item/citation contract; submit_candidate. Save a final checkpoint including candidate_id, read it back, and finish_run. Give the human the candidate ID. Never use the legacy verifier or review your own candidate. Verification: load the verification workflow; begin_review, read all candidate items and sources, and record_review_checks plus any additional record_review_findings. Include full error and gap evidence. submit_review only after complete coverage; export_accepted if accepted. Finish the run with verification_id. A verdict is an LLM assessment, not formal certification. The human opens the next generation run for revision.

Every MCP call requires the current access_key and run_id.
