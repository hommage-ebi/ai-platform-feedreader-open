"""User-editable interest IDs, criteria and application guidance are provenance."""
import copy
import json
import sys
import unittest
from pathlib import Path
from pipeline.arxiv.tests import test_pipeline
from pipeline.shared.runtime import read, write
from pipeline.arxiv.common import paper_path
from pipeline.shared.interests import load_interests,render_relevance,validate_interests
from pipeline.arxiv.split_merge import split,merge,validate_classification,prompt_hash
from pipeline.arxiv.make_tasks import make_tasks,claim,finish,release
from pipeline.arxiv.assemble import assemble,assemble_one
from pipeline.arxiv.reader_adapter import adapt
PAPER=test_pipeline.PAPER

class InterestsConfig(unittest.TestCase):
    def setUp(self):
        self.f=test_pipeline.PipelineTests();self.f.setUp();self.root=self.f.root;self.run=self.f.run
        self.path=self.root/'pipeline/config/interests.json'
        self.config={'schema_version':1,'interests':[{'id':'climate.models','label':'Climate','criteria':'Evaluate climate models'},{'id':'ocean-v2','label':'Oceans','criteria':'Study ocean circulation'}],'application_guidance':'Use affirmative evidence, not keyword matching.'}
    def tearDown(self):self.f.tearDown()
    def save(self):write(self.path,self.config)
    def classify(self):
        self.save();write(self.run/'run/candidates.json',[PAPER]);split(self.run);task=make_tasks(self.run,'classify')[0]
        token=claim(self.root,task,'synthetic-test-output')
        write(task['output_files'][0],[{'id':PAPER['id'],'version':'v1','decision':'include','topics':['climate.models'],'reason':'Synthetic affirmative evidence'}])
        finish(self.root,task,token);merge(self.run);return task
    def test_custom_ids_and_arbitrary_topic_counts(self):
        self.classify();self.assertEqual(read(self.run/'run/selected.json')[0]['topics'],['climate.models'])
        row=read(self.run/'run/classifications.json')[0]
        with self.assertRaises(ValueError):validate_classification([{**row,'topics':['1']}],[PAPER],self.run)
        # Defaults may use any number of topics, while this paper keeps its original IDs.
        self.config['interests']=[{'id':'topic-'+str(i),'label':str(i),'criteria':'Criterion '+str(i)} for i in range(9)];self.save()
        self.assertEqual(len(load_interests(self.root)['interests']),9)
        with self.assertRaises(ValueError):validate_classification([{**row,'topics':['topic-8']}],[PAPER],self.run)
        validate_classification([row],[PAPER],self.run)
        self.config['interests']=self.config['interests'][:1];self.save()
        self.assertEqual(len(load_interests(self.root)['interests']),1)
        validate_classification([row],[PAPER],self.run)
    def test_literal_config_placeholders_and_dynamic_example(self):
        literal='{input_file} {output_file} {interest_config} {topic_example} {x} {{braces}}'
        self.config['application_guidance']=literal;self.config['interests'][0]['criteria']=literal;self.save()
        expanded=render_relevance((self.root/'pipeline/arxiv/prompts/relevance.txt').read_text(),load_interests(self.root),'/real/input.json','/real/output.json')
        self.assertEqual(expanded.count(literal),2)
        self.assertIn('入力ファイル: /real/input.json',expanded);self.assertIn('出力先: /real/output.json',expanded)
        self.assertIn('"topics":["climate.models"]',expanded)
        self.assertNotIn('この6項目',expanded);self.assertNotIn('材料科学',expanded)
    def test_current_defaults_are_embedded_in_new_classification_task(self):
        # Check the editable configuration contract, not a fixed category count or IDs.
        defaults=load_interests(Path(__file__).resolve().parents[3])
        write(self.path,defaults);write(self.run/'run/candidates.json',[PAPER]);split(self.run)
        task=make_tasks(self.run,'classify')[0]
        self.assertEqual(read(self.run/'run/processing-policy.json')['interests'],defaults)
        self.assertIn(json.dumps(defaults,ensure_ascii=False,sort_keys=True,indent=2),task['prompt'])
        self.assertIn('"topics":'+json.dumps([defaults['interests'][0]['id']],ensure_ascii=False,separators=(',',':')),task['prompt'])
        validate_classification([{'id':PAPER['id'],'version':'v1','decision':'include','topics':[row['id'] for row in defaults['interests']],'reason':'Synthetic evidence'}],[PAPER],self.run)

    def test_replacement_topics_apply_only_to_newly_captured_papers(self):
        self.classify();before=(self.run/'run/processing-policy.json').read_bytes()
        self.config['interests']=[{'id':'new-topic','label':'Replacement topic','criteria':'Evaluate replacement evidence'}];self.save()
        self.assertEqual(split(self.run),[]);self.assertEqual(make_tasks(self.run,'classify'),[])
        self.assertEqual(merge(self.run)[0]['topics'],['climate.models'])
        self.assertEqual((self.run/'run/processing-policy.json').read_bytes(),before)
        other=self.run.parent/'new-capture';paper={**PAPER,'id':'2610.99999'}
        write(other/'ledger.json',{'target_date':'2026-10-18','today_at_start':'2026-10-18','run_id':'new-capture','stages':{}})
        write(other/'run/candidates.json',[paper]);split(other);task=make_tasks(other,'classify')[0]
        self.assertEqual(read(other/'run/processing-policy.json')['interests'],self.config)
        self.assertIn('"topics":["new-topic"]',task['prompt'])
        row={'id':paper['id'],'version':'v1','decision':'include','topics':['new-topic'],'reason':'Synthetic evidence'}
        validate_classification([row],[paper],other)
        with self.assertRaises(ValueError):validate_classification([{**row,'topics':['climate.models']}],[paper],other)

    def test_guidance_change_preserves_groups_proofs_and_cache(self):
        original=self.classify();before=prompt_hash(self.run);self.assertEqual(split(self.run),[])
        evidence={p:p.read_bytes() for p in [self.run/'run/groups.json',self.run/'run/classification-proofs.json',self.run/'run/processing-policy.json']}
        self.config['application_guidance']+=' Require stronger evidence.';self.save()
        self.assertEqual(prompt_hash(self.run),before);self.assertEqual(split(self.run),[])
        self.assertEqual(merge(self.run)[0]['topics'],['climate.models'])
        self.assertEqual(make_tasks(self.run,'classify'),[])
        self.assertEqual({p:p.read_bytes() for p in evidence},evidence)
        self.assertTrue(Path(original['output_files'][0]).exists())

    def test_config_change_preserves_unpublished_and_published_imports(self):
        self.classify();self.f.fixture();article=assemble_one(self.run,read(self.run/'run/selected.json')[0]);dest=self.root/'data/articles'
        self.assertEqual(adapt(self.run/'site',self.run,dest)['failures'],[])
        self.config['application_guidance']+=' Changed.';self.save()
        self.assertEqual(adapt(self.run/'site',self.run,dest)['failures'],[])
        self.assertEqual(read(dest/'papers.json')[0]['content_sha256'],article['content_sha256'])
        write(self.run/'release.json',{'status':'succeeded','articles':[{'article_id':PAPER['id'],'version':'v1','content_sha256':article['content_sha256']}]})
        self.assertEqual(adapt(self.run/'site',self.run,dest)['failures'],[])
        self.assertEqual(read(dest/'papers.json')[0]['content_sha256'],article['content_sha256'])

    def test_config_change_preserves_pending_classification_claim(self):
        self.save();write(self.run/'run/candidates.json',[PAPER]);split(self.run);task=make_tasks(self.run,'classify')[0]
        token=claim(self.root,task,'synthetic-output')
        write(task['output_files'][0],[{'id':PAPER['id'],'version':'v1','decision':'include','topics':['climate.models'],'reason':'Evidence'}])
        self.config['application_guidance']+=' New condition.';self.save()
        with self.assertRaises(RuntimeError):claim(self.root,task,'duplicate-active-claim')
        finish(self.root,task,token)
        self.assertEqual(merge(self.run)[0]['topics'],['climate.models'])
        self.assertEqual(make_tasks(self.run,'classify'),[])

    def prepare_summary(self):
        from pipeline.arxiv.prepare import prepare_one
        body=('<article>'+('Synthetic manuscript. '*60)+'</article>').encode()
        prepare_one(self.run,PAPER,lambda url:(body,'text/html',url))
        task=make_tasks(self.run,'summary')[0];token=claim(self.root,task,'synthetic-summary')
        directory=paper_path(self.run,PAPER)
        write(directory/'result.json',{'id':PAPER['id'],'version':'v1','title_ja':'試験','lead_ja':['問い','方法','結果'],'source_url':PAPER['source_url'],'summary_file':'summary.md','figures':[]})
        (directory/'summary.md').write_text('## 説明\n\n合成した文章による検証です。')
        return task,token,directory

    def test_summary_reuses_original_eligibility_and_output(self):
        self.classify();task,token,directory=self.prepare_summary();finish(self.root,task,token)
        receipt=read(directory/'model-output-receipt.json');receipt.pop('verified_at',None)
        self.config['application_guidance']+=' New eligibility guidance.';self.save()
        self.assertEqual(make_tasks(self.run,'summary'),[])
        self.assertEqual(read(self.run/'ledger.json')['stages']['summary_tasks']['status'],'complete')
        after=read(directory/'model-output-receipt.json');after.pop('verified_at',None)
        self.assertEqual(after,receipt)
        self.assertEqual(read(directory/'model-output-receipt.json')['cache_key'],task['cache_key'])

    def test_active_summary_finish_uses_original_policy(self):
        self.classify();task,token,directory=self.prepare_summary()
        self.config['application_guidance']+=' Changed while running.';self.save()
        finish(self.root,task,token)
        self.assertEqual(read(directory/'model-output-receipt.json')['cache_key'],task['cache_key'])

    def test_topic_edits_do_not_regenerate_or_retag_old_article(self):
        self.classify();directory=self.f.fixture();first=assemble(self.run)[0];dest=self.root/'data/articles'
        adapt(self.run/'site',self.run,dest);summary_before=(directory/'summary.md').read_bytes()
        self.config['interests'][0]['id']='new-topic';self.save()
        self.assertEqual(split(self.run),[]);self.assertEqual(make_tasks(self.run,'classify'),[])
        merge(self.run);second=assemble(self.run)[0]
        self.assertEqual(first['content_sha256'],second['content_sha256'])
        self.assertEqual(first['generation_hash'],second['generation_hash'])
        self.assertEqual(second['tags'],['climate.models']);self.assertEqual((directory/'summary.md').read_bytes(),summary_before)
        adapt(self.run/'site',self.run,dest);self.assertEqual(read(dest/'papers.json')[0]['tags'],['climate.models'])

    def test_shared_unpublished_article_keeps_original_metadata(self):
        import shutil
        self.classify();self.f.fixture();assemble(self.run);dest=self.root/'data/articles';adapt(self.run/'site',self.run,dest)
        other=self.run.parent/'other';shutil.copytree(self.run,other);ledger=read(other/'ledger.json');ledger['run_id']='other';write(other/'ledger.json',ledger)
        self.assertEqual(adapt(other/'site',other,dest)['failures'],[])
        self.config['interests'][0]['id']='updated-topic';self.save()
        self.assertEqual(split(self.run),[]);self.assertEqual(make_tasks(self.run,'classify'),[])
        assemble(self.run);self.assertEqual(adapt(self.run/'site',self.run,dest)['failures'],[])
        record=read(dest/'papers.json')[0];self.assertEqual(record['tags'],['climate.models'])
        self.assertEqual(set(record['generation_runs']),{str(self.run.relative_to(self.root)),str(other.relative_to(self.root))})

    def test_invalid_new_defaults_cannot_poison_captured_run(self):
        self.classify();self.f.fixture();assemble_one(self.run,read(self.run/'run/selected.json')[0]);dest=self.root/'data/articles';adapt(self.run/'site',self.run,dest)
        self.assertEqual(split(self.run),[])
        assemble(self.run);adapt(self.run/'site',self.run,dest)
        good=self.path.read_bytes();bad_configs=[None,'{broken','{"schema_version":1,"schema_version":1,"interests":[],"application_guidance":"x"}']
        mutations=[lambda c:c.update(schema_version=True),lambda c:c.update(extra='x'),lambda c:c.update(interests=[]),lambda c:c.update(application_guidance=' '),lambda c:c['interests'][0].update(id='bad id'),lambda c:c['interests'][0].update(criteria=''),lambda c:c['interests'].append(copy.deepcopy(c['interests'][0]))]
        for mutation in mutations:
            value=copy.deepcopy(self.config);mutation(value);bad_configs.append(json.dumps(value))
        for value in bad_configs:
            with self.subTest(value=value):
                if value is None:self.path.unlink()
                else:self.path.write_text(value)
                before={p:p.read_bytes() for p in [dest/'papers.json',self.run/'site/papers.json',self.run/'run/groups.json',self.run/'run/processing-policy.json',self.run/'run/paper-policies.json',self.run/'run/classification-proofs.json']}
                with self.assertRaises(ValueError):load_interests(self.root)
                self.assertEqual(split(self.run),[])
                self.assertEqual(make_tasks(self.run,'classify'),[])
                self.assertEqual(len(assemble(self.run)),1)
                self.assertEqual(adapt(self.run/'site',self.run,dest)['failures'],[])
                self.assertEqual({p:p.read_bytes() for p in before},before)
                self.path.write_bytes(good)

    def test_snapshot_tampering_is_rejected_without_reclassifying(self):
        from pipeline.arxiv.policy import run_policy,paper_policy
        self.classify()
        paths=[self.run/'run/processing-policy.json',self.run/'run/paper-policies.json']
        for path in paths:
            with self.subTest(path=path.name):
                original=path.read_bytes();value=read(path)
                if path==paths[0]:value['interests']['application_guidance']='Tampered policy'
                else:value[PAPER['id']+'/v1']['policy_sha256']='0'*64
                write(path,value)
                operation=(lambda:run_policy(self.run)) if path==paths[0] else (lambda:paper_policy(self.run,PAPER))
                with self.assertRaises(ValueError):operation()
                with self.assertRaises(ValueError):split(self.run)
                self.assertEqual(path.read_bytes(),(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode())
                path.write_bytes(original)
        self.assertEqual(merge(self.run)[0]['topics'],['climate.models'])

if __name__=='__main__':unittest.main()
