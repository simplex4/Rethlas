import tempfile
import unittest
from pathlib import Path
from sandbox_workflow import legacy
from sandbox_workflow.core import read_json, atomic
from sandbox_workflow.tests.test_workflow import report


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.policy = self.root / '.research/policy.json'
        self.statement = self.root / 'statement.md'
        self.statement.write_text('Improve the lower bound.')
        self.marker = self.root / 'blueprint_verified.md'
        legacy.prepare(self.policy, self.statement, 'improvement')

    def candidate(self, n, verdict='strict_improvement', correct=True):
        claim = {'statement': f'Bound >= {n}.', 'improvement': f'Gain to {n}.'}
        proof = f"# theorem main\n## statement\n{claim['statement']}\n## proof\nArgument {n}.\n"
        ctx = legacy.context(read_json(self.policy), proof, claim)
        b = legacy.binding(proof, ctx)
        r = report({k:b[k] for k in ('statement_sha256','candidate_sha256')},correct)
        r['improvement_assessment']={'baseline_sha256':ctx['baseline_sha256'],'verdict':verdict,'explanation':'Check domains and strict gain.'}
        legacy.save_receipt(self.policy, proof, ctx, r)
        self.marker.write_text(proof)
        return ctx

    def test_collect_restarts_and_archives_two_rounds(self):
        self.candidate(2)
        self.assertTrue(legacy.collect(self.policy,self.marker)['accepted'])
        self.assertFalse(self.marker.exists())
        ctx=self.candidate(3)
        self.assertEqual(len(ctx['baseline']['accepted_results']),1)
        legacy.collect(self.policy,self.marker)
        self.assertEqual(len(list((self.root/'improvements').glob('*/blueprint_verified.md'))),2)
        self.assertEqual(len(read_json(self.policy)['accepted_results']),2)

    def test_no_gain_and_wrong_proof_cannot_publish(self):
        for n, verdict, correct in [(2,'not_improvement',True),(3,'strict_improvement',False),(4,'unresolved',True)]:
            self.candidate(n,verdict,correct)
            with self.assertRaisesRegex(ValueError,'both required'):
                legacy.collect(self.policy,self.marker)
        self.assertEqual(read_json(self.policy)['accepted_results'],[])

    def test_unreviewed_marker_and_policy_change_rejected(self):
        self.marker.write_text('unreviewed')
        with self.assertRaises(FileNotFoundError): legacy.collect(self.policy,self.marker)
        with self.assertRaises(ValueError): legacy.prepare(self.policy,self.statement,'fixed')

    def test_fixed_statement_immutable_and_marker_retained(self):
        policy=self.root/'fixed/.research/policy.json'
        legacy.prepare(policy,self.statement,'fixed')
        proof='# theorem main\n## statement\nImprove the lower bound.\n## proof\nArgument.'
        ctx=legacy.context(read_json(policy),proof)
        with self.assertRaises(ValueError): legacy.context(read_json(policy),proof.replace('Improve','Alter'))
        legacy.save_receipt(policy,proof,ctx,report(legacy.binding(proof,ctx)))
        self.marker.write_text(proof)
        legacy.collect(policy,self.marker)
        self.assertTrue(self.marker.exists())

class RunnerTests(unittest.TestCase):
    def test_original_shell_continues_after_two_verified_results(self):
        import os
        import shutil
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=Path(__file__).resolve().parents[2]
            shutil.copytree(source/'sandbox_workflow',root/'sandbox_workflow',ignore=shutil.ignore_patterns('__pycache__','tests'))
            generation=root/'agents/generation'
            (generation/'tests').mkdir(parents=True)
            (generation/'data').mkdir()
            (generation/'data/example.md').write_text('Improve the bound.')
            shutil.copy2(source/'agents/generation/tests/run_example.sh',generation/'tests/run_example.sh')
            binaries=root/'bin';binaries.mkdir()
            codex=binaries/'codex'
            codex.write_text('#!'+sys.executable+'\n'+'''import sys,os,json
from pathlib import Path
if '--version' in sys.argv:
    print('fake-codex');sys.exit(0)
assert 'mcp_servers.reasoning_agent.env_vars=["RETHLAS_POLICY_FILE"]' in sys.argv
sys.path.insert(0,str(Path.cwd().parents[1]))
from sandbox_workflow import legacy
from sandbox_workflow.core import read_json
p=Path(os.environ['RETHLAS_POLICY_FILE']);policy=read_json(p)
n=len(policy['accepted_results'])+2
claim={'statement':f'Bound >= {n}.','improvement':f'Gain {n}.'}
proof=f"# theorem main\\n## statement\\n{claim['statement']}\\n## proof\\nArgument {n}."
ctx=legacy.context(policy,proof,claim); b=legacy.binding(proof,ctx)
r={k:b[k] for k in ('statement_sha256','candidate_sha256')}
r.update(verification_report={'summary':'Checked.','critical_errors':[],'gaps':[]},verdict='correct',repair_hints='',improvement_assessment={'baseline_sha256':ctx['baseline_sha256'],'verdict':'strict_improvement','explanation':'Strict gain.'})
legacy.save_receipt(p,proof,ctx,r)
(p.parent.parent/'blueprint_verified.md').write_text(proof)
print('session id: test-session')
''')
            codex.chmod(0o755)
            curl=binaries/'curl';curl.write_text('#!/bin/sh\nexit 0\n');curl.chmod(0o755)
            env={**os.environ,'PATH':str(binaries)+os.pathsep+os.environ['PATH'],
                 'CODEX_HOME':str(Path.home()/'.codex'),'ITERATIVE_IMPROVEMENT':'1','MAX_ITERATIONS':'2'}
            log=root/'runner.log'
            with log.open('w') as out:
                completed=subprocess.run(['bash',str(generation/'tests/run_example.sh')],env=env,text=True,stdout=out,stderr=subprocess.STDOUT,timeout=15)
            self.assertEqual(completed.returncode,1,log.read_text())
            policy=read_json(generation/'results/example/.research/policy.json')
            self.assertEqual(len(policy['accepted_results']),2)
            self.assertEqual(len(list((generation/'logs').rglob('*iter_*.md'))),2)
            self.assertFalse((generation/'results/example/blueprint_verified.md').exists())
