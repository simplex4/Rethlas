import tempfile
import unittest
from pathlib import Path
from chatgpt_workflow.store import Store


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name))
        self.store.create_problem('bound','Improve the bound.',iterative_improvement=True)

    def submit(self,n):
        ctx=self.store.context('bound')
        claim={'statement':f'Bound >= {n}.','improvement':f'Strict gain to {n}.'}
        items=[{'item_id':'main','kind':'theorem','statement':claim['statement'],'proof':f'Argument {n}.'}]
        return self.store.submit_candidate('bound',items,ctx['latest_candidate'],claim,ctx['baseline_sha256'])

    def review(self,c,verdict='strict_improvement',correct=True):
        review=self.store.begin_review(c['candidate_id'])
        vid=review['verification_id']
        self.store.record_review_checks(vid,c['candidate_sha256'],[{'item_id':'main','assessment':'Checked proof.',
            'gaps':[] if correct else [{'location':'main','issue':'Missing step.'}]}])
        candidate=self.store.read_candidate(c['candidate_id'])
        assessment={'baseline_sha256':candidate['baseline_sha256'],'verdict':verdict,'explanation':'Checked strict gain with identical hypotheses.'}
        return self.store.submit_review(vid,c['candidate_sha256'],'Checked all items.','' if correct else 'Repair missing step.',assessment)

    def test_two_improvements_and_rejected_intermediate(self):
        first=self.submit(2)
        self.assertTrue(self.review(first)['accepted'])
        self.assertEqual(self.store.context('bound')['state'],'IMPROVING')
        original_export=self.store.export_accepted('bound')
        second=self.submit(2)
        self.assertFalse(self.review(second,'not_improvement')['accepted'])
        self.assertEqual(self.store.export_accepted('bound')['candidate_id'],first['candidate_id'])
        third=self.submit(3)
        self.assertTrue(self.review(third)['accepted'])
        self.assertEqual(len(self.store.context('bound')['baseline']['accepted_results']),2)
        self.assertNotEqual(original_export['files'],self.store.export_accepted('bound')['files'])
        self.assertEqual(self.store.export_accepted('bound',first['candidate_id']),original_export)

    def test_wrong_proof_or_unresolved_comparison_not_promoted(self):
        self.assertFalse(self.review(self.submit(2),correct=False)['accepted'])
        self.assertFalse(self.review(self.submit(3),'unresolved')['accepted'])
        with self.assertRaises(ValueError): self.store.export_accepted('bound')

    def test_missing_and_stale_comparison_rejected(self):
        c=self.submit(2)
        r=self.store.begin_review(c['candidate_id'])
        self.store.record_review_checks(r['verification_id'],c['candidate_sha256'],[{'item_id':'main','assessment':'Checked.'}])
        for a in (None,{'baseline_sha256':'stale','verdict':'strict_improvement','explanation':'Gain.'}):
            with self.assertRaises(ValueError): self.store.submit_review(r['verification_id'],c['candidate_sha256'],'Checked.',improvement_assessment=a)
        self.assertEqual(self.store.context('bound')['state'],'REVIEWING')

    def test_fixed_unchanged_and_terminal(self):
        self.store.create_problem('fixed','Original theorem.')
        item={'item_id':'main','kind':'theorem','statement':'Changed theorem.','proof':'Argument.'}
        with self.assertRaises(ValueError): self.store.submit_candidate('fixed',[item])
        item['statement']='Original theorem.'
        c=self.store.submit_candidate('fixed',[item]); r=self.store.begin_review(c['candidate_id'])
        self.store.record_review_checks(r['verification_id'],c['candidate_sha256'],[{'item_id':'main','assessment':'Checked.'}])
        self.store.submit_review(r['verification_id'],c['candidate_sha256'],'Checked.')
        self.assertEqual(self.store.context('fixed')['state'],'ACCEPTED')
        with self.assertRaises(ValueError): self.store.create_problem('fixed','Original theorem.',iterative_improvement=True)

    def test_stale_submission_rejected_but_exact_replay_allowed(self):
        ctx=self.store.context('bound'); c=self.submit(2); self.review(c)
        claim={'statement':'Bound >= 2.','improvement':'Strict gain to 2.'}
        items=[{'item_id':'main','kind':'theorem','statement':claim['statement'],'proof':'Argument 2.'}]
        replay=self.store.submit_candidate('bound',items,None,claim,ctx['baseline_sha256'])
        self.assertTrue(replay['replayed'])
        items[0]['proof']='Another argument.'
        with self.assertRaisesRegex(ValueError,'Stale'): self.store.submit_candidate('bound',items,c['candidate_id'],claim,ctx['baseline_sha256'])

    def test_export_authorization_with_optional_candidate(self):
        from chatgpt_workflow.access import Access
        access=Access(self.store)
        key=access.issue('bound','verification')['access_key']
        access.authorize('export_accepted',key,{'problem_id':'bound','candidate_id':None})
        own=self.submit(2)
        access.authorize('export_accepted',key,{'problem_id':'bound','candidate_id':own['candidate_id']})
        self.store.create_problem('other','Other theorem.')
        other=self.store.submit_candidate('other',[{'item_id':'main','kind':'theorem','statement':'Other theorem.','proof':'Argument.'}])
        with self.assertRaises(PermissionError): access.authorize('export_accepted',key,{'problem_id':'bound','candidate_id':other['candidate_id']})
