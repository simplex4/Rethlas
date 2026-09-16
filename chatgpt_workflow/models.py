"""Input contracts. Validation checks protocol consistency, not mathematical truth."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

Text = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=200_000)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,80}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Citation(Contract):
    citation_id: Identifier
    statement: Text
    source: Text
    theorem_id: str = Field(default="", max_length=2000)
    arxiv_id: str = Field(default="", max_length=200)
    applicability: Text


class ProofItem(Contract):
    item_id: Identifier
    kind: Literal["definition", "lemma", "proposition", "claim", "theorem"]
    statement: Text
    proof: Text
    citations: list[Citation] = Field(default_factory=list, max_length=100)


class Finding(Contract):
    location: Text
    issue: Text


class ReferenceCheck(Contract):
    citation_id: Identifier
    outcome: Literal["ok", "wrong", "unresolved"]
    assessment: Text


class ItemCheck(Contract):
    item_id: Identifier
    assessment: Text
    critical_errors: list[Finding] = Field(default_factory=list, max_length=200)
    gaps: list[Finding] = Field(default_factory=list, max_length=200)
    reference_checks: list[ReferenceCheck] = Field(default_factory=list, max_length=100)


class Artifact(Contract):
    record_id: Identifier
    channel: Literal["immediate_conclusions", "toy_examples", "counterexamples",
                     "big_decisions", "subgoals", "proof_steps", "failed_paths",
                     "branch_states", "sources", "checkpoint"]
    text: Text
    provenance: str = Field(default="", max_length=20_000)


class AdditionalFinding(Finding):
    record_id: Identifier
    kind: Literal["critical_error", "gap"]


def nonblank(value: str, name: str, maximum: int = 200_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonblank text of at most {maximum} characters")
    return value
