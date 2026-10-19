"""Cross-stage regressions: current eligibility, draft lifecycle and process locks."""
import datetime as dt
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from pipeline.arxiv.tests import test_pipeline
from pipeline.shared.runtime import digest, read, record, write
from pipeline.arxiv.common import identity
from pipeline.arxiv.assemble import assemble,assemble_one
from pipeline.arxiv.reader_adapter import adapt
from pipeline.shared.collection import recover_collection, TRANSACTION
from pipeline.arxiv.workflow import start
from pipeline.arxiv.collect import collect
PAPER=test_pipeline.PAPER

class ImportLifecycle(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp()
        self.root=self.f.root;self.run=self.f.run;self.dest=self.root/'data/articles'
        write(self.run/'run/candidates.json',[PAPER]);self.f.classify([PAPER]);self.paper_dir=self.f.fixture()
        record(self.run,'collect','complete');self.article=assemble_one(self.run,PAPER)
    def tearDown(self):self.f.tearDown()
    def adapt(self):return adapt(self.run/'site',self.run,self.dest)
    def change_summary(self):
        (self.paper_dir/'summary.md').write_text('## Revised\n\nA regenerated explanation.')
        self.f.attest(self.paper_dir);return assemble_one(self.run,PAPER)
    def protect(self,status='succeeded',global_record=False):
        path=self.root/'data/releases/build-test.json' if global_record else self.run/'release.json'
        write(path,{'status':status,'articles':[{'article_id':PAPER['id'],'version':'v1','content_sha256':self.article['content_sha256']}]})
    def test_unbound_legacy_overlap_does_not_block_fresh_partial_import(self):
        from pipeline.arxiv.split_merge import split
        legacy={**PAPER,'id':'2610.99999'}
        old=self.root/'data/runs/2026-10-17/legacy'
        write(old/'run/candidates.json',[legacy]);write(old/'ledger.json',{'stages':{}})
        write(self.run/'run/candidates.json',[PAPER,legacy]);split(self.run)
        self.assertEqual(read(self.run/'ledger.json')['stages']['policy:'+ '/'.join(identity(legacy))]['status'],'blocked')
        result=self.adapt();self.assertEqual(result['failures'],[])
        self.assertEqual(read(self.dest/'papers.json')[0]['id'],PAPER['id'])
    def test_published_import_rejects_policy_damage_without_mutating_collection(self):
        self.adapt();self.protect()
        mapping=self.run/'run/paper-policies.json';snapshot=self.run/'run/processing-policy.json'
        original=mapping.read_bytes();original_snapshot=snapshot.read_bytes()
        before={str(p.relative_to(self.dest)):p.read_bytes() for p in self.dest.rglob('*') if p.is_file()}
        for mutation in ('invalid_json','missing_map','missing_entry','changed_input','changed_policy','missing_snapshot'):
            with self.subTest(mutation=mutation):
                mapping.write_bytes(original);snapshot.write_bytes(original_snapshot)
                if mutation=='invalid_json':mapping.write_text('{broken')
                elif mutation=='missing_map':mapping.unlink()
                elif mutation=='missing_snapshot':snapshot.unlink()
                else:
                    value=read(mapping);key='/'.join(identity(PAPER))
                    if mutation=='missing_entry':del value[key]
                    elif mutation=='changed_input':value[key]['input_sha256']='0'*64
                    else:value[key]['policy']['summary']+=' unauthorized edit'
                    write(mapping,value)
                with self.assertRaises(ValueError):self.adapt()
                self.assertEqual({str(p.relative_to(self.dest)):p.read_bytes() for p in self.dest.rglob('*') if p.is_file()},before)
        mapping.write_bytes(original);snapshot.write_bytes(original_snapshot)
        self.assertEqual(self.adapt()['failures'],[])
    def test_excluded_stale_site_cannot_import(self):
        self.f.classify([PAPER],'exclude')
        result=self.adapt()
        self.assertEqual(result['added'],[]);self.assertEqual(len(result['failures']),1)
        self.assertEqual(read(self.dest/'papers.json'),[])
        self.assertEqual(read(self.dest/'batch-status.json')['completed_count'],0)
    def test_assemble_reconciles_exclusion_before_first_import(self):
        self.f.classify([PAPER],'exclude');self.assertEqual(assemble(self.run),[])
        self.assertEqual(read(self.run/'site/papers.json'),[]);self.assertEqual(self.adapt()['failures'],[])
        self.assertEqual(read(self.dest/'papers.json'),[])
    def test_excluded_imported_draft_is_retired(self):
        self.adapt();self.f.classify([PAPER],'exclude');assemble(self.run);self.adapt()
        self.assertEqual(read(self.dest/'papers.json'),[])
        self.assertEqual(read(self.dest/'images-manifest.json')['images'],[])
    def test_summary_prompt_change_preserves_original_import(self):
        with (self.root/'pipeline/arxiv/prompts/summary.txt').open('a') as stream:stream.write('\nNew prompt')
        result=self.adapt();self.assertEqual(len(result['added']),1);self.assertEqual(result['failures'],[])
        self.assertEqual(read(self.dest/'papers.json')[0]['content_sha256'],self.article['content_sha256'])
    def test_failed_reassembly_retires_imported_draft(self):
        self.adapt();(self.paper_dir/'summary.md').write_text('Unattested change')
        self.assertEqual(assemble(self.run),[]);self.assertEqual(read(self.run/'site/papers.json'),[])
        self.adapt();self.assertEqual(read(self.dest/'papers.json'),[])
    def test_candidate_tampering_invalidates_but_new_prompt_preserves_import(self):
        self.adapt();before=(self.dest/'papers.json').read_bytes()
        write(self.run/'run/candidates.json',[{**PAPER,'abstract':'Changed input'}])
        with self.assertRaisesRegex(ValueError,'binding'):self.adapt()
        self.assertEqual((self.dest/'papers.json').read_bytes(),before)
        write(self.run/'run/candidates.json',[PAPER])
        with (self.root/'pipeline/arxiv/prompts/relevance.txt').open('a') as stream:stream.write('\nNew prompt')
        self.assertEqual(self.adapt()['failures'],[])
        self.assertEqual(read(self.dest/'papers.json')[0]['content_sha256'],self.article['content_sha256'])
    def test_unpublished_replacement_and_idempotence(self):
        self.assertEqual(len(self.adapt()['added']),1);self.assertEqual(self.adapt()['added'],[])
        new=self.change_summary();self.assertFalse((self.run/'release.json').exists())
        self.assertEqual(self.adapt()['failures'],[])
        stored=read(self.dest/'papers.json')[0]
        self.assertEqual(stored['content_sha256'],new['content_sha256']);self.assertEqual(len(stored['versionHistory']),1)
        self.assertEqual(digest((self.dest/stored['html_path']).read_bytes()),new['content_sha256'])
    def test_published_and_pending_global_versions_cannot_be_replaced(self):
        self.adapt()
        for status in ('succeeded','verification_pending'):
            with self.subTest(status=status):
                self.protect(status,global_record=True);self.change_summary()
                result=self.adapt();self.assertEqual(len(result['failures']),1)
                self.assertEqual(read(self.dest/'papers.json')[0]['content_sha256'],self.article['content_sha256'])
                self.assertEqual(digest((self.dest/self.article['html_path']).read_bytes()),self.article['content_sha256'])
    def test_published_bytes_survive_exclusion(self):
        self.adapt();self.protect();self.f.classify([PAPER],'exclude');assemble(self.run);self.adapt()
        self.assertEqual(read(self.dest/'papers.json')[0]['content_sha256'],self.article['content_sha256'])
        batch=read(self.dest/'batch-status.json');self.assertEqual(batch['completed_count'],1);self.assertEqual(batch['published_count'],1);self.assertEqual(len(batch['completed_articles']),1)
    def test_mixed_valid_and_stale_partial_import(self):
        other={**PAPER,'id':'2610.54321','source_url':'https://arxiv.org/abs/2610.54321v1'}
        write(self.run/'run/candidates.json',[PAPER,other]);self.f.classify([PAPER,other]);self.f.fixture(other);assemble_one(self.run,other)
        (self.paper_dir/'summary.md').write_text('Stale output without receipt')
        result=self.adapt();self.assertEqual(result['added'],[identity(other)]);self.assertEqual(len(result['failures']),1)
        self.assertEqual([a['id'] for a in read(self.dest/'papers.json')],[other['id']])
    def test_failed_overlapping_run_does_not_retire_other_run_draft(self):
        self.adapt();other=self.root/'data/runs/2026-10-18/other';other.mkdir(parents=True)
        write(other/'ledger.json',{'target_date':'2026-10-18','run_id':'other','stages':{}})
        write(other/'run/candidates.json',[PAPER]);write(other/'run/selected.json',[])
        write(other/'run/classifications.json',[]);write(other/'run/classification-proofs.json',{});write(other/'site/papers.json',[])
        from pipeline.arxiv.policy import bind_papers
        bind_papers(other,[PAPER])
        self.assertEqual(adapt(other/'site',other,self.dest)['failures'],[])
        self.assertEqual(read(self.dest/'papers.json')[0]['content_sha256'],self.article['content_sha256'])
    def test_successful_generation_can_replace_other_run_draft(self):
        self.adapt();path=self.dest/'papers.json';records=read(path)
        records[0]['generation_run']='data/runs/2026-10-17/previous'
        records[0]['versionHistory'][0]['generation_run']='data/runs/2026-10-17/previous'
        records[0]['generation_runs']=['data/runs/2026-10-17/previous'];records[0]['versionHistory'][0]['generation_runs']=['data/runs/2026-10-17/previous'];write(path,records)
        new=self.change_summary();self.assertEqual(self.adapt()['failures'],[])
        self.assertEqual(read(path)[0]['content_sha256'],new['content_sha256'])

    def test_same_generation_shared_owners_survive_one_run_exclusion(self):
        import shutil
        self.adapt();other=self.run.parent/'other';shutil.copytree(self.run,other)
        ledger=read(other/'ledger.json');ledger['run_id']='other';write(other/'ledger.json',ledger)
        self.assertEqual(adapt(other/'site',other,self.dest)['failures'],[])
        owners=read(self.dest/'papers.json')[0]['generation_runs'];self.assertEqual(len(owners),2)
        self.f.classify([PAPER],'exclude');assemble(self.run);self.adapt()
        remaining=read(self.dest/'papers.json');self.assertEqual(len(remaining),1)
        self.assertEqual(remaining[0]['generation_runs'],[str(other.relative_to(self.root))])
        for name in ('classifications.json','classification-proofs.json','selected.json'):
            write(other/'run'/name,read(self.run/'run'/name))
        assemble(other);adapt(other/'site',other,self.dest)
        self.assertEqual(read(self.dest/'papers.json'),[])
    def test_missing_or_malformed_required_state_never_retires_draft(self):
        self.adapt()
        for name in ('ledger.json','run/candidates.json','run/classifications.json','run/classification-proofs.json','run/selected.json','site/papers.json'):
            path=self.run/name;original=path.read_bytes()
            for malformed in (None,'{broken','null'):
                with self.subTest(name=name,malformed=malformed):
                    before={p:p.read_bytes() for p in self.dest.rglob('*.json')}
                    if malformed is None:path.unlink()
                    else:path.write_text(malformed)
                    try:
                        with self.assertRaises((ValueError,TypeError)):self.adapt()
                        self.assertEqual({p:p.read_bytes() for p in self.dest.rglob('*.json')},before)
                    finally:path.write_bytes(original)

    def test_missing_canonical_index_or_manifest_requires_recovery(self):
        self.adapt()
        for name in ('papers.json','images-manifest.json'):
            path=self.dest/name;original=path.read_bytes();path.unlink()
            try:
                before={p:p.read_bytes() for p in self.dest.rglob('*.json')}
                with self.assertRaisesRegex(ValueError,'collection is incomplete'):self.adapt()
                self.assertEqual({p:p.read_bytes() for p in self.dest.rglob('*.json')},before)
            finally:path.write_bytes(original)

    def test_interrupted_transaction_replays_before_next_import(self):
        from pipeline.shared import collection
        original=collection.write
        def crash(path,data):
            if Path(path)==self.dest/'papers.json':raise OSError('Simulated interruption after manifest')
            return original(path,data)
        with patch('pipeline.shared.collection.write',side_effect=crash),self.assertRaises(OSError):self.adapt()
        self.assertTrue((self.dest/TRANSACTION).exists())
        result=self.adapt();self.assertEqual(result['failures'],[]);self.assertFalse((self.dest/TRANSACTION).exists())
        self.assertEqual(len(read(self.dest/'papers.json')),1)
    def test_transaction_recovery_replaces_html_consistently(self):
        self.adapt();new=self.change_summary()
        from pipeline.shared import collection
        original=collection.write
        def crash(path,data):
            if Path(path)==self.dest/'papers.json':raise OSError('Interrupted replacement')
            return original(path,data)
        with patch('pipeline.shared.collection.write',side_effect=crash),self.assertRaises(OSError):self.adapt()
        self.assertTrue((self.dest/TRANSACTION).exists());self.adapt()
        stored=read(self.dest/'papers.json')[0]
        self.assertEqual(stored['content_sha256'],new['content_sha256'])
        self.assertEqual(digest((self.dest/stored['html_path']).read_bytes()),new['content_sha256'])
    def test_start_and_adapter_respect_other_process_run_lock(self):
        process=subprocess.Popen([sys.executable,'-c',"import fcntl,sys; f=open(sys.argv[1],'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()",str(self.run/'.run.lock')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'locked');before=(self.run/'ledger.json').read_bytes()
            with patch('pipeline.arxiv.collect.collect') as fetcher:
                with self.assertRaises(BlockingIOError):start(self.root,dt.datetime.fromisoformat('2026-10-18T01:00:00+00:00'))
                fetcher.assert_not_called()
            cli=Path(__file__).resolve().parents[1]/'workflow.py'
            completed=subprocess.run([sys.executable,str(cli),'start','--project-root',str(self.root),'--started-at','2026-10-18T01:00:00+00:00'],cwd=self.root,capture_output=True,text=True)
            self.assertNotEqual(completed.returncode,0);self.assertFalse((self.root/'ledger.json').exists())
            with self.assertRaises(BlockingIOError):self.adapt()
            self.assertEqual((self.run/'ledger.json').read_bytes(),before)
        finally:
            process.stdin.close();process.wait();process.stdout.close()
        self.assertEqual(self.adapt()['failures'],[])
        with patch('pipeline.arxiv.collect.collect',side_effect=lambda run,date:collect(run,date,fetcher=lambda _:self.f.rss())):
            self.assertEqual(Path(start(self.root,dt.datetime.fromisoformat('2026-10-18T01:00:00+00:00'))['today']),self.run)

if __name__=='__main__':unittest.main()
