"""Storage reductions preserve classification, recovery and article provenance."""
import contextlib,io,json,shutil,sys,unittest
from unittest.mock import patch
from pipeline.arxiv.tests import test_pipeline
from pipeline.arxiv.common import identity,recompute_completion
from pipeline.arxiv.collect import collect
from pipeline.arxiv.inputs import compact,compact_run,cleanup_inputs,input_hash,pool_path
from pipeline.arxiv.policy import run_policy,paper_policy,bind_papers
from pipeline.arxiv.split_merge import split,merge
from pipeline.arxiv.make_tasks import make_tasks,claim,finish,release
from pipeline.arxiv.assemble import assemble_one,validate_output
from pipeline.shared.runtime import digest,read,write
PAPER=test_pipeline.PAPER

class CompactRuns(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp();self.root=self.f.root;self.run=self.f.run
    def tearDown(self):self.f.tearDown()
    def collect(self):
        item=f'<item><title>{PAPER["title"]}</title><link>{PAPER["source_url"]}</link><description>{PAPER["abstract"]}</description></item>'
        return collect(self.run,'2026-10-18',fetcher=lambda _:self.f.rss(item))
    def classify(self,decision='exclude'):
        split(self.run)
        for task in make_tasks(self.run,'classify'):
            token=claim(self.root,task,'synthetic-test')
            write(task['output_files'][0],[{'id':p['id'],'version':p['version'],'decision':decision,'topics':[] if decision=='exclude' else ['3'],'reason':'Synthetic evidence'} for p in read(task['input_files'][0])])
            finish(self.root,task,token)
        return merge(self.run)
    def test_collect_classify_cleanup_and_cache_loss_keep_completed_evidence(self):
        rows=self.collect();self.assertEqual(rows,[compact({k:v for k,v in PAPER.items() if k!='topics'})])
        self.classify();self.assertEqual(recompute_completion(self.run),'complete')
        cleanup_inputs(self.run)
        self.assertFalse(pool_path(self.run).exists());self.assertFalse((self.run/'.cache/groups').exists())
        self.assertFalse((self.run/'run/groups').exists())
        shutil.rmtree(self.run/'.cache',ignore_errors=True);shutil.rmtree(self.root/'.cache',ignore_errors=True)
        self.assertEqual(split(self.run),[]);self.assertEqual(merge(self.run),[])
        self.assertEqual(recompute_completion(self.run),'complete')
        binding=read(self.run/'run/paper-policies.json')[PAPER['id']+'/v1']
        self.assertEqual(set(binding),{'input_sha256','policy_sha256'})
        self.assertEqual(binding['policy_sha256'],run_policy(self.run)['sha256'])
        self.assertFalse((self.run/'run/policies').exists())
    def test_selected_rows_have_no_title_abstract_and_articles_need_no_summary_ja(self):
        self.collect();selected=self.classify('include');self.assertNotIn('title',selected[0]);self.assertNotIn('abstract',selected[0])
        directory=self.f.fixture();result=read(directory/'result.json');self.assertNotIn('summary_ja',result)
        validate_output(directory);article=assemble_one(self.run,selected[0]);self.assertNotIn('summary_ja',article)
        self.assertEqual(article['lead_ja'],result['lead_ja']);self.assertEqual(article['title'],PAPER['title'])
        cleanup_inputs(self.run);self.assertFalse(pool_path(self.run).exists())
        self.assertEqual(merge(self.run),selected)
    def test_pending_inputs_survive_cleanup_and_missing_cache_blocks_reclassification(self):
        self.collect();split(self.run);cleanup_inputs(self.run)
        self.assertTrue(pool_path(self.run).is_file());self.assertTrue(split(self.run))
        shutil.rmtree(self.run/'.cache')
        with self.assertRaisesRegex(ValueError,'Temporary classification input unavailable'):split(self.run)
        self.assertEqual(recompute_completion(self.run),'incomplete')
    def test_cleanup_refuses_to_delete_active_inputs(self):
        self.collect();split(self.run);task=make_tasks(self.run,'classify')[0];token=claim(self.root,task,'active')
        with self.assertRaisesRegex(ValueError,'model task is running'):cleanup_inputs(self.run)
        self.assertTrue(pool_path(self.run).exists());self.assertTrue(read(task['input_files'][0]))
        release(self.root,task['cache_key'],token)
    def test_legacy_candidate_compaction_preserves_verified_collection_capture(self):
        self.collect();lean=read(self.run/'run/candidates.json');verbose=[{**lean[0],**PAPER}];write(self.run/'run/candidates.json',verbose)
        capture=read(self.run/'run/collection-capture.json');capture['candidates_sha256']=digest(verbose);write(self.run/'run/collection-capture.json',capture)
        ledger=read(self.run/'ledger.json');ledger['stages']['collect']['details']['capture_sha256']=digest(capture);write(self.run/'ledger.json',ledger)
        compact_run(self.run);self.classify();self.assertEqual(recompute_completion(self.run),'complete')
    def test_legacy_embedded_policy_migrates_and_imported_policy_is_stored_once(self):
        write(self.run/'run/candidates.json',[PAPER]);bind_papers(self.run,[PAPER]);original=paper_policy(self.run,PAPER)
        mapping=read(self.run/'run/paper-policies.json');mapping[PAPER['id']+'/v1']={'input_sha256':input_hash(PAPER),'policy':original};write(self.run/'run/paper-policies.json',mapping)
        bind_papers(self.run,[PAPER]);self.assertNotIn('policy',read(self.run/'run/paper-policies.json')[PAPER['id']+'/v1'])
        second={**PAPER,'id':'2610.54321'};write(self.run/'run/candidates.json',[PAPER,second]);bind_papers(self.run,[second])
        prompt=self.root/'pipeline/arxiv/prompts/summary.txt';prompt.write_text(prompt.read_text()+'\nNew policy.')
        other=self.run.parent/'other';write(other/'ledger.json',{'stages':{}});write(other/'run/candidates.json',[PAPER,second]);split(other)
        self.assertEqual(len(list((other/'run/policies').glob('*.json'))),1)
        for p in (PAPER,second):self.assertEqual(paper_policy(other,p),original)
    def test_cleanup_cli_removes_legacy_groups_and_task_descriptors(self):
        self.collect();self.classify();write(self.run/'run/groups/legacy.json',[PAPER])
        from pipeline.arxiv.workflow import cli
        out=io.StringIO()
        with patch.object(sys,'argv',['workflow.py','cleanup','--run-dir',str(self.run)]),contextlib.redirect_stdout(out):cli()
        self.assertFalse((self.run/'run/groups').exists());self.assertFalse((self.run/'run/classifier_tasks.json').exists())
        self.assertEqual(recompute_completion(self.run),'complete')
    def test_compact_hash_and_transient_group_tampering_still_fail(self):
        self.collect();split(self.run);task=make_tasks(self.run,'classify')[0]
        write(task['input_files'][0],[{**PAPER,'abstract':'Tampered abstract'}])
        with self.assertRaisesRegex(ValueError,'inputs or prompt changed'):claim(self.root,task,'tampered')
        with self.assertRaises(ValueError):input_hash({**PAPER,'input_sha256':'0'*64})

if __name__=='__main__':unittest.main()
