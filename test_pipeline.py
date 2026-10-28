from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import copy,json,os,socket,unittest
import pipeline as p

TESTROOT=Path(__file__).resolve().parent/'test-work';TESTROOT.mkdir(exist_ok=True)
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='case-',dir=TESTROOT);self.root=Path(self.temp.name).resolve()
        assert self.root.is_relative_to(TESTROOT.resolve())
        cfg=p.read(Path(__file__).resolve().parent/'pipeline.json');cfg['scope']['expected_members_per_quarter']=2;p.write(self.root/'pipeline.json',cfg);self.cfg=cfg
        self.groups=[{'id':f'g{i}','label':f'Group {i}','short':f'G{i}','definition':'Fixture'} for i in range(1,13)]
        def company(identity,prom,price):
            rows=[{'quarter':'2026-09-30','group':g,'fully':shares,'name':name,'member':member,'eligibility':'Presumed float','url':'https://www.bseindia.com/fixture'} for g,shares,name,member in [(1,prom,'Promoters','ShareholdingOfPromoterAndPromoterGroupMember'),(2,100-prom,'Mutual funds','MutualFundsOrUTIMember')]]
            return {'identity':identity,'groups':[g['label'] for g in self.groups],'rows':rows,'quarters':[{'q':'2026-09-30','controls_ok':True,'fully':100,'quote':{'close':price}}]}
        self.a=company('A',40,10);self.b=company('B',20,30);self.seed()
    def tearDown(self):
        assert self.root.is_relative_to(TESTROOT.resolve());self.temp.cleanup()
    def put(self,relative,body):
        file=self.root/'data/inputs'/relative;p.atomic(file,body);return {'file':relative,'sha256':p.digest(body),'bytes':len(body)}
    def seed(self,price=100):
        manifest={'companies':[self.put('companies/A.json',p.encoded(self.a)),self.put('companies/B.json',p.encoded(self.b))],'groups':self.put('groups.json',p.encoded(self.groups)),'prices':[self.put('prices/calendar-2026-Q3.json',p.encoded({'data':[{'EOD_TIMESTAMP':'30-SEP-2026','EOD_INDEX_NAME':'NIFTY 50','EOD_CLOSE_INDEX_VAL':price}]}))],'cash_market_fii_dii_included':False}
        p.write(self.root/'data/inputs/manifest.json',manifest);p.write(self.root/'data/bootstrap-report.json',{'stale_company_quarters':[]})
    def candidate(self,result):return p.read(self.root/'data/candidates'/result['candidate_id']/'website-data.json')
    def test_market_cap_weights_and_real_zero_are_preserved(self):
        r=p.stage(self.root);q=self.candidate(r)['quarters'][0]
        self.assertAlmostEqual(q['values'][0],25);self.assertAlmostEqual(q['values'][1],75);self.assertEqual(q['values'][2],0);self.assertEqual(sum(q['values']),100);self.assertFalse(q['estimated'])
    def test_missing_price_rescales_without_inventing_a_zero(self):
        self.b['quarters'][0]['quote']=None;self.seed();r=p.stage(self.root);q=self.candidate(r)['quarters'][0]
        self.assertEqual(q['used'],1);self.assertTrue(q['estimated']);self.assertEqual(q['excluded'],['B']);self.assertEqual(q['values'][:2],[40,60])
    def test_unchanged_inputs_reuse_verified_candidate(self):
        a=p.stage(self.root);b=p.stage(self.root);self.assertEqual(a['candidate_id'],b['candidate_id']);self.assertTrue(b['reused_unchanged_candidate'])
    def test_tampering_keeps_previous_successful_pointer(self):
        p.stage(self.root);pointer=self.root/'data/latest-candidate.json';before=pointer.read_bytes();(self.root/'data/inputs/companies/A.json').write_text('{}')
        with self.assertRaisesRegex(p.PipelineError,'hash mismatch'):p.stage(self.root)
        self.assertEqual(pointer.read_bytes(),before)
    def test_failed_ownership_reconciliation_is_rejected(self):
        self.a['rows'][0]['fully']=39;self.seed()
        with self.assertRaisesRegex(p.PipelineError,'do not reconcile'):p.stage(self.root)
    def test_duplicate_company_quarter_is_rejected(self):
        self.a['quarters'].append(copy.deepcopy(self.a['quarters'][0]));self.seed()
        with self.assertRaisesRegex(p.PipelineError,'Duplicate company-quarter'):p.stage(self.root)
    def test_missing_constituent_does_not_become_smaller_universe(self):
        self.b['quarters']=[];self.seed()
        with self.assertRaisesRegex(p.PipelineError,'Membership coverage'):p.stage(self.root)
    def test_overlapping_runs_are_rejected_without_stealing_lock(self):
        with p.lock(self.root):
            with self.assertRaisesRegex(p.PipelineError,'Another integration run'):p.stage(self.root)
            self.assertTrue((self.root/'data/pipeline.lock').exists())
    def test_input_path_cannot_escape_its_folder(self):
        with self.assertRaisesRegex(p.PipelineError,'escapes'):p.input_path(self.root,'../../outside.json')
    def test_no_network_or_publication_occurs_in_stage(self):
        with patch.object(socket,'socket',side_effect=AssertionError('Network access forbidden')):r=p.stage(self.root)
        self.assertEqual(r['status'],'review_required_not_published');self.assertEqual(r['remote_writes'],0);self.assertEqual(r['network_requests'],0);self.assertEqual(r['new_schedules_created'],0)
    def test_cash_activity_cannot_be_enabled_accidentally(self):
        cfg=copy.deepcopy(self.cfg);cfg['scope']['cash_market_fii_dii']=True;p.write(self.root/'pipeline.json',cfg)
        with self.assertRaisesRegex(p.PipelineError,'excluded'):p.stage(self.root)
    def test_ambiguous_uti_is_flagged_without_guessed_remap(self):
        row={'group':7,'fully':10,'name':'UNIT TRUST OF INDIA','quarter':'2026-09-30','member':'OtherInstitutionsMember','eligibility':'Unresolved','url':'https://www.bseindia.com/fixture'}
        issue=p.classification_review('A',row);self.assertIsNone(issue['proposed_group']);self.assertEqual(issue['issue'],'UTI_ENTITY_IDENTITY_REQUIRES_REVIEW')
    def test_clear_fii_mapping_is_proposed_but_not_applied(self):
        row={'group':7,'fully':10,'name':'FII','quarter':'2026-09-30','member':'OtherInstitutionsForeignMember','eligibility':'Unresolved','url':'https://www.bseindia.com/fixture'}
        issue=p.classification_review('A',row);self.assertEqual(issue['proposed_group'],6);self.assertEqual(row['group'],7)
    def test_pointer_write_failure_keeps_last_successful_candidate(self):
        p.stage(self.root);pointer=self.root/'data/latest-candidate.json';before=pointer.read_bytes();self.seed(price=101);replace=os.replace
        def fail_pointer(src,dst):
            if Path(dst)==pointer:raise OSError('Simulated interrupted publication pointer')
            return replace(src,dst)
        with patch.object(p.os,'replace',side_effect=fail_pointer):
            with self.assertRaises(OSError):p.stage(self.root)
        self.assertEqual(pointer.read_bytes(),before)
if __name__=='__main__':unittest.main(verbosity=2)
