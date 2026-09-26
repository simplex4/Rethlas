import io
from pathlib import Path
import time
import unittest

from sandbox_workflow.core import WorkflowError, atomic, read_json, sha
from sandbox_workflow.research_contract import digest, validate_candidate
from sandbox_workflow.timer import ElapsedTimer, duration
from sandbox_workflow.tests import test_workflow as fixtures


def improving_candidate(label):
    def action(cmd, cwd, env, prompt, events, stderr):
        turn = read_json(cwd / 'turn.json')
        claim = {'statement': f'The bound is at least {label}.', 'improvement': f'Strict gain {label} under the original hypotheses.'}
        proof = f"# theorem main\n## statement\n{claim['statement']}\n## proof\nArgument {turn['attempt']}.\n# Evidence and replay\nNotes.\n".encode()
        atomic(cwd / 'blueprint.md', proof)
        atomic(cwd / 'submission.json', {'run_id': turn['run_id'], 'attempt': turn['attempt'], 'path': 'blueprint.md',
              'sha256': sha(proof), 'claim': claim, 'baseline_sha256': digest(read_json(cwd / 'baseline.json'))})
        return 0
    return action


def improving_review(verdict='strict_improvement', correct=True):
    def action(cmd, cwd, env, prompt, events, stderr):
        b = read_json(cwd / 'binding.json')
        value = fixtures.report({k:b[k] for k in ('statement_sha256','candidate_sha256')}, correct)
        value['improvement_assessment'] = {'baseline_sha256': b['baseline_sha256'], 'verdict': verdict,
                                         'explanation': 'Compared the full domain and hypotheses and checked strict gain.'}
        atomic(Path(cmd[cmd.index('--output-last-message')+1]), value)
        return 0
    return action


class ImprovementTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    workflow = fixtures.WorkflowTests.workflow
    def test_two_improvements_with_nonimprovement_between(self):
        w, runner, rid = self.workflow([improving_candidate(2), improving_review(),
            improving_candidate(2), improving_review('not_improvement'), improving_candidate(3), improving_review()])
        result = w.resume(rid, 3, iterative_improvement=True)
        self.assertEqual(result['accepted_candidates'], [0,2])
        self.assertEqual([c['status'] for c in result['candidates']], ['accepted','not_improved','accepted'])
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['phase'], 'generation')
        self.assertEqual(len(w.baseline(w.path(rid), result)['accepted_results']), 2)
        self.assertIn('at least 3', (w.export(rid)/'blueprint_verified.md').read_text())
        self.assertTrue((w.path(rid)/'candidates/00000/verification.json').exists())
        verifier_calls = [cmd for cmd,_,_ in runner.calls if '--output-schema' in cmd]
        self.assertEqual(len(verifier_calls),3)
        self.assertTrue(all('resume' not in c for c in verifier_calls))

    def test_failed_improvement_retains_exportable_best(self):
        w, _, rid = self.workflow([improving_candidate(2), improving_review(), improving_candidate(3), improving_review('unresolved')])
        m=w.resume(rid,2,iterative_improvement=True)
        self.assertEqual(m['accepted_candidates'],[0])
        self.assertIn('at least 2',(w.export(rid)/'blueprint_verified.md').read_text())

    def test_wrong_proof_cannot_be_promoted_even_with_strict_comparison(self):
        w,_,rid=self.workflow([improving_candidate(2),improving_review(correct=False)])
        m=w.resume(rid,1,iterative_improvement=True)
        self.assertEqual(m['accepted_candidates'],[])
        with self.assertRaises(WorkflowError): w.export(rid)

    def test_fixed_statement_rejects_changed_claim_and_allows_appendix(self):
        original='Original fixed theorem.'
        p='# theorem main\n## statement\n'+original+'\n## proof\nProof.\n# Evidence and replay\nNotes.'
        self.assertEqual(validate_candidate(p, original), original)
        with self.assertRaisesRegex(ValueError,'equal the original'):
            validate_candidate(p.replace(original,'Changed theorem.'), original)
        with self.assertRaisesRegex(ValueError,'equal the original'):
            validate_candidate(p.replace(original,original+'\nExtra result.'), original)

    def test_resume_accepted_fixed_run_requires_explicit_opt_in(self):
        w,runner,rid=self.workflow(['candidate','correct',improving_candidate(2),improving_review()])
        w.resume(rid,1)
        self.assertEqual(len(runner.calls),2)
        self.assertEqual(w.resume(rid,1)['status'],'accepted')
        m=w.resume(rid,1,iterative_improvement=True)
        self.assertEqual(m['accepted_candidates'],[0,1])

    def test_missing_assessment_blocks_promotion(self):
        def missing(cmd,cwd,env,prompt,events,stderr):
            b=read_json(cwd/'binding.json')
            atomic(Path(cmd[cmd.index('--output-last-message')+1]),fixtures.report({k:b[k] for k in ('statement_sha256','candidate_sha256')}))
            return 0
        w,_,rid=self.workflow([improving_candidate(2),missing])
        with self.assertRaises(WorkflowError): w.resume(rid,1,iterative_improvement=True)
        self.assertEqual(w.load(rid)[1]['accepted_candidates'],[])

    def test_changed_baseline_blocks_promotion(self):
        def tamper(cmd,cwd,env,prompt,events,stderr):
            improving_review()(cmd,cwd,env,prompt,events,stderr)
            atomic(cwd/'baseline.json',{'accepted_results':[],'original_question':'Altered'})
            return 0
        w,_,rid=self.workflow([improving_candidate(2),tamper])
        with self.assertRaisesRegex(WorkflowError,'baseline changed'): w.resume(rid,1,iterative_improvement=True)

    def test_changed_earlier_accepted_proof_blocks_future_baseline(self):
        w,_,rid=self.workflow([improving_candidate(2),improving_review(),improving_candidate(3),improving_review()])
        w.resume(rid,2,iterative_improvement=True)
        (w.path(rid)/'candidates/00000/candidate.md').write_text('Changed old proof')
        with self.assertRaisesRegex(WorkflowError,'baseline proof changed'): w.export(rid)

    def test_pause_after_acceptance_preserves_result_and_resume(self):
        def pause(cmd,cwd,env,prompt,events,stderr):
            improving_review()(cmd,cwd,env,prompt,events,stderr)
            (cwd.parents[1]/'PAUSE_AFTER_TURN').write_text('Pause')
            return 0
        w,_,rid=self.workflow([improving_candidate(2),pause,improving_candidate(3),improving_review()])
        m=w.resume(rid,2,iterative_improvement=True)
        self.assertEqual(m['status'],'paused')
        self.assertEqual(m['accepted_candidates'],[0])
        m=w.resume(rid,1,clear_pause=True)
        self.assertEqual(m['accepted_candidates'],[0,1])


class TimerTests(unittest.TestCase):
    def test_timer_prints_total_and_stops_on_exception(self):
        stream=io.StringIO()
        timer_values=iter([0,3661])
        timer=ElapsedTimer(interval=100,stream=stream,clock=lambda: next(timer_values))
        with self.assertRaises(ValueError):
            with timer: raise ValueError('test')
        self.assertFalse(timer.thread.is_alive())
        self.assertIn('Total time: 01:01:01',stream.getvalue())
        self.assertEqual(duration(30),'00:00:30')

    def test_periodic_timer_output(self):
        import threading
        tick=threading.Event()
        class Stream(io.StringIO):
            def write(self,text):
                result=super().write(text)
                if 'still running' in text: tick.set()
                return result
        stream=Stream()
        with ElapsedTimer(interval=0.001,stream=stream):
            self.assertTrue(tick.wait(1))
            time.sleep(0.005)
        output=stream.getvalue()
        self.assertIn('\r  [elapsed 00:00:00] still running',output)
        self.assertGreaterEqual(output.count('\r'),2)
        self.assertNotIn('still running...\n  [elapsed',output)
        self.assertEqual(output.count('\n'),2)
