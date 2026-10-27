import sys,shutil,datetime as dt,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline.arxiv.tests import test_pipeline
PAPER=test_pipeline.PAPER
from pipeline.shared.runtime import read, record, write
from pipeline.arxiv.common import recompute_completion
from pipeline.arxiv.split_merge import split,merge
from pipeline.arxiv.assemble import assemble_one
from pipeline.arxiv.reader_adapter import adapt
from pipeline.arxiv.collect import collect
from pipeline.arxiv.workflow import start
class Lifecycle(unittest.TestCase):
 def setUp(self):self.f=test_pipeline.PipelineTests();self.f.setUp();self.root=self.f.root;self.run=self.f.run
 def tearDown(self):self.f.tearDown()
 def finish_published(self):
  write(self.run/'run/candidates.json',[PAPER]);paths=split(self.run);g=self.run/paths[0]
  write(g.with_name(g.stem+'.result.json'),[{'id':PAPER['id'],'version':'v1','decision':'include','topics':['3'],'reason':'Synthetic test'}]);self.f.finish_classifiers();merge(self.run)
  self.f.fixture();record(self.run,'collect','complete');a=assemble_one(self.run,PAPER);adapt(self.run/'site',self.run,self.root/'data/articles')
  write(self.run/'release.json',{'status':'succeeded','articles':[{'article_id':PAPER['id'],'version':'v1','content_sha256':a['content_sha256']}]});self.assertEqual(recompute_completion(self.run),'complete');return a
 def invoke(self,day='2026-10-19',items=''):
  date=dt.datetime.fromisoformat(day+'T01:00:00+00:00');rss=f'<rss><channel><lastBuildDate>{date.strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>{items}</channel></rss>'.encode()
  calls=[]
  def capture(run,target,**kwargs):calls.append(target);return collect(run,target,fetcher=lambda url:rss,**kwargs)
  with patch('pipeline.arxiv.collect.collect',side_effect=capture):plan=start(self.root,date)
  self.assertEqual(calls,[day]);return plan
 def test_fresh_today_empty_is_real_empty(self):
  shutil.rmtree(self.root/'data/runs');plan=self.invoke();run=Path(plan['today']);self.assertEqual(read(run/'run/candidates.json'),[]);self.assertEqual(recompute_completion(run),'complete');self.assertEqual(plan['backlog'],[])
 def test_same_day_complete_then_append_reopens_only_new_work(self):
  self.finish_published();item='<item><title>New materials paper</title><link>https://arxiv.org/abs/2610.54321v1</link><description>New abstract</description></item>'
  plan=self.invoke('2026-10-18',item);self.assertEqual(Path(plan['today']),self.run);self.assertEqual(len(read(self.run/'run/candidates.json')),2);self.assertEqual(recompute_completion(self.run),'incomplete');self.assertEqual(read(self.run/'ledger.json')['pending'],[{'id':'2610.54321','version':'v1','stage':'classify'}])
 def test_past_published_prompt_changes_remain_immutable(self):
  a=self.finish_published();before=(self.root/'data/articles'/a['html_path']).read_bytes()
  for name in ('relevance.txt','summary.txt'):
   with (self.root/'pipeline/arxiv/prompts'/name).open('a') as f:f.write('\nChanged prompt')
  self.assertEqual(split(self.run),[]);self.assertEqual(recompute_completion(self.run),'complete');self.assertEqual(self.invoke()['backlog'],[]);self.assertEqual((self.root/'data/articles'/a['html_path']).read_bytes(),before)
 def test_past_published_without_cache_or_site(self):
  self.finish_published();shutil.rmtree(self.run/'site');shutil.rmtree(self.root/'.cache',ignore_errors=True);self.assertEqual(recompute_completion(self.run),'complete');self.assertEqual(self.invoke()['backlog'],[])
 def test_missing_candidates_is_blocked_not_empty(self):
  record(self.run,'collect','complete',{'candidate_count':5});self.assertEqual(recompute_completion(self.run),'incomplete');plan=self.invoke();self.assertEqual(len(plan['backlog']),1);self.assertEqual(plan['backlog'][0]['status'],'blocked')
 def test_corrupt_candidate_backlog_does_not_block_today(self):
  write(self.run/'run/candidates.json',{'invalid':'not array'});plan=self.invoke();self.assertEqual(plan['backlog'][0]['status'],'blocked');self.assertTrue((self.run/'failures.jsonl').exists());self.assertEqual(read(Path(plan['today'])/'run/candidates.json'),[])
 def test_corrupt_ledger_backlog_does_not_block_today(self):
  (self.run/'ledger.json').write_text('{broken');plan=self.invoke();self.assertEqual(plan['backlog'][0]['status'],'blocked');self.assertEqual((self.run/'ledger.json').read_text(),'{broken')
 def test_unpublished_prompt_change_preserves_classification(self):
  self.finish_published();(self.run/'release.json').unlink();recompute_completion(self.run)
  with (self.root/'pipeline/arxiv/prompts/relevance.txt').open('a') as f:f.write('\nChanged prompt')
  self.assertEqual(split(self.run),[]);self.assertEqual(recompute_completion(self.run),'incomplete');self.assertEqual(read(self.run/'ledger.json')['pending'][0]['stage'],'publication')
 def test_mixed_generic_collection_does_not_break_arxiv(self):
  self.finish_published();path=self.root/'data/articles/papers.json';write(path,[*read(path),{'id':'blog:hello','source':'blog','version':'current','title':'Generic','html_path':'papers/blog.html','stored_images':[]}]);self.assertEqual(recompute_completion(self.run),'complete')
if __name__=='__main__':unittest.main(verbosity=2)
