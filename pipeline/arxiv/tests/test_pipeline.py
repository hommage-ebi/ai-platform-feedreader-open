import datetime as dt,io,json,os,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pipeline.shared.runtime import digest, read, record, write
from pipeline.arxiv.common import identity, paper_path, recompute_completion
from pipeline.arxiv.collect import collect,parse
from pipeline.arxiv.split_merge import split,merge,validate_classification
from pipeline.arxiv.make_tasks import make_tasks,claim,release,finish,state_dir
from pipeline.arxiv.prepare import prepare_one,cleanup
from pipeline.arxiv.assemble import assemble_one,validate_output,render_narrative
from pipeline.arxiv.reader_adapter import adapt
from pipeline.shared.image_store import store_figure

# Synthetic parser fixture identity and prose; not copied from an actual paper.
PAPER={'id':'2610.12345','version':'v1','title':'Materials study','abstract':'Machine learned potential','date':'2026-10-18','source_url':'https://arxiv.org/abs/2610.12345v1','topics':['3']}
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.run=self.root/'data/runs/2026-10-18/test';self.run.mkdir(parents=True)
        prompts=self.root/'pipeline/arxiv/prompts';prompts.mkdir(parents=True)
        source=Path(__file__).resolve().parents[1]/'prompts'
        for name in ['relevance.txt','summary.txt']:shutil.copyfile(source/name,prompts/name)
        shutil.copytree(source.parent/'config',self.root/'pipeline/arxiv/config')
        shutil.copytree(source.parent.parent/'config',self.root/'pipeline/config')
        # Keep fixture topic IDs independent of the user's editable defaults.
        shutil.copyfile(Path(__file__).parent/'fixtures/interests.json',self.root/'pipeline/config/interests.json')
        self.env=patch.dict(os.environ,{'PROJECT_ROOT':str(self.root)});self.env.start()
        write(self.run/'ledger.json',{'target_date':'2026-10-18','today_at_start':'2026-10-18','run_id':'test','stages':{}})
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_policy_cache_revalidates_changed_bytes_and_returns_fresh_bindings(self):
        from pipeline.arxiv import policy
        self.fixture();policy.validate_bindings(self.run)
        with patch.object(policy,'binding_policy',wraps=policy.binding_policy) as check:
            first=policy.validate_bindings(self.run);first.clear()
            self.assertTrue(policy.validate_bindings(self.run));self.assertEqual(check.call_count,0)
        path=self.run/'run/processing-policy.json';value=read(path);value['summary']+=' tampered';write(path,value)
        with self.assertRaisesRegex(ValueError,'hash mismatch'):policy.validate_bindings(self.run)

    def test_policy_cache_detects_tampered_historical_policy(self):
        from pipeline.arxiv import policy
        self.fixture();current=policy.run_policy(self.run);prior={**current,'summary':current['summary']+' historical'}
        prior['sha256']=digest({k:v for k,v in prior.items() if k!='sha256'})
        path=self.run/'run/policies'/f"{prior['sha256']}.json";write(path,prior)
        bindings=read(self.run/'run/paper-policies.json');bindings['/'.join(identity(PAPER))]['policy_sha256']=prior['sha256'];write(self.run/'run/paper-policies.json',bindings)
        policy.validate_bindings(self.run);policy.validate_bindings(self.run)
        prior['summary']+=' tampered';write(path,prior)
        with self.assertRaisesRegex(ValueError,'hash mismatch'):policy.validate_bindings(self.run)

    def test_missing_html_figure_falls_back_and_explicit_recovery_preserves_history(self):
        from pipeline.arxiv.prepare import prepare_pdf
        html=('<article>'+('Manuscript text. '*60)+'<figure><img class="ltx_missing_image" src=""></figure></article>').encode()
        pdf=b'%PDF-1.4\nsynthetic'
        def fetcher(url):return (pdf,'application/pdf',url) if '/pdf/' in url else (html,'text/html',url)
        self.assertEqual(prepare_one(self.run,PAPER,fetcher)['kind'],'pdf')
        p=self.fixture();(p/'source.html').write_bytes(html)
        old=read(p/'source.json');old['sha256']=digest(html);write(p/'source.json',old)
        write(self.run/'run/selected.json',[PAPER])
        with self.assertRaisesRegex(ValueError,'Verified model output'):prepare_pdf(self.run,PAPER,fetcher)
        (p/'model-output-receipt.json').unlink()
        with self.assertRaisesRegex(ValueError,'draft output'):prepare_pdf(self.run,PAPER,fetcher)
        (p/'result.json').unlink();(p/'summary.md').unlink()
        claims=self.root/'data/model-task-claims.json';write(claims,{'active':{'token':'fixture','run_dir':'data/runs/2026-10-18/test','target_keys':['fixture']}})
        with self.assertRaisesRegex(ValueError,'Model task is running'):prepare_pdf(self.run,PAPER,fetcher)
        write(claims,{})
        recovered=prepare_pdf(self.run,PAPER,fetcher)
        self.assertEqual(recovered['kind'],'pdf');self.assertEqual(read(p/'source-history.json'),[old])
        self.assertEqual(read(self.run/'ledger.json')['stages']['prepare:'+ '/'.join(identity(PAPER))]['status'],'complete')

    def test_prepare_pdf_never_switches_published_or_unrelated_source(self):
        from pipeline.arxiv.prepare import prepare_pdf
        p=self.fixture();(p/'model-output-receipt.json').unlink()
        (p/'result.json').unlink();(p/'summary.md').unlink()
        with self.assertRaisesRegex(ValueError,'No missing HTML'):prepare_pdf(self.run,PAPER)
        write(self.root/'data/releases/fixture.json',{'status':'succeeded','articles':[{'article_id':PAPER['id'],'version':PAPER['version'],'content_sha256':'a'*64}]})
        with self.assertRaisesRegex(ValueError,'Published or pending'):prepare_pdf(self.run,PAPER)

    def test_markdown_autolink_is_accepted_but_raw_html_is_rejected(self):
        p=self.fixture();path=p/'summary.md';path.write_text('## 方法\n\n<https://example.org/code>')
        validate_output(p)
        path.write_text('## 方法\n\n<https://example.org/code><script>alert(1)</script>')
        with self.assertRaisesRegex(ValueError,'raw HTML'):validate_output(p)

    def test_batch_does_not_count_recovered_paper_failures(self):
        self.fixture();self.classify([PAPER]);record(self.run,'classify','blocked',{'error':'old'})
        record(self.run,'summary','failed',{'error':'old'},'/'.join(identity(PAPER)))
        article=assemble_one(self.run,PAPER);adapt(self.run/'site',self.run,self.root/'data/articles')
        batch=read(self.root/'data/articles/batch-status.json')
        self.assertEqual(batch['failed_count'],0);self.assertEqual(batch['completed_count'],1)
    def fixture(self,paper=None):
        paper=paper or PAPER
        from pipeline.arxiv.policy import run_policy,bind_papers
        run_policy(self.run)
        candidates=read(self.run/'run/candidates.json',[])
        if not any(identity(row)==identity(paper) for row in candidates):write(self.run/'run/candidates.json',[*candidates,paper])
        bind_papers(self.run,[paper])
        p=paper_path(self.run,paper);p.mkdir(parents=True,exist_ok=True)
        body=('<article><p>'+('Synthetic manuscript. '*60)+'</p></article>').encode();(p/'source.html').write_bytes(body)
        write(p/'source.json',{'id':paper['id'],'version':paper['version'],'source_url':paper['source_url'],'url':paper['source_url'].replace('/abs/','/html/'),'kind':'html','local_file':'source.html','sha256':digest(body),'verified':True})
        write(p/'metadata.json',{'schema_version':1,'id':paper['id'],'version':paper['version'],'title':paper['title'],'authors':['A. Author'],'affiliations':['Department, University'],'institution_names':['University'],'field_sources':{},'source':paper['source_url'],'notes':[],'missing_fields':[]})
        write(p/'result.json',{'id':paper['id'],'version':paper['version'],'title_ja':'材料研究','lead_ja':['研究の問い','独自の方法','結果と限界'],'source_url':paper['source_url'],'summary_file':'summary.md','figures':[]})
        (p/'summary.md').write_text('## 方法\n\n本文の式 \\(E=\\alpha_1\\) を説明する。\n\n\\[\nE = \\sum_i x_i\n\\]\n\n結果を検討する。');self.attest(p);return p
    def attest(self,p):
        from pipeline.arxiv.make_tasks import summary_fingerprint
        from pipeline.arxiv.policy import summary_template,agent_settings
        paper=next(row for row in read(self.run/'run/candidates.json') if paper_path(self.run,row)==p)
        template=summary_template(self.run,paper)
        settings=agent_settings(self.run,paper,'summary')
        key=digest({'kind':'summary','input':summary_fingerprint(p),'prompt':template,**settings})
        write(p/'model-output-receipt.json',{'cache_key':key,'outputs':{name:digest((p/name).read_bytes()) for name in ['metadata.json','result.json','summary.md']}})
    def rss(self,items='',date='Sun, 18 Oct 2026 03:00:00 GMT'):
        return f'<rss><channel><lastBuildDate>{date}</lastBuildDate>{items}</channel></rss>'.encode()
    def test_collect_append_empty_and_failure_preserve(self):
        item='<item><title>Materials study</title><link>https://arxiv.org/abs/2610.12345</link><description>arXiv:2610.12345v1 Announce Type: new Abstract: Machine learned potential</description></item>'
        collect(self.run,'2026-10-18',fetcher=lambda u:self.rss(item));self.assertEqual(len(read(self.run/'run/candidates.json')),1)
        collect(self.run,'2026-10-18',fetcher=lambda u:self.rss(),refresh_rss=True);self.assertEqual(len(read(self.run/'run/candidates.json')),1)
        collect(self.run,'2026-10-18',fetcher=lambda u:self.rss(date='Sat, 17 Oct 2026 03:00:00 GMT'),refresh_rss=True)
        self.assertEqual(read(self.run/'ledger.json')['stages']['collect_attempt']['status'],'failed');self.assertEqual(read(self.run/'ledger.json')['stages']['collect']['status'],'complete');self.assertEqual(len(read(self.run/'run/candidates.json')),1)
    def test_no_guess_missing_version(self):
        with self.assertRaises(ValueError):parse(self.rss('<item><link>https://arxiv.org/abs/2610.12345</link><title>T</title><description>A</description></item>'),'2026-10-18')
    def test_historical_collect_forbidden(self):
        with self.assertRaises(ValueError):collect(self.run,'2026-10-17')
    def test_classification_zero_partial_and_uncertain(self):
        self.assertEqual(split(self.run),[]);write(self.run/'run/candidates.json',[PAPER]);paths=split(self.run);self.assertEqual(len(paths),1)
        group=self.run/paths[0];result={'id':PAPER['id'],'version':'v1','decision':'uncertain','topics':['3'],'reason':'具体的接点と不明点'};write(group.with_name(group.stem+'.result.json'),[result]);self.finish_classifiers();selected=merge(self.run);self.assertEqual(selected[0]['decision'],'uncertain');self.assertEqual(split(self.run),[])
        with (self.root/'pipeline/arxiv/prompts/relevance.txt').open('a') as f:f.write('\nUpdated prompt')
        self.assertEqual(split(self.run),[]);self.assertEqual(merge(self.run)[0]['decision'],'uncertain')
    def test_invalid_classification_never_excluded(self):
        with self.assertRaises(ValueError):validate_classification([], [PAPER])
        write(self.run/'run/candidates.json',[PAPER]);split(self.run);self.assertEqual(merge(self.run),[]);self.assertEqual(read(self.run/'run/classifications.json'),[])
    def test_model_task_claim_limit_and_durable_reuse(self):
        write(self.run/'run/candidates.json',[PAPER]);split(self.run);tasks=make_tasks(self.run,'classify');task=tasks[0]
        self.assertIn('関心設定',task['prompt']);self.assertEqual(task['model'],'gpt-6.1-sol');self.assertEqual(task['reasoning_effort'],'high')
        token=claim(self.root,task,'worker')
        with self.assertRaises(RuntimeError):claim(self.root,task,'duplicate')
        more=[]
        for i in range(5):
            other=self.root/f'data/runs/2026-10-18/claim-{i}';write(other/'ledger.json',{'stages':{}});write(other/'run/candidates.json',[{**PAPER,'id':f'2610.{20000+i}'}]);split(other);t=make_tasks(other,'classify')[0];more.append((t,claim(self.root,t,'worker')))
        other=self.root/'data/runs/2026-10-18/seventh';write(other/'ledger.json',{'stages':{}});write(other/'run/candidates.json',[{**PAPER,'id':'2610.30000'}]);split(other);seventh=make_tasks(other,'classify')[0]
        with self.assertRaises(RuntimeError):claim(self.root,seventh,'worker')
        for t,tok in more:release(self.root,t['cache_key'],tok)
        write(task['output_files'][0],[{'id':PAPER['id'],'version':'v1','decision':'include','topics':['3'],'reason':'材料現象を扱う'}]);finish(self.root,task,token)
        shutil.rmtree(state_dir(self.root));self.assertEqual(make_tasks(self.run,'classify'),[])
    def test_prepare_exact_version_hash_and_cleanup(self):
        body=('<article><p>'+('Main paper text. '*60)+'</p></article>').encode()
        def fetcher(url):return body,'text/html',url
        source=prepare_one(self.run,PAPER,fetcher);self.assertTrue(source['verified'])
        with self.assertRaises(ValueError):prepare_one(self.run,PAPER,lambda url:(body+b'changed','text/html',url))
        cleanup(paper_path(self.run,PAPER));self.assertTrue((paper_path(self.run,PAPER)/'source.json').exists());self.assertFalse((paper_path(self.run,PAPER)/'source.html').exists())
        with self.assertRaises(ValueError):prepare_one(self.run,PAPER,lambda url:(body,'text/html',url+'0'))
    def test_zero_image_article_adapter_versions_and_unpublished_replacement(self):
        self.fixture();write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER]);article=assemble_one(self.run,PAPER);dest=self.root/'data/articles';self.assertEqual(len(adapt(self.run/'site',self.run,dest)['added']),1)
        html=(dest/article['html_path']).read_text();self.assertIn('\\(E=\\alpha_1\\)',html);self.assertIn('\\[\nE = \\sum_i x_i\n\\]',html);self.assertEqual(html.count('class="narrative"'),1)
        p2={**PAPER,'version':'v2','source_url':PAPER['source_url'][:-1]+'2'};self.fixture(p2);write(self.run/'run/candidates.json',[PAPER,p2]);self.classify([PAPER,p2]);assemble_one(self.run,p2);adapt(self.run/'site',self.run,dest)
        result=read(dest/'papers.json')[0];self.assertEqual(result['version'],'v2');self.assertEqual(len(result['versionHistory']),2);self.assertEqual(result['stored_images'],[])
        p=paper_path(self.run,PAPER);(p/'summary.md').write_text('Changed existing version');self.attest(p);assemble_one(self.run,PAPER);res=adapt(self.run/'site',self.run,dest);self.assertEqual(res['failures'],[]);self.assertEqual(read(dest/'papers.json')[0]['version'],'v2')
    def test_overlapping_group_claim_and_restored_claim_rejected(self):
        write(self.run/'run/candidates.json',[PAPER]);split(self.run);task=make_tasks(self.run,'classify')[0];token=claim(self.root,task,'worker')
        shutil.rmtree(state_dir(self.root))
        with self.assertRaises(RuntimeError):claim(self.root,task,'overlap')
        release(self.root,task['cache_key'],token)
    def test_changed_metadata_invalidates_summary_reuse(self):
        from pipeline.arxiv.make_tasks import summary_fingerprint
        p=self.fixture();before=summary_fingerprint(p);meta=read(p/'metadata.json');meta['authors']=['Corrected author'];write(p/'metadata.json',meta)
        self.assertNotEqual(before,summary_fingerprint(p))
        with self.assertRaises(ValueError):assemble_one(self.run,PAPER)
    def test_prepare_does_not_download_or_inventory_figures(self):
        body=('<base href="/html/2610.12345v1/"><article>'+('Scientific text '*60)+'<figure id="F1"><img src="x1.png"><figcaption>Figure 1: evidence</figcaption></figure></article>').encode()
        requested=[]
        def fetcher(url):
            requested.append(url);self.assertEqual(url,'https://arxiv.org/html/2610.12345v1')
            return body,'text/html',url
        prepare_one(self.run,PAPER,fetcher);p=paper_path(self.run,PAPER)
        self.assertEqual(requested,['https://arxiv.org/html/2610.12345v1'])
        self.assertFalse((p/'figures.json').exists());self.assertFalse((p/'figures').exists())
        write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER]);task=make_tasks(self.run,'summary')[0]
        self.assertEqual({Path(f).name for f in task['input_files']},{'source.html','source.json','metadata.json','fulltext.html.txt'})
        self.assertNotIn('figures.json',task['input_files'])

    def test_html_selection_is_linked_without_downloading_or_storage(self):
        from PIL import Image
        from pipeline.arxiv.selected_figures import materialize_selection
        from pipeline.arxiv.tests.test_adversarial_sources import MemoryStore
        raw=io.BytesIO();Image.new('RGB',(12,9),(10,70,130)).save(raw,format='PNG')
        body=('<base href="/html/2610.12345v1/"><article>'+('Scientific text '*60)+'<figure id="F1"><img src="chosen.png"><figcaption>Figure 1: chosen</figcaption></figure><figure id="F2"><img src="unused.png"></figure></article>').encode()
        urls=[]
        def fetcher(url):
            urls.append(url)
            if url.endswith('/chosen.png'):return raw.getvalue(),'image/png',url
            if url.endswith('2610.12345v1'):return body,'text/html',url
            self.fail('Unselected resource fetched: '+url)
        source=prepare_one(self.run,PAPER,fetcher);p=paper_path(self.run,PAPER)
        write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER]);task=make_tasks(self.run,'summary')[0]
        token=claim(self.root,task,'deterministic-test-fixture-not-model-execution')
        chosen={'src':'selected:fig1','number':'Figure 1','caption':'chosen','source':source['url'],'selection':{'kind':'html','element_id':'F1','asset_index':0}}
        write(p/'result.json',{'id':PAPER['id'],'version':'v1','title_ja':'図選択の試験','lead_ja':['問い','方法','結果'],'source_url':PAPER['source_url'],'summary_file':'summary.md','figures':[chosen]})
        (p/'summary.md').write_text('## 検証\n\n選択された原図を確認する。\n\n![chosen](selected:fig1)\n')
        with patch('pipeline.arxiv.assemble.materialize_selection',side_effect=AssertionError('Must finish before extraction')),self.assertRaises(ValueError):
            assemble_one(self.run,PAPER,store=MemoryStore())
        finish(self.root,task,token)
        self.assertEqual(len(urls),1);self.assertFalse((p/'figures').exists())
        store=MemoryStore()
        with patch('pipeline.arxiv.assemble.materialize_selection',side_effect=lambda directory,src,fig:materialize_selection(directory,src,fig,fetcher=fetcher)):
            article=assemble_one(self.run,PAPER,store=store)
        self.assertEqual(urls,[source['url']])
        self.assertEqual(article['stored_images'],[])
        self.assertEqual(article['linked_images'][0]['source_ref'],'selected:fig1')
        self.assertEqual(article['linked_images'][0]['url'],source['url']+'/chosen.png')
        self.assertEqual(store.objects,{})
        self.assertFalse((p/'figures').exists())
        rendered=(self.run/'site'/article['html_path']).read_text()
        self.assertEqual(rendered.count('<figure>'),1);self.assertNotIn('unused.png',rendered)
        self.assertIn('src="'+source['url']+'/chosen.png"',rendered)
        self.assertIn('referrerpolicy="no-referrer"',rendered)

    def test_selected_descriptors_require_order_provenance_and_unique_assets(self):
        p=self.fixture()
        body=('<base href="/html/2610.12345v1/"><article>'+('Synthetic manuscript. '*60)+'<figure id="F1"><img src="same.png"><figcaption>Figure 1: original</figcaption></figure><figure id="F2"><img src="same.png"><figcaption>Figure 2: duplicate asset</figcaption></figure></article>').encode()
        (p/'source.html').write_bytes(body);source=read(p/'source.json');source['sha256']=digest(body);write(p/'source.json',source)
        first={'src':'selected:one','number':'Figure 1','caption':'Original','source':source['url'],'selection':{'kind':'html','element_id':'F1','asset_index':0}}
        second={'src':'selected:two','number':'Figure 2','caption':'Duplicate asset','source':source['url'],'selection':{'kind':'html','element_id':'F2','asset_index':0}}
        output=read(p/'result.json')
        # A stale extraction inventory must not authorize arbitrary image URLs.
        write(p/'figures.json',[{'src':'https://evil.example/a.png','number':'Figure 1'}])
        for figures,markdown in [
            ([first,second],'![one](selected:one)\n\n![two](selected:two)'),
            ([first,second],'![two](selected:two)\n\n![one](selected:one)'),
            ([first],''),
            ([{**first,'source':source['url'].replace('v1','v2')}],'![one](selected:one)'),
            ([{**first,'caption':''}],'![one](selected:one)'),
            ([{**first,'number':'Figure 9'}],'![one](selected:one)'),
            ([{**first,'src':'https://evil.example/a.png'}],'![one](https://evil.example/a.png)'),
        ]:
            with self.subTest(figures=figures,markdown=markdown):
                write(p/'result.json',{**output,'figures':figures});(p/'summary.md').write_text('## 試験\n\n本文を検証する。\n\n'+markdown)
                with self.assertRaises(ValueError):validate_output(p)
        write(p/'result.json',{**output,'figures':[first]});(p/'summary.md').write_text('## 試験\n\n![one](selected:one)')
        validate_output(p)
        self.assertFalse((p/'figures').exists())

    def test_summary_prompt_change_preserves_existing_receipt(self):
        self.fixture();original=assemble_one(self.run,PAPER)
        with (self.root/'pipeline/arxiv/prompts/summary.txt').open('a') as f:f.write('Changed prompt')
        self.assertEqual(assemble_one(self.run,PAPER),original)
    def test_reuse_classification_across_different_grouping(self):
        write(self.run/'run/candidates.json',[PAPER]);paths=split(self.run);path=self.run/paths[0]
        write(path.with_name(path.stem+'.result.json'),[{'id':PAPER['id'],'version':'v1','decision':'include','topics':['3'],'reason':'材料現象'}]);self.finish_classifiers();merge(self.run)
        second=self.root/'data/runs/2026-10-19/next';second.mkdir(parents=True);write(second/'run/candidates.json',[{**PAPER,'date':'2026-10-19'}])
        self.assertEqual(split(second),[]);self.assertEqual(len(merge(second)),1)
    def test_start_freezes_jst_and_collects_before_backlog(self):
        from pipeline.arxiv.workflow import start
        write(self.run/'run/candidates.json',[PAPER])
        def collector(run,date,**kwargs):
            self.assertEqual(date,'2026-10-19');record(run,'collect','failed',{'reason':'fixture network failure'})
        with patch('pipeline.arxiv.collect.collect',side_effect=collector) as call:
            plan=start(self.root,dt.datetime(2026,10,18,18,0,tzinfo=dt.timezone.utc));call.assert_called_once()
        self.assertEqual(plan['backlog'][0]['run_id'],'test');self.assertEqual(read(Path(plan['today'])/'ledger.json')['today_at_start'],'2026-10-19')
    def test_completion_only_after_real_release(self):
        self.fixture();write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER]);record(self.run,'collect','complete');article=assemble_one(self.run,PAPER);adapt(self.run/'site',self.run,self.root/'data/articles')
        self.assertEqual(recompute_completion(self.run),'incomplete');write(self.run/'release.json',{'status':'succeeded','articles':[{'article_id':PAPER['id'],'version':'v1','content_sha256':article['content_sha256']}]});self.assertEqual(recompute_completion(self.run),'complete')
        newer={**PAPER,'version':'v2','source_url':PAPER['source_url'][:-1]+'2'};self.fixture(newer);write(self.run/'run/candidates.json',[PAPER,newer]);self.classify([PAPER,newer]);assemble_one(self.run,newer);adapt(self.run/'site',self.run,self.root/'data/articles')
        self.assertEqual(recompute_completion(self.run),'incomplete');self.assertEqual(read(self.run/'ledger.json')['pending'],[{'id':PAPER['id'],'version':'v2','stage':'publication'}])
    def finish_classifiers(self):
        for task in make_tasks(self.run,'classify'):
            token=claim(self.root,task,'synthetic-classifier-output');finish(self.root,task,token)
    def test_classifier_output_and_merged_results_cannot_bypass_finish_proof(self):
        write(self.run/'run/candidates.json',[PAPER]);path=self.run/split(self.run)[0]
        include={'id':PAPER['id'],'version':'v1','decision':'include','topics':['3'],'reason':'synthetic'}
        output=path.with_name(path.stem+'.result.json');write(output,[include])
        self.assertEqual(merge(self.run),[])
        self.finish_classifiers();write(output,[{**include,'decision':'exclude','topics':[]}])
        self.assertEqual(merge(self.run),[])
        write(output,[include]);self.assertEqual(len(merge(self.run)),1)
        write(self.run/'run/classifications.json',[{**include,'decision':'exclude','topics':[]}]);record(self.run,'collect','complete')
        self.assertEqual(recompute_completion(self.run),'incomplete');self.assertEqual(len(split(self.run)),1)
    def classify(self,papers,decision='include'):
        from pipeline.arxiv.split_merge import classification_proof,prompt_hash
        write(self.run/'run/classifications.json',[{'id':p['id'],'version':p['version'],'decision':decision,'topics':[] if decision=='exclude' else ['3'],'reason':'synthetic classification'} for p in papers])
        write(self.run/'run/classification-proofs.json',{'/'.join(identity(p)):classification_proof(p,r,prompt_hash(self.run),self.run) for p,r in zip(papers,read(self.run/'run/classifications.json'))})
        write(self.run/'run/selected.json',[{**p,**r} for p,r in zip(papers,read(self.run/'run/classifications.json')) if r['decision']!='exclude'])
    def test_changed_prompt_preserves_unpublished_excluded_decision(self):
        write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER],'exclude');record(self.run,'collect','complete')
        self.assertEqual(recompute_completion(self.run),'complete')
        with (self.root/'pipeline/arxiv/prompts/relevance.txt').open('a') as f:f.write(' new rule')
        self.assertEqual(split(self.run),[]);self.assertEqual(recompute_completion(self.run),'complete')
        self.assertEqual(read(self.run/'ledger.json')['pending'],[])
    def test_published_immutable_version_survives_prompt_changes_and_adapter_retry(self):
        self.fixture();write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER]);record(self.run,'collect','complete')
        article=assemble_one(self.run,PAPER);adapt(self.run/'site',self.run,self.root/'data/articles')
        write(self.run/'release.json',{'status':'succeeded','articles':[{'article_id':PAPER['id'],'version':'v1','content_sha256':article['content_sha256']}]})
        for name in ('relevance.txt','summary.txt'):
            with (self.root/'pipeline/arxiv/prompts'/name).open('a') as f:f.write(' changed prompt')
        self.assertEqual(split(self.run),[]);self.assertEqual(len(merge(self.run)),1)
        self.assertEqual(recompute_completion(self.run),'complete')
        adapt(self.run/'site',self.run,self.root/'data/articles')
        batch=read(self.root/'data/articles/batch-status.json');self.assertEqual(batch['published_count'],1);self.assertEqual(batch['publication_status'],'succeeded')
    def test_append_candidate_invalidates_terminal_run_and_next_day_backlog(self):
        from pipeline.arxiv.workflow import start
        write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER],'exclude');record(self.run,'collect','complete');self.assertEqual(recompute_completion(self.run),'complete')
        item='<item><title>Another synthetic study</title><link>https://arxiv.org/abs/2610.12346v1</link><description>Other abstract</description></item>'
        collect(self.run,'2026-10-18',fetcher=lambda u:self.rss(item));self.assertEqual(read(self.run/'ledger.json')['status'],'incomplete')
        with patch('pipeline.arxiv.collect.collect',return_value=[]):plan=start(self.root,dt.datetime(2026,10,18,16,tzinfo=dt.timezone.utc))
        self.assertIn('test',[r['run_id'] for r in plan['backlog']])
    def test_finish_rejects_input_tampering_but_ignores_new_default_prompt(self):
        write(self.run/'run/candidates.json',[PAPER]);split(self.run);task=make_tasks(self.run,'classify')[0];token=claim(self.root,task,'worker')
        write(task['output_files'][0],[{'id':PAPER['id'],'version':'v1','decision':'include','topics':['3'],'reason':'synthetic'}])
        original=read(task['input_files'][0]);write(task['input_files'][0],[{**PAPER,'title':'Changed'}])
        with self.assertRaisesRegex(ValueError,'inputs or prompt changed'):finish(self.root,task,token)
        write(task['input_files'][0],original)
        with (self.root/'pipeline/arxiv/prompts/relevance.txt').open('a') as f:f.write(' changed')
        finish(self.root,task,token)
        self.assertTrue((state_dir(self.root)/'results'/task['cache_key']/'receipt.json').exists())
        self.assertEqual(merge(self.run)[0]['decision'],'include')
    def test_summary_finish_binds_inputs_and_allows_intended_metadata_output(self):
        body=('<article>'+('Synthetic manuscript text. '*50)+'</article>').encode()
        prepare_one(self.run,PAPER,lambda url:(body,'text/html',url));write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER])
        task=make_tasks(self.run,'summary')[0];token=claim(self.root,task,'synthetic-model-output')
        p=paper_path(self.run,PAPER);metadata=read(p/'metadata.json');metadata['authors']=['Synthetic Author'];write(p/'metadata.json',metadata)
        write(p/'result.json',{'id':PAPER['id'],'version':'v1','title_ja':'試験記事','lead_ja':['問い','方法','結果'],'source_url':PAPER['source_url'],'summary_file':'summary.md','figures':[]})
        (p/'summary.md').write_text('## 検証\n\n人工的な文章を説明する。')
        finish(self.root,task,token);self.assertEqual(assemble_one(self.run,PAPER)['authors'],['Synthetic Author'])
        cleanup(p);self.assertEqual(make_tasks(self.run,'summary'),[])
    def test_summary_task_rejects_changed_prepared_source(self):
        body=('<article>'+('Synthetic source. '*60)+'</article>').encode()
        prepare_one(self.run,PAPER,lambda url:(body,'text/html',url));write(self.run/'run/candidates.json',[PAPER]);self.classify([PAPER])
        p=paper_path(self.run,PAPER);(p/'source.html').write_text('tampered')
        self.assertEqual(make_tasks(self.run,'summary'),[])
        self.assertIn('hash changed', (self.run/'failures.jsonl').read_text())
    def test_cli_frozen_invocation_survives_midnight(self):
        from pipeline.arxiv import workflow;import contextlib
        run=self.root/'data/runs/2026-10-18/midnight'
        def collector(path,date,**kwargs):
            ledger=read(path/'ledger.json');self.assertEqual(ledger['today_at_start'],'2026-10-18');self.assertEqual(date,'2026-10-18');record(path,'collect','complete');return []
        argv=['workflow.py','collect','--run-dir',str(run),'--date','2026-10-18','--started-at','2026-10-18T23:59:59+0900']
        with patch.object(sys,'argv',argv),patch('pipeline.arxiv.collect.collect',side_effect=collector),contextlib.redirect_stdout(io.StringIO()):workflow.cli()
    def test_cli_assemble_reports_missing_receipt_and_nonzero(self):
        from pipeline.arxiv import workflow;import contextlib
        p=self.fixture();(p/'model-output-receipt.json').unlink();write(self.run/'run/selected.json',[PAPER]);out=io.StringIO()
        with patch.object(sys,'argv',['workflow.py','assemble','--run-dir',str(self.run)]),contextlib.redirect_stdout(out),self.assertRaises(SystemExit) as result:workflow.cli()
        self.assertEqual(result.exception.code,1);report=json.loads(out.getvalue());self.assertEqual(report['status'],'partial_failure');self.assertIn('run finish',report['failures'][0]['details']['error'])
    def test_bad_summary_and_lead_rejected(self):
        p=self.fixture();(p/'summary.md').write_text('<script>alert(1)</script>')
        with self.assertRaises(ValueError):validate_output(p)
        (p/'summary.md').write_text('研究の問い')
        with self.assertRaises(ValueError):validate_output(p)
    def test_webp_upload_readback_and_corruption(self):
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(23,17),(1,22,145)).save(data,format='PNG')
        class Store:
            def put(self,k,d,m):self.data=d;self.mime=m
            def get(self,k):return self.data,self.mime
        fig={'src':'figures/a.png'};entry=store_figure(PAPER,fig,{},self.run,Store(),loader=lambda p,s:(data.getvalue(),'image/png'));self.assertEqual((entry['width'],entry['height']),(23,17));self.assertTrue(entry['verified_at'])
        class Corrupt(Store):
            def get(self,k):return self.data+b'changed',self.mime
        with self.assertRaises(ValueError):store_figure(PAPER,fig,{},self.run,Corrupt(),loader=lambda p,s:(data.getvalue(),'image/png'))
    def test_svg_preserves_original_and_rejects_external_resources(self):
        svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="12"><defs><path id="line" d="M0 0L20 12"/></defs><use href="#line" stroke="black"/></svg>'
        class Store:
            def __init__(self):self.data={}
            def put(self,k,d,m):self.data[k]=(d,m)
            def get(self,k):return self.data[k]
        store=Store();entry=store_figure(PAPER,{'src':'figures/test.svg'},{},self.run,store,loader=lambda p,s:(svg,'image/svg+xml'))
        self.assertEqual((entry['width'],entry['height']),(20,12));self.assertEqual(store.get(entry['original_object_key'])[0],svg)
        unsafe=svg.replace(b'href="#line"',b'href="https://evil.invalid/a.svg"')
        with self.assertRaises(ValueError):store_figure(PAPER,{'src':'figures/test.svg'},{},self.run,Store(),loader=lambda p,s:(unsafe,'image/svg+xml'))
    def test_images_unknown_source_and_unsafe_markdown(self):
        p=self.fixture();(p/'summary.md').write_text('![fig](https://evil.test/x.png)')
        with self.assertRaises(ValueError):validate_output(p)
        self.assertNotIn('javascript:',render_narrative('[bad](javascript:alert(1))',[],[]))

if __name__=='__main__':unittest.main()
