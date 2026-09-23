# Check referenced statements

<!-- Adapted from agents/verification/.agents/skills/check-referenced-statements/SKILL.md. -->

For every external theorem, lemma, or definition used in the candidate:

1. Record its proof location and complete cited statement. Inspect supplied references first; use `skills/search-math-results.md` for permitted retrieval of missing sources.
2. Read the primary source's exact statement, relevant definitions, and proof. Compare terminology, formulas, quantifiers, hypotheses, and ambient objects. Identical names do not imply identical definitions.
3. Check both whether the cited result matches and whether it applies here. Verify every downstream deduction from that result, including specializations and transitions from one property to another.
4. An invalid transition or mismatched definition/hypothesis is a critical error. Missing justification or an unverified specialization is a gap even if the cited theorem exists.
5. If a source is inaccessible or cannot be found, record an unresolved essential citation as a gap and explain the exact limitation. Failed retrieval alone does not prove nonexistence. If the obtained source contradicts the citation, record a critical error with evidence.
6. Write each structured finding to a local JSON file and run `python3 research.py append --channel reference_checks --file record.json`. Include location, referenced statement, source identifiers, local source path, context expansion, applicability analysis, critical errors, gaps, and access limitations.

Perform mathematical comparison through careful reasoning rather than a superficial string-matching utility. Never silently accept an essential unverified reference.
