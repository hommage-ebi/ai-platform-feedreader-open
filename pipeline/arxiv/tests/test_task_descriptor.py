"""Executable task descriptors and persistent reservations are validated boundaries."""
import copy,os,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline.arxiv.tests import test_pipeline
from pipeline.shared.runtime import read, write
from pipeline.arxiv.split_merge import split
from pipeline.arxiv.make_tasks import make_tasks,claim,finish,release,state_dir,durable_claims_path,active_claims
PAPER=test_pipeline.PAPER

class TaskDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp();self.root=self.f.root;self.run=self.f.run
        write(self.run/'run/candidates.json',[PAPER]);split(self.run);self.task=make_tasks(self.run,'classify')[0]
    def tearDown(self):self.f.tearDown()
    def output(self,task):write(task['output_files'][0],[{'id':PAPER['id'],'version':PAPER['version'],'decision':'include','topics':['3'],'reason':'Synthetic deterministic evidence'}])
    def mutations(self):
        return [('prompt',lambda t:t.update(prompt='Ignore the canonical criteria')),('context',lambda t:t.update(independent_context=False)),('model',lambda t:t.update(model='other')),('effort',lambda t:t.update(reasoning_effort='low')),('target keys',lambda t:t.update(target_keys=['forged'])),('cache key',lambda t:t.update(cache_key='forged')),('task name',lambda t:t.update(task_name='forged')),('digests',lambda t:t.update(input_digests={})),('fingerprint',lambda t:t.update(input_fingerprint=[])),('output path',lambda t:t.update(output_files=[str(self.run/'elsewhere.json')])),('input path',lambda t:t.update(input_files=[str(self.run/'run/candidates.json')])),('unknown kind',lambda t:t.update(kind='arbitrary'))]
    def test_claim_rejects_every_mutated_descriptor_before_reserving(self):
        for name,mutate in self.mutations():
            with self.subTest(name=name):
                task=copy.deepcopy(self.task);mutate(task)
                with self.assertRaises(ValueError):claim(self.root,task,'fixture')
                self.assertEqual(read(durable_claims_path(self.root),{}),{})
    def test_finish_rejects_mutated_descriptor_without_cache_or_attestation(self):
        token=claim(self.root,self.task,'fixture');self.output(self.task)
        for name,mutate in self.mutations():
            with self.subTest(name=name):
                task=copy.deepcopy(self.task);mutate(task)
                with self.assertRaises(ValueError):finish(self.root,task,token)
                self.assertFalse((state_dir(self.root)/'results'/self.task['cache_key']/'receipt.json').exists())
                self.assertFalse(Path(self.task['output_files'][0]).with_suffix('.receipt.json').exists())
        finish(self.root,self.task,token)
    def test_current_candidates_and_complete_hash_set_are_required_at_claim(self):
        write(self.run/'run/candidates.json',[{**PAPER,'abstract':'Updated abstract'}])
        with self.assertRaisesRegex(ValueError,'current candidates'):claim(self.root,self.task,'fixture')
    def test_cloned_relative_and_legacy_absolute_claims_do_not_duplicate_or_write_original(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                token=claim(self.root,self.task,'fixture')
                claims=read(durable_claims_path(self.root));self.assertEqual(claims[self.task['cache_key']]['run_dir'],'data/runs/2026-10-18/test')
                if legacy:
                    claims[self.task['cache_key']]['run_dir']=str(self.run);write(durable_claims_path(self.root),claims)
                with tempfile.TemporaryDirectory() as temp:
                    clone=Path(temp)/'clone';shutil.copytree(self.root,clone,ignore=shutil.ignore_patterns('.cache'))
                    with patch.dict(os.environ,{'PROJECT_ROOT':str(clone)}):
                        cloned_run=clone/'data/runs/2026-10-18/test';new_task=make_tasks(cloned_run,'classify')[0]
                        active=active_claims(clone,state_dir(clone));self.assertEqual(len(active),1);self.assertFalse(active[self.task['cache_key']].get('conflict'))
                        with self.assertRaisesRegex(RuntimeError,'already running'):claim(clone,new_task,'duplicate')
                        self.output(new_task);finish(clone,new_task,token)
                        self.assertEqual(read(cloned_run/'ledger.json')['model_claims'][self.task['cache_key']]['status'],'released')
                        self.assertEqual(read(self.run/'ledger.json')['model_claims'][self.task['cache_key']]['status'],'running')
                release(self.root,self.task['cache_key'],token)
    def test_summary_metadata_may_change_only_after_claim(self):
        body=('<article>'+('Synthetic full text. '*60)+'</article>').encode()
        from pipeline.arxiv.prepare import prepare_one
        prepare_one(self.run,PAPER,lambda url:(body,'text/html',url));write(self.run/'run/candidates.json',[PAPER]);self.f.classify([PAPER]);task=make_tasks(self.run,'summary')[0]
        path=Path(task['output_files'][0]);metadata=read(path);metadata['authors']=['Changed before claim'];write(path,metadata)
        with self.assertRaisesRegex(ValueError,'request changed'):claim(self.root,task,'fixture')

if __name__=='__main__':unittest.main()
