"""Model identity is historical provenance; concurrency is a live safety cap."""
import sys
import unittest
from pathlib import Path
from pipeline.arxiv.tests import test_pipeline
from pipeline.shared.runtime import read, write
from pipeline.arxiv.common import paper_path
from pipeline.arxiv.make_tasks import make_tasks,claim,finish,release,durable_claims_path
from pipeline.arxiv.split_merge import split,merge

class AgentConfig(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp();self.root=self.f.root;self.run=self.f.run
        self.path=self.root/'pipeline/config/agents.json';self.config=read(self.path)
    def tearDown(self):self.f.tearDown()
    def save(self):write(self.path,self.config)
    def classifier(self,run,paper):
        write(run/'ledger.json',{'stages':{}});write(run/'run/candidates.json',[paper]);split(run)
        return make_tasks(run,'classify')[0]
    def output(self,task,paper):
        write(task['output_files'][0],[{'id':paper['id'],'version':paper['version'],'decision':'include','topics':['3'],'reason':'Synthetic evidence'}])
    def new_paper(self,n):
        return {**test_pipeline.PAPER,'id':f'2610.{40000+n}','source_url':f'https://arxiv.org/abs/2610.{40000+n}v1'}
    def test_model_and_effort_pinned_for_pending_and_completed_work(self):
        self.config['classify']={'model':'gpt-6-sol','reasoning_effort':'medium'};self.save()
        paper=test_pipeline.PAPER;task=self.classifier(self.run,paper);token=claim(self.root,task,'old-policy')
        self.assertEqual((task['model'],task['reasoning_effort']),('gpt-6-sol','medium'))
        self.config['classify']={'model':'gpt-6.1-sol','reasoning_effort':'high'};self.save()
        self.output(task,paper);finish(self.root,task,token);self.assertEqual(merge(self.run)[0]['decision'],'include')
        proof=read(self.run/'run/classification-proofs.json')[paper['id']+'/v1']
        self.assertEqual((proof['model'],proof['reasoning_effort']),('gpt-6-sol','medium'))
        self.assertEqual(make_tasks(self.run,'classify'),[])
        fresh=self.classifier(self.run.parent/'new-policy',self.new_paper(1))
        self.assertEqual((fresh['model'],fresh['reasoning_effort']),('gpt-6.1-sol','high'))
        self.assertNotEqual(fresh['cache_key'],task['cache_key'])
        repeat=self.run.parent/'repeat';write(repeat/'ledger.json',{'stages':{}});write(repeat/'run/candidates.json',[paper])
        self.assertEqual(split(repeat),[]);self.assertEqual(merge(repeat)[0]['decision'],'include')
        self.assertEqual(read(repeat/'run/classification-proofs.json')[paper['id']+'/v1'],proof)
    def test_summary_settings_stay_pinned_after_defaults_change(self):
        self.config['summary']={'model':'gpt-6-sol','reasoning_effort':'medium'};self.save()
        paper=test_pipeline.PAPER;task=self.classifier(self.run,paper);token=claim(self.root,task,'classifier')
        self.output(task,paper);finish(self.root,task,token);merge(self.run)
        from pipeline.arxiv.prepare import prepare_one
        body=('<article>'+('Synthetic paper. '*80)+'</article>').encode()
        prepare_one(self.run,paper,lambda url:(body,'text/html',url))
        summary=make_tasks(self.run,'summary')[0];token=claim(self.root,summary,'summary')
        self.assertEqual((summary['model'],summary['reasoning_effort']),('gpt-6-sol','medium'))
        self.config['summary']={'model':'gpt-6.1-sol','reasoning_effort':'high'};self.save()
        directory=paper_path(self.run,paper)
        write(directory/'result.json',{'id':paper['id'],'version':paper['version'],'title_ja':'検証','summary_ja':'合成した検証','lead_ja':['問い','方法','結果'],'source_url':paper['source_url'],'summary_file':'summary.md','figures':[]})
        (directory/'summary.md').write_text('## 検証\n\n合成した説明です。')
        finish(self.root,summary,token)
        self.assertEqual(make_tasks(self.run,'summary'),[])
        self.assertEqual(read(directory/'model-output-receipt.json')['cache_key'],summary['cache_key'])
    def test_model_proof_tampering_is_not_reused(self):
        paper=test_pipeline.PAPER;task=self.classifier(self.run,paper);token=claim(self.root,task,'classifier')
        self.output(task,paper);finish(self.root,task,token);merge(self.run)
        path=self.run/'run/classification-proofs.json';proofs=read(path)
        proofs[paper['id']+'/v1']['model']='unattested-model';write(path,proofs)
        from pipeline.arxiv.split_merge import validate_current_selection
        with self.assertRaises(ValueError):validate_current_selection(self.run,paper)

    def test_live_cap_counts_old_claims_and_does_not_prevent_finishing(self):
        self.config['max_parallel']=2;self.save()
        papers=[self.new_paper(i) for i in range(3)]
        tasks=[self.classifier(self.run.parent/f'capacity-{i}',p) for i,p in enumerate(papers)]
        tokens=[claim(self.root,tasks[i],'old-policy') for i in range(2)]
        with self.assertRaises(RuntimeError):claim(self.root,tasks[2],'full')
        self.config['max_parallel']=1;self.save()
        self.assertEqual(len(read(durable_claims_path(self.root))),2)
        self.output(tasks[0],papers[0]);finish(self.root,tasks[0],tokens[0])
        with self.assertRaises(RuntimeError):claim(self.root,tasks[2],'still-full')
        release(self.root,tasks[1]['cache_key'],tokens[1])
        token=claim(self.root,tasks[2],'capacity-available')
        self.assertEqual(len(read(durable_claims_path(self.root))),1)
        release(self.root,tasks[2]['cache_key'],token)

if __name__=='__main__':unittest.main()
