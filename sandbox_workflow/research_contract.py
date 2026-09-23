"""Shared, standard-library contracts for fixed proofs and iterative research.

Also copied beside the sandbox-local research CLI. These checks validate the
protocol, never the truth of a theorem or the mathematical improvement relation.
"""
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def validate_claim(claim):
    if not isinstance(claim, dict) or set(claim) != {'statement', 'improvement'}:
        raise ValueError('Improvement claim requires exactly statement and improvement fields')
    for key, value in claim.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 200_000:
            raise ValueError(f'Claim {key} must be nonblank text, at most 200000 characters')
    return claim


def final_theorem(proof):
    """Find the final mathematical item; permit non-mathematical appendices.

    Ignore fenced code when detecting headings. No normalization of formulas or
    statement content is performed; only boundary whitespace is stripped.
    """
    headings = []
    offset = 0
    fence = None
    for line in proof.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r'(`{3,}|~{3,})', stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            match = re.match(r'^# (.+?)\s*$', line)
            if match:
                headings.append((offset, offset + len(line), match.group(1)))
        offset += len(line)
    math = [(i, h) for i, h in enumerate(headings)
            if re.match(r'^(theorem|lemma|proposition|claim|definition)\b', h[2], re.I)]
    if not math or not re.match(r'^theorem\b', math[-1][1][2], re.I):
        raise ValueError('The last mathematical item must be a # theorem with ## statement and ## proof')
    i, h = math[-1]
    end = headings[i + 1][0] if i + 1 < len(headings) else len(proof)
    section = proof[h[1]:end]
    match = re.search(r'(?ms)^## statement[ \t]*\n(.*?)^## proof[ \t]*\n(.+)', section)
    if not match or not match.group(2).strip():
        raise ValueError('Final theorem is missing ## statement or a nonempty ## proof')
    return match.group(1).strip(), match.group(2).strip()


def validate_candidate(proof, original, mode='fixed', claim=None):
    statement, _ = final_theorem(proof)
    if mode == 'fixed':
        if claim is not None:
            raise ValueError('Fixed-statement mode does not accept an improvement claim')
        if statement != original.strip():
            raise ValueError('Fixed theorem statement must equal the original exactly; move commentary into ## proof')
    elif mode == 'improvement':
        validate_claim(claim)
        if statement != claim['statement'].strip():
            raise ValueError('Final theorem statement must equal improvement.json statement; keep the original question unchanged')
    else:
        raise ValueError('Unknown research mode')
    return statement


def comparison_schema():
    props = {'baseline_sha256': {'type': 'string'},
             'verdict': {'type': 'string', 'enum': ['strict_improvement', 'not_improvement', 'unresolved']},
             'explanation': {'type': 'string'}}
    return {'type': 'object', 'additionalProperties': False,
            'properties': props, 'required': list(props)}


def validate_comparison(value, expected_hash):
    if not isinstance(value, dict) or set(value) != set(comparison_schema()['properties']):
        raise ValueError('Independent improvement assessment is required')
    if value['baseline_sha256'] != expected_hash:
        raise ValueError('Improvement assessment refers to a stale baseline')
    if value['verdict'] not in ('strict_improvement', 'not_improvement', 'unresolved'):
        raise ValueError('Invalid improvement verdict')
    if not isinstance(value['explanation'], str) or not value['explanation'].strip():
        raise ValueError('Improvement assessment must justify domains, hypotheses and strict gain')
    return value['verdict'] == 'strict_improvement'


IMPROVEMENT_INSTRUCTIONS = '''This is iterative-improvement research. The original question is immutable context,
not the new theorem statement. Read every accepted result in the baseline. State a precise new theorem
with its domain, quantifiers and hypotheses, and explain exactly where it strictly improves on the
original known results AND the whole accepted collection. Disjoint-domain improvements are useful;
weaker hypotheses must be compared explicitly. Rewording, equivalent bounds, restricted claims with
no stronger consequence, and unproved optimality assertions are not improvements. Preserve all
previously accepted results. Exhausted search is not a proof of optimality.'''

VERIFIER_IMPROVEMENT_INSTRUCTIONS = '''Independently check two separate obligations: correctness of the entire
candidate proof, and whether its precise claim is a strict improvement for the original question
relative to the frozen baseline (including the original known results). Inspect exact quantifiers,
domains, hypotheses, formulas and a concrete strict gain; check any comparison argument. Return
improvement_assessment with the exact baseline_sha256, verdict strict_improvement, not_improvement,
or unresolved, and a substantive explanation. Correct-but-equivalent results are not progress.
An unavailable or unproved comparison is unresolved. Do not adopt the generator's improvement claim
as an established fact. No result is promoted unless BOTH proof and improvement checks pass.'''
