"""Adversarial boundaries: other sources, damaged history and RSS recapture."""
import datetime as dt
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from pipeline.arxiv.tests import test_pipeline
from pipeline.shared.runtime import read, write
from pipeline.arxiv.assemble import assemble_one
from pipeline.arxiv.reader_adapter import adapt
from pipeline.arxiv.split_merge import split
from pipeline.arxiv.make_tasks import make_tasks,claim,release,state_dir,active_claims,durable_claims_path
from pipeline.arxiv.collect import collect
from pipeline.arxiv.policy import load_sources
from pipeline.arxiv.workflow import start
PAPER=test_pipeline.PAPER

class RunIsolation(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp();self.root=self.f.root;self.run=self.f.run;self.feeds=['https://rss.arxiv.org/rss/'+x for x in load_sources(self.root)['categories']]
    def tearDown(self):self.f.tearDown()
    def task(self):
        write(self.run/'run/candidates.json',[PAPER]);split(self.run)
        return make_tasks(self.run,'classify')[0]
    def corrupt_old(self):
        old=self.root/'data/runs/2026-10-17/broken';old.mkdir(parents=True)
        (old/'ledger.json').write_text('{broken');return old
    def test_generic_collection_does_not_block_new_arxiv_assembly_or_import(self):
        dest=self.root/'data/articles';dest.mkdir(parents=True)
        generic={'id':'blog:hello','source':'blog','version':'current','title':'Generic','html_path':'papers/blog.html','stored_images':[]}
        write(dest/'papers.json',[generic]);write(dest/'images-manifest.json',{'schema_version':1,'images':[]})
        (dest/'papers').mkdir();(dest/'papers/blog.html').write_text('<p>Original blog</p>')
        write(self.run/'run/candidates.json',[PAPER]);self.f.classify([PAPER]);self.f.fixture()
        article=assemble_one(self.run,PAPER);self.assertEqual(article['id'],PAPER['id'])
        result=adapt(self.run/'site',self.run,dest);self.assertEqual(result['failures'],[])
        saved=next(a for a in read(dest/'papers.json') if a['id']=='blog:hello')
        self.assertEqual(saved['versionHistory'],[generic]);self.assertEqual((dest/'papers/blog.html').read_text(),'<p>Original blog</p>')
        shutil.rmtree(self.run/'site')
        self.assertEqual(assemble_one(self.run,PAPER)['content_sha256'],article['content_sha256'])
    def test_corrupt_old_ledger_is_isolated_with_authoritative_registry(self):
        task=self.task();write(durable_claims_path(self.root),{});old=self.corrupt_old()
        token=claim(self.root,task,'current-worker')
        self.assertEqual(read(durable_claims_path(self.root))[task['cache_key']]['token'],token)
        blocked=read(state_dir(self.root)/'blocked-runs.json')['runs'];self.assertEqual(blocked[0]['run_dir'],str(old))
        self.assertEqual((old/'ledger.json').read_text(),'{broken');release(self.root,task['cache_key'],token)
    def test_corrupt_owner_does_not_drop_reservation_or_global_limit(self):
        write(self.run/'run/candidates.json',[{**PAPER,'id':f'2610.{20000+i}'} for i in range(6)]);split(self.run);tasks=make_tasks(self.run,'classify');tokens=[]
        for candidate in tasks:
            tokens.append(claim(self.root,candidate,'worker'))
        (self.run/'ledger.json').write_text('{broken');shutil.rmtree(state_dir(self.root))
        claims=active_claims(self.root,state_dir(self.root));self.assertEqual(len(claims),6)
        other=self.root/'data/runs/2026-10-18/healthy';other.mkdir(parents=True);write(other/'ledger.json',{'stages':{}})
        write(other/'run/candidates.json',[{**PAPER,'id':'2610.30000'}]);split(other);seventh=make_tasks(other,'classify')[0]
        with self.assertRaisesRegex(RuntimeError,'concurrency limit'):claim(self.root,seventh,'worker')
    def test_corrupt_requested_run_cannot_claim(self):
        task=self.task();write(durable_claims_path(self.root),{});(self.run/'ledger.json').write_text('{broken')
        with self.assertRaises(ValueError):claim(self.root,task,'worker')
        self.assertEqual(read(durable_claims_path(self.root)),{})
    def test_missing_registries_and_corrupt_mirror_require_recovery(self):
        task=self.task();self.corrupt_old()
        with self.assertRaisesRegex(RuntimeError,'capacity'):claim(self.root,task,'worker')
    def test_conflicting_mirror_cannot_overwrite_or_release_reservation(self):
        task=self.task();token=claim(self.root,task,'worker')
        other=self.root/'data/runs/2026-10-17/other';other.mkdir(parents=True)
        write(other/'ledger.json',{'model_claims':{task['cache_key']:{'status':'running','token':'different','target_keys':['other']}}})
        claims=active_claims(self.root,state_dir(self.root));self.assertEqual(len(claims),2)
        self.assertEqual(claims[task['cache_key']]['token'],token);self.assertTrue(claims[task['cache_key']]['conflict'])
        with self.assertRaisesRegex(ValueError,'reconciliation'):release(self.root,task['cache_key'],token)
    def test_saved_empty_capture_survives_refetch_failure_and_next_day(self):
        collect(self.run,'2026-10-18',fetcher=lambda _:self.f.rss())
        collect(self.run,'2026-10-18',fetcher=lambda _:(_ for _ in ()).throw(OSError('Network down')))
        ledger=read(self.run/'ledger.json');self.assertEqual(ledger['stages']['collect_attempt']['status'],'failed')
        self.assertEqual(ledger['stages']['collect']['status'],'complete');self.assertEqual(ledger['status'],'complete');self.assertEqual(ledger['pending'],[])
        rss=self.f.rss(date='Mon, 19 Oct 2026 03:00:00 GMT')
        with patch('pipeline.arxiv.collect.collect',side_effect=lambda run,date:collect(run,date,fetcher=lambda _:rss)):
            plan=start(self.root,dt.datetime.fromisoformat('2026-10-19T03:00:00+00:00'))
        self.assertEqual(plan['backlog'],[])
    def test_first_failed_capture_has_actionable_collect_pending(self):
        collect(self.run,'2026-10-18',fetcher=lambda _:(_ for _ in ()).throw(OSError('Network down')))
        ledger=read(self.run/'ledger.json');self.assertEqual(ledger['status'],'incomplete')
        self.assertEqual([x['stage'] for x in ledger['pending']],['collect'])
    def test_partial_feed_coverage_is_not_fabricated(self):
        def partial(url):
            if url==self.feeds[0]:return self.f.rss()
            raise OSError('Unavailable')
        collect(self.run,'2026-10-18',fetcher=partial)
        self.assertEqual(read(self.run/'ledger.json')['status'],'incomplete')
        def remaining(url):
            if url!=self.feeds[0]:return self.f.rss()
            raise OSError('Previously captured feed unavailable')
        collect(self.run,'2026-10-18',fetcher=remaining)
        self.assertEqual(read(self.run/'ledger.json')['status'],'complete')
        self.assertEqual(read(self.run/'ledger.json')['stages']['collect_attempt']['status'],'failed')
    def test_changed_saved_candidates_invalidate_prior_coverage(self):
        collect(self.run,'2026-10-18',fetcher=lambda _:self.f.rss())
        write(self.run/'run/candidates.json',[PAPER]);self.f.classify([PAPER],'exclude')
        collect(self.run,'2026-10-18',fetcher=lambda _:(_ for _ in ()).throw(OSError('Network down')))
        self.assertEqual(read(self.run/'ledger.json')['status'],'incomplete')
    def test_input_conflict_is_not_hidden_by_retained_capture(self):
        def item(title):return '<item><title>'+title+'</title><link>https://arxiv.org/abs/2610.12345v1</link><description>Abstract</description></item>'
        collect(self.run,'2026-10-18',fetcher=lambda _:self.f.rss(item('Original')))
        collect(self.run,'2026-10-18',fetcher=lambda _:self.f.rss(item('Conflicting')))
        self.assertEqual(read(self.run/'ledger.json')['stages']['collect']['status'],'failed')
        collect(self.run,'2026-10-18',fetcher=lambda _:(_ for _ in ()).throw(OSError('Network down')))
        self.assertEqual(read(self.run/'ledger.json')['stages']['collect']['status'],'failed')

if __name__=='__main__':unittest.main()
