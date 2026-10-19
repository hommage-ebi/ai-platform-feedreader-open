"""Collection configuration and first-capture policy boundaries."""
import sys,unittest,copy
from pathlib import Path
from pipeline.arxiv.tests import test_pipeline
from pipeline.shared.runtime import digest, read, write
from pipeline.arxiv.common import identity
from pipeline.arxiv.policy import run_policy,paper_policy,load_sources,validate_sources
from pipeline.arxiv.collect import collect
from pipeline.arxiv.split_merge import split,merge,prompt_hash
from pipeline.arxiv.make_tasks import make_tasks

class CapturePolicy(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp();self.root=self.f.root;self.run=self.f.run
    def tearDown(self):self.f.tearDown()
    def new_run(self,name='new'):
        run=self.root/'data/runs/2026-10-18'/name
        write(run/'ledger.json',{'target_date':'2026-10-18','today_at_start':'2026-10-18','stages':{}})
        return run
    def test_sources_define_scope_and_old_retry_keeps_original_scope(self):
        config=self.root/'pipeline/arxiv/config/sources.json';write(config,{'schema_version':1,'categories':['astro-ph','cs.AI']})
        seen=[]
        collect(self.run,'2026-10-18',fetcher=lambda url:(seen.append(url) or self.f.rss()))
        self.assertEqual(seen,['https://rss.arxiv.org/rss/astro-ph','https://rss.arxiv.org/rss/cs.AI'])
        write(config,{'schema_version':1,'categories':['math']});seen.clear()
        collect(self.run,'2026-10-18',fetcher=lambda url:(seen.append(url) or self.f.rss()))
        self.assertEqual(len(seen),2)
        other=self.new_run();seen.clear();collect(other,'2026-10-18',fetcher=lambda url:(seen.append(url) or self.f.rss()))
        self.assertEqual(seen,['https://rss.arxiv.org/rss/math'])
    def test_invalid_sources_block_new_capture_before_network_not_old_run(self):
        run_policy(self.run);config=self.root/'pipeline/arxiv/config/sources.json'
        for categories in ([],['cs.LG','cs.LG'],['../private'],['https://example.com'],[True]):
            write(config,{'schema_version':1,'categories':categories});calls=[]
            with self.assertRaises(ValueError):collect(self.new_run('bad'),'2026-10-18',fetcher=lambda url:calls.append(url))
            self.assertEqual(calls,[])
            collect(self.run,'2026-10-18',fetcher=lambda _:self.f.rss())
    def test_overlap_exclusion_and_new_version_use_different_policies(self):
        paper=test_pipeline.PAPER;write(self.run/'run/candidates.json',[paper]);self.f.classify([paper],decision='exclude')
        old=paper_policy(self.run,paper)
        prompt=self.root/'pipeline/arxiv/prompts/relevance.txt';prompt.write_text(prompt.read_text()+'\nChanged for new work.')
        other=self.new_run();version={**paper,'version':'v2'};fresh={**paper,'id':'2610.99999'}
        write(other/'run/candidates.json',[paper,version,fresh]);split(other);merge(other)
        self.assertEqual(paper_policy(other,paper)['sha256'],old['sha256'])
        self.assertNotEqual(paper_policy(other,version)['sha256'],old['sha256'])
        self.assertNotEqual(paper_policy(other,fresh)['sha256'],old['sha256'])
        self.assertEqual(read(other/'run/classifications.json')[0]['decision'],'exclude')
        tasks=make_tasks(other,'classify');self.assertEqual(sum(len(read(t['input_files'][0])) for t in tasks),2)
    def test_legacy_unknown_policy_blocks_only_overlapping_pending_paper(self):
        paper=test_pipeline.PAPER;write(self.run/'run/candidates.json',[paper])
        other=self.new_run();fresh={**paper,'id':'2610.99999'};write(other/'run/candidates.json',[paper,fresh]);paths=split(other)
        self.assertEqual([p['id'] for rel in paths for p in read(other/rel)],[fresh['id']])
        self.assertIn('policy:'+ '/'.join(identity(paper)),read(other/'ledger.json')['stages'])
    def test_malformed_unrelated_history_isolated_but_overlap_blocks(self):
        paper=test_pipeline.PAPER;write(self.run/'run/candidates.json',[paper])
        (self.run/'run/paper-policies.json').write_text('{broken')
        other=self.new_run();fresh={**paper,'id':'2610.99999'};write(other/'run/candidates.json',[paper,fresh]);paths=split(other)
        self.assertEqual([p['id'] for rel in paths for p in read(other/rel)],[fresh['id']])
    def test_boolean_snapshot_schema_version_rejected_even_with_new_hash(self):
        from pipeline.arxiv.policy import validate_policy
        value=copy.deepcopy(run_policy(self.run));value['schema_version']=True
        value['sha256']=digest({k:v for k,v in value.items() if k!='sha256'})
        with self.assertRaises(ValueError):validate_policy(value)
    def test_missing_snapshot_never_silently_repins_pending_work(self):
        write(self.run/'run/candidates.json',[test_pipeline.PAPER]);split(self.run)
        (self.run/'run/processing-policy.json').unlink()
        with self.assertRaisesRegex(ValueError,'Historical processing policy'):split(self.run)

if __name__=='__main__':unittest.main()
