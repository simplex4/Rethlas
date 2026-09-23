import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from sandbox_workflow import legacy
from sandbox_workflow.core import atomic, read_json, WorkflowError
from sandbox_workflow.tests.test_workflow import report

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('verification_api_test',ROOT/'agents/verification/api/server.py')
api=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api;spec.loader.exec_module(api)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.proof='# theorem main\n## statement\nBound >= 2.\n## proof\nArgument.'
        self.ctx=legacy.context({'mode':'improvement','original_question':'Improve bound.','accepted_results':[]},self.proof,
            {'statement':'Bound >= 2.','improvement':'Gain to 2.'})

    def subprocess(self,cmd,**kwargs):
        directory=Path(cmd[cmd.index('--output-last-message')+1]).parent
        b=read_json(directory/'binding.json')
        r=report({k:b[k] for k in ('statement_sha256','candidate_sha256')})
        r['improvement_assessment']={'baseline_sha256':b['baseline_sha256'],'verdict':'strict_improvement','explanation':'Compared original bounds and whole collection.'}
        atomic(directory/'research_verification.json',r)
        return SimpleNamespace(returncode=0)

    def test_structured_independent_review(self):
        with patch.object(api,'RESULTS_ROOT',self.root),patch.object(api.subprocess,'run',side_effect=self.subprocess) as run:
            r=api.run_context_verification('round',self.proof,self.ctx)
        self.assertEqual(r['improvement_assessment']['verdict'],'strict_improvement')
        cmd=run.call_args.args[0]
        self.assertIn('--output-schema',cmd)
        self.assertNotIn('resume',cmd)
        self.assertIn('improvement_assessment',read_json(self.root/'round/research.schema.json')['properties'])

    def test_stale_baseline_and_changed_fixed_statement_never_launch(self):
        with patch.object(api.subprocess,'run') as run:
            with self.assertRaises(ValueError): api.run_context_verification('bad',self.proof,{**self.ctx,'baseline_sha256':'stale'})
            with self.assertRaises(ValueError): api.run_context_verification('bad',self.proof,{**self.ctx,'mode':'fixed','claim':None})
            run.assert_not_called()

    def test_nonzero_exit_rejects_even_well_formed_report(self):
        def fail(cmd,**kwargs):
            self.subprocess(cmd,**kwargs)
            return SimpleNamespace(returncode=1)
        with patch.object(api,'RESULTS_ROOT',self.root),patch.object(api.subprocess,'run',side_effect=fail):
            with self.assertRaisesRegex(ValueError,'exited 1'): api.run_context_verification('bad',self.proof,self.ctx)

    def test_generation_mcp_forwards_context_and_records_receipt(self):
        spec=importlib.util.spec_from_file_location('generation_mcp_test',ROOT/'agents/generation/mcp/server.py')
        generation=importlib.util.module_from_spec(spec);spec.loader.exec_module(generation)
        policy=self.root/'policy.json'
        atomic(policy,{'mode':'improvement','original_question':self.ctx['original_question'],'accepted_results':[]})
        def post(endpoint,json,timeout):
            self.assertEqual(json['research_context'],self.ctx)
            with patch.object(api,'RESULTS_ROOT',self.root/'reviews'),patch.object(api.subprocess,'run',side_effect=self.subprocess):
                r=api.verify(api.VerifyRequest(**json))
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:r)
        with patch.dict('os.environ',{'RETHLAS_POLICY_FILE':str(policy)}),patch.object(generation.requests,'post',side_effect=post) as request:
            result=generation.verify_proof_service(self.ctx['original_question'],self.proof,improvement=self.ctx['claim'])
            self.assertTrue(result['accepted'])
            self.assertEqual(len(list((self.root/'receipts').glob('*.json'))),1)
            with self.assertRaisesRegex(ValueError,'immutable'):
                generation.verify_proof_service('Changed question',self.proof,improvement=self.ctx['claim'])
            self.assertEqual(request.call_count,1)
