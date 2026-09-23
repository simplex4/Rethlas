"""Durable result rounds for the original MCP/HTTP Codex workflow.

This module does not launch Codex or change its account/sandbox configuration.
"""
import argparse
import json
from pathlib import Path

from .core import atomic, read_json, sha, validate_review, WorkflowError
from .research_contract import digest, validate_candidate, validate_comparison


def prepare(policy_file, statement_file, mode):
    policy_file, statement_file = Path(policy_file), Path(statement_file)
    original = statement_file.read_text()
    if policy_file.exists():
        policy = read_json(policy_file)
        if policy['original_question'] != original or policy['mode'] != mode:
            raise ValueError('Existing research policy/input differs; use a new problem ID for a different mode or question')
    else:
        policy = {'mode': mode, 'original_question': original, 'accepted_results': []}
        atomic(policy_file, policy)
    return policy


def context(policy, proof, claim=None):
    validate_candidate(proof, policy['original_question'], policy['mode'], claim)
    baseline = {'original_question': policy['original_question'], 'accepted_results': policy['accepted_results']}
    return {'mode': policy['mode'], 'original_question': policy['original_question'],
            'claim': claim, 'baseline': baseline, 'baseline_sha256': digest(baseline)}


def binding(proof, ctx):
    result = {'statement_sha256': sha(ctx['original_question'].encode()), 'candidate_sha256': sha(proof.encode())}
    if ctx['mode'] == 'improvement':
        result.update(mode='improvement', baseline_sha256=ctx['baseline_sha256'], claim=ctx['claim'])
    return result


def promoted(report, ctx):
    return report['verdict'] == 'correct' and (ctx['mode'] != 'improvement' or
            validate_comparison(report['improvement_assessment'], ctx['baseline_sha256']))


def save_receipt(policy_file, proof, ctx, report):
    policy_file = Path(policy_file)
    if context(read_json(policy_file), proof, ctx['claim']) != ctx:
        raise ValueError('Research baseline changed during verification')
    validate_review(report, binding(proof, ctx))
    receipt = {'proof': proof, 'context': ctx, 'report': report}
    path = policy_file.parent / 'receipts' / (sha(proof.encode()) + '.json')
    # Repeated identical verification may not overwrite prior evidence.
    if path.exists() and read_json(path) != receipt:
        raise ValueError('A different review already exists for this exact proof; revise the candidate')
    atomic(path, receipt)
    return receipt


def collect(policy_file, verified_file):
    policy_file, verified_file = Path(policy_file), Path(verified_file)
    if not verified_file.exists():
        return {'accepted': False}
    proof = verified_file.read_text()
    key = sha(proof.encode())
    policy = read_json(policy_file)
    receipt = read_json(policy_file.parent / 'receipts' / (key + '.json'))
    if receipt['proof'] != proof:
        raise ValueError('Published proof differs from reviewed proof')
    ctx, report = receipt['context'], receipt['report']
    validate_review(report, binding(proof, ctx))
    if not promoted(report, ctx):
        raise ValueError('Published proof has not passed both required verification checks')
    previous = next((r for r in policy['accepted_results'] if r['candidate_sha256'] == key), None)
    if previous is None:
        if context(policy, proof, ctx['claim']) != ctx:
            raise ValueError('Published proof was reviewed against a stale baseline')
        destination = policy_file.parent.parent / 'improvements' / key
        destination.mkdir(parents=True, exist_ok=True)
        for name, data in [('blueprint_verified.md', proof.encode()), ('verification.json', report), ('context.json', ctx)]:
            target = destination / name
            if target.exists():
                expected = data if isinstance(data, bytes) else (json.dumps(data, indent=2, ensure_ascii=False)+'\n').encode()
                if target.read_bytes() != expected:
                    raise ValueError('Accepted-result archive collision')
            atomic(target, data)
        policy['accepted_results'].append({'candidate_sha256': key, 'claim': ctx['claim'], 'proof': proof})
        atomic(policy_file, policy)
    if policy['mode'] == 'improvement':
        # Remove only the transient completion marker after durable archival.
        verified_file.unlink()
    return {'accepted': True, 'accepted_results': len(policy['accepted_results']), 'mode': policy['mode']}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('operation', choices=['prepare', 'collect'])
    p.add_argument('--policy', required=True)
    p.add_argument('--statement')
    p.add_argument('--mode', choices=['fixed', 'improvement'], default='fixed')
    p.add_argument('--verified')
    a = p.parse_args()
    try:
        result = prepare(a.policy, a.statement, a.mode) if a.operation == 'prepare' else collect(a.policy, a.verified)
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, WorkflowError) as exc:
        p.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
