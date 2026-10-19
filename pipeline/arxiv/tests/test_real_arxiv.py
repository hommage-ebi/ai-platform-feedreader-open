"""Opt-in real licensed sources, deterministic selections, and no model execution.

This checks the exact source -> selected-only extraction -> lossless storage ->
article path. Test-generated output fixtures exercise claim/finish receipts; they
are not model-generated metadata, summaries, translations, or localization.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from pipeline.shared.runtime import digest, read, write
from pipeline.arxiv.common import identity, paper_path
from pipeline.arxiv.prepare import fetch, prepare_one
from pipeline.arxiv.selected_figures import materialize_selection
from pipeline.arxiv.make_tasks import make_tasks, claim, finish
from pipeline.arxiv.assemble import assemble_one

FIXTURE = Path(__file__).parent / 'fixtures/mistral-7b-v1'
MANIFEST = FIXTURE / 'manifest.json'

class MemoryStore:
    def __init__(self): self.objects = {}
    def put(self, key, data, mime): self.objects[key] = data, mime
    def get(self, key): return self.objects[key]

@unittest.skipUnless(os.environ.get('ARXIV_REAL_TEST') == '1', 'set ARXIV_REAL_TEST=1 for licensed real-paper integration')
class RealArxivTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.selections = json.loads((FIXTURE / 'selections.json').read_text())['figures']
        cls.cache = Path(os.environ.get('ARXIV_REAL_CACHE', str(Path(__file__).resolve().parents[3] / '.cache/arxiv/real-mistral-v1')))
        cls.cache.mkdir(parents=True, exist_ok=True)
        cls.assets = {}
        for asset in cls.manifest['assets']:
            path = cls.cache / asset['file']
            if not path.exists():
                if os.environ.get('ARXIV_REAL_DOWNLOAD') != '1':
                    raise RuntimeError('Missing fixture; explicitly set ARXIV_REAL_DOWNLOAD=1 to download hash-pinned official files')
                body, mime, final = fetch(asset['url'])
                if mime != asset['mime'] or final != asset['url']:
                    raise ValueError('Fixture response differs from pinned source')
                if hashlib.sha256(body).hexdigest() != asset['sha256']:
                    raise ValueError('Fixture hash changed; review source, do not silently repin')
                path.write_bytes(body)
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != asset['sha256']:
                raise ValueError('Cached fixture hash mismatch')
            cls.assets[asset['url']] = (body, asset['mime'], asset['url'])
        cls.paper = {k: cls.manifest[k] for k in ('id','version','title','source_url')}
        from bs4 import BeautifulSoup
        abstract=BeautifulSoup(cls.assets['https://arxiv.org/html/2310.06825v1'][0],'html.parser').select_one('.ltx_abstract')
        if abstract is None or not abstract.get_text(' ',strip=True):raise ValueError('Pinned original HTML lacks an abstract')
        cls.paper['abstract']=abstract.get_text(' ',strip=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / 'data/runs/2026-10-18/real-source-fixture'
        self.run.mkdir(parents=True)
        prompts = self.root / 'pipeline/arxiv/prompts'
        prompts.mkdir(parents=True)
        for name in ('summary.txt','relevance.txt'):
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'prompts' / name, prompts / name)
        shutil.copytree(Path(__file__).resolve().parents[1]/'config',self.root/'pipeline/arxiv/config')
        shutil.copytree(Path(__file__).resolve().parents[2]/'config',self.root/'pipeline/config')
        # This integration fixture must not depend on the user's current interests.
        shutil.copyfile(Path(__file__).parent/'fixtures/interests.json',self.root/'pipeline/config/interests.json')
        self.environment = patch.dict(os.environ, {'PROJECT_ROOT': str(self.root)})
        self.environment.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.environment.stop)

    def selected_outputs(self, source):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.assets['https://arxiv.org/html/2310.06825v1'][0], 'html.parser')
        figures = []
        for index, selected in enumerate(self.selections, 1):
            caption = soup.find(id=selected['element_id']).figcaption.get_text(' ', strip=True)
            self.assertIn(selected['caption_phrase'], caption)
            if source['kind'] == 'html':
                selection = {'kind':'html', 'element_id':selected['element_id'], 'asset_index':selected['asset_index']}
            else:
                selection = {'kind':'pdf', 'page':selected['page'], 'bbox':selected['bbox'], 'units':'pt', 'page_bounds':[0,0,612,792]}
            figures.append({'src':f'selected:fig{index}', 'number':selected['number'], 'caption':caption, 'source':source['url'], 'selection':selection})
        return figures

    def assemble_fixture(self, source, fetcher):
        directory = paper_path(self.run, self.paper)
        from pipeline.arxiv.split_merge import classification_proof,prompt_hash
        write(self.run/'run/candidates.json',[self.paper])
        classification={'id':self.paper['id'],'version':self.paper['version'],'decision':'include','topics':['6'],'reason':'Licensed deterministic integration fixture'}
        write(self.run/'run/classifications.json',[classification])
        write(self.run/'run/classification-proofs.json',{'/'.join(identity(self.paper)):classification_proof(self.paper,classification,prompt_hash(self.run,self.paper),self.run)})
        write(self.run/'run/selected.json', [{**self.paper,**classification}])
        task = make_tasks(self.run, 'summary')[0]
        names = {Path(p).name for p in task['input_files']}
        expected = {'source.json','metadata.json','source.'+source['kind']}
        if source['kind'] == 'html': expected.add('fulltext.html.txt')
        self.assertEqual(names, expected)
        self.assertFalse((directory/'figures.json').exists())
        self.assertFalse((directory/'figures').exists())
        figures = self.selected_outputs(source)
        write(directory/'result.json', {'id':self.paper['id'], 'version':'v1', 'title_ja':'実資料の決定論的な試験', 'summary_ja':'図の参照と保存経路を検証するための人工的な試験出力', 'lead_ja':['試験の対象','固定した選択','保存の検証'], 'source_url':self.paper['source_url'], 'summary_file':'summary.md', 'figures':figures})
        (directory/'summary.md').write_text('## 実資料を用いた試験\n\n原図の保存と表示を決定論的に検証する。\n\n'+'\n\n'.join(f'![{f["number"]}]({f["src"]})' for f in figures)+'\n')
        token = claim(self.root, task, 'deterministic-regression-fixture-not-model-execution')
        finish(self.root, task, token)
        self.assertFalse((directory/'figures').exists())
        store = MemoryStore()
        with patch('pipeline.arxiv.assemble.materialize_selection', side_effect=lambda p,s,f:materialize_selection(p,s,f,fetcher=fetcher)):
            article = assemble_one(self.run, read(self.run/'run/selected.json')[0], store=store)
        self.assertEqual([s['source_ref'] for s in article['stored_images']], [f'selected:fig{i}' for i in range(1,7)])
        self.assertEqual(len([key for key in store.objects if key.endswith('.webp')]), 6)
        html = (self.run/'site'/article['html_path']).read_text()
        self.assertEqual(html.count('<figure>'), 6)
        self.assertNotIn('selected:', html)
        self.assertNotIn('https://arxiv.org/html/2310.06825v1/images/', html)
        for figure in figures: self.assertIn(figure['number'], html)
        self.assertFalse((directory/'figures.json').exists())
        return directory, article, store

    def test_real_html_six_explicit_selections_only(self):
        from bs4 import BeautifulSoup
        requested = []
        def fetcher(url):
            requested.append(url)
            return self.assets[url]
        source = prepare_one(self.run, self.paper, fetcher=fetcher)
        self.assertEqual(source['kind'], 'html')
        self.assertEqual(requested, [source['url']])
        directory, article, store = self.assemble_fixture(source, fetcher)
        self.assertEqual(requested, [source['url']]+[a['url'] for a in self.manifest['assets'][2:]])
        for stored, asset in zip(article['stored_images'], self.manifest['assets'][2:]):
            self.assertEqual(stored['source_sha256'], asset['sha256'])
            self.assertEqual(stored['original_byte_size'], asset['bytes'])
        vector = article['stored_images'][2]
        self.assertEqual(store.get(vector['original_object_key'])[0], self.assets[self.manifest['assets'][4]['url']][0])
        soup = BeautifulSoup((directory/'source.html').read_text(), 'html.parser')
        self.assertEqual(len([f for f in soup.find_all('figure') if '.F' in f.get('id','')]), 6)
        self.assertEqual(len([f for f in soup.find_all('figure') if '.T' in f.get('id','')]), 5)
        self.assertEqual(len(soup.find_all('math')), 41)
        self.assertEqual(soup.find('math').get('alttext'), 'W')
        text = (directory/'fulltext.html.txt').read_text()
        self.assertIn('Agieval: A human-centric benchmark for evaluating foundation models.', text)
        self.assertNotIn('Report GitHub Issue', text)
        # Malformed author markup is not silently treated as verified affiliation.
        meta = read(directory/'metadata.json')
        self.assertEqual(meta['affiliations'], [])
        self.assertIn('authors', meta['missing_fields'])
        self.assertIn('affiliations', meta['missing_fields'])

    def test_real_forced_pdf_six_explicit_crops(self):
        import fitz
        from PIL import Image
        requested = []
        def force_pdf(url):
            requested.append(url)
            if '/html/' in url: raise ValueError('Deliberately forced PDF fallback')
            return self.assets[url]
        source = prepare_one(self.run, self.paper, fetcher=force_pdf)
        self.assertEqual(source['kind'], 'pdf')
        self.assertIn('Deliberately forced', source['html_failure'])
        directory, article, store = self.assemble_fixture(source, lambda url:self.fail('PDF crop must not fetch an asset'))
        self.assertEqual(requested, ['https://arxiv.org/html/2310.06825v1','https://arxiv.org/pdf/2310.06825v1'])
        with fitz.open(directory/'source.pdf') as pdf:
            self.assertEqual(len(pdf), 9)
            header = pdf[0].get_text().split('Abstract')[0]
            authors = [' '.join(x.split()) for x in header.split('Mistral 7B',1)[1].strip().split(',')]
            expected = self.manifest['attribution'].split('. Mistral 7B (2023)')[0].split(', ')
            self.assertEqual(authors, expected)
            self.assertEqual(len(authors), 18)
            self.assertIn('Agieval: A human-centric benchmark', pdf[-1].get_text())
            self.assertIn('window_size', pdf[1].get_text())
            self.assertIn('32000', pdf[1].get_text())
            for selected, stored in zip(self.selections, article['stored_images']):
                page = pdf[selected['page']-1]
                self.assertEqual(list(page.rect), [0,0,612,792])
                expected = page.get_pixmap(matrix=fitz.Matrix(3,3), clip=fitz.Rect(selected['bbox']), alpha=False)
                image = Image.open(io.BytesIO(store.get(stored['object_key'])[0])).convert('RGB')
                self.assertEqual(image.size, (expected.width,expected.height))
                self.assertEqual(image.tobytes(), expected.samples)
                self.assertNotIn('original_object_key', stored)
                self.assertEqual(stored['selection']['bbox'], selected['bbox'])
                self.assertEqual(stored['source_document_sha256'], source['sha256'])
                self.assertEqual(stored['rasterization']['dpi'], 216)
                self.assertGreater(stored['width'], 500)
        # Whole Figure 6 screenshot, never only its small decorative icons.
        self.assertGreater(article['stored_images'][5]['height'], 1800)
        self.assertGreater(article['stored_images'][5]['width'], 1100)
        self.assertLess(self.selections[1]['bbox'][3], 141.32)
        self.assertLess(self.selections[5]['bbox'][3], 690.20)
        self.assertEqual(len(list((directory/'figures').glob('*.png'))), 6)
        self.assertEqual(len(list((directory/'figures').glob('*.svg'))), 0)
        self.assertEqual(len(store.objects), 6)

if __name__ == '__main__':
    unittest.main()
