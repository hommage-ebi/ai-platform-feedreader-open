"""Adversarial source isolation and decoded-pixel integrity regressions."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from pipeline.shared.runtime import digest, read
from pipeline.arxiv.common import paper_path
from pipeline.shared.image_store import store_figure
from pipeline.arxiv.prepare import prepare_one
from pipeline.arxiv.selected_figures import validate_selection,materialize_selection

PAPER={'id':'2610.12345','version':'v1','title':'Synthetic source test','abstract':'Synthetic abstract', 'source_url':'https://arxiv.org/abs/2610.12345v1'}

class MemoryStore:
    def __init__(self):self.objects={}
    def put(self,key,data,mime):self.objects[key]=(data,mime)
    def get(self,key):return self.objects[key]

class AdversarialSourceTests(unittest.TestCase):
    def check_pixels(self,image):
        from PIL import Image
        data=io.BytesIO();image.save(data,format='PNG');raw=data.getvalue()
        store=MemoryStore()
        entry=store_figure(PAPER,{'src':'figures/test.png'},{},Path('/unused'),store,loader=lambda p,s:(raw,'image/png'))
        recovered=Image.open(io.BytesIO(store.get(entry['object_key'])[0])).convert('RGBA')
        expected=Image.open(io.BytesIO(raw)).convert('RGBA')
        self.assertEqual(recovered.size,expected.size)
        self.assertEqual(recovered.tobytes(),expected.tobytes())
    def test_palette_transparency_is_lossless(self):
        from PIL import Image
        image=Image.new('P',(2,2),0);image.putpalette([255,0,0]+[0,0,0]*255)
        image.info['transparency']=0;self.check_pixels(image)
    def test_rgb_color_key_transparency_is_lossless(self):
        from PIL import Image
        image=Image.new('RGB',(2,2),(255,0,0));image.putpixel((1,1),(0,0,255))
        image.info['transparency']=(255,0,0);self.check_pixels(image)
    def test_high_bit_depth_image_is_not_silently_clipped(self):
        from PIL import Image
        image=Image.new('I;16',(2,2));image.putpixel((0,0),1000)
        data=io.BytesIO();image.save(data,format='PNG');store=MemoryStore()
        with self.assertRaisesRegex(ValueError,'High-bit-depth'):
            store_figure(PAPER,{'src':'figures/test.png'},{},Path('/unused'),store,loader=lambda p,s:(data.getvalue(),'image/png'))
        self.assertEqual(store.objects,{})
    def test_animated_image_is_not_silently_reduced_to_first_frame(self):
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(2,2),'red').save(data,format='GIF',save_all=True,append_images=[Image.new('RGB',(2,2),'blue')],duration=100,loop=0)
        store=MemoryStore()
        with self.assertRaisesRegex(ValueError,'Animated source'):
            store_figure(PAPER,{'src':'figures/test.gif'},{},Path('/unused'),store,loader=lambda p,s:(data.getvalue(),'image/gif'))
        self.assertEqual(store.objects,{})
    def test_empty_article_with_long_navigation_falls_back_to_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            body=('<html><nav>'+('Navigation '*100)+'</nav><article></article></html>').encode()
            requested=[]
            def fetcher(url):
                requested.append(url)
                return (b'%PDF-synthetic','application/pdf',url) if '/pdf/' in url else (body,'text/html',url)
            source=prepare_one(tmp,PAPER,fetcher=fetcher)
            self.assertEqual(source['kind'],'pdf');self.assertEqual(len(requested),2)
            self.assertIn('Incomplete/abstract-only HTML',source['html_failure'])
    def test_inline_svg_is_materialized_only_when_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,directory=self.html_source(tmp,'<figure id="F1"><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path d="M0 0L10 10"/></svg></figure>')
            self.assertEqual(source['fulltext_sha256'],digest((directory/'fulltext.html.txt').read_bytes()))
            self.assertFalse((directory/'figures').exists());self.assertFalse((directory/'figures.json').exists())
            figure=self.html_figure(source)
            validate_selection(directory,source,figure)
            self.assertFalse((directory/'figures').exists())
            candidate=materialize_selection(directory,source,figure,fetcher=lambda url:self.fail('Inline SVG must not fetch'))
            self.assertEqual(candidate['source_sha256'],digest((directory/candidate['local_file']).read_bytes()))
            self.assertEqual(candidate['source_document_sha256'],source['sha256'])
            self.assertTrue(candidate['local_file'].endswith('.svg'))

    def html_source(self,tmp,markup,base='<base href="/html/2610.12345v1/">'):
        body=(base+'<article><p>'+('Manuscript text. '*60)+'</p>'+markup+'</article>').encode()
        source=prepare_one(tmp,PAPER,fetcher=lambda url:(body,'text/html',url))
        return source,paper_path(tmp,PAPER)

    def html_figure(self,source,**selection):
        return {'src':'selected:fig1','number':'Figure 1','caption':'Synthetic fixture','source':source['url'],'selection':{'kind':'html','element_id':'F1','asset_index':0,**selection}}

    def test_bad_html_selections_fail_before_fetch_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,directory=self.html_source(tmp,'<figure id="F1"><img src="chosen.png"></figure>')
            for changes in ({'element_id':'missing'},{'asset_index':-1},{'asset_index':True},{'asset_index':1.0},{'asset_index':1},{'asset_index':'0'},{'kind':'pdf'},{'extra':'ignored'}):
                with self.subTest(changes=changes),self.assertRaises(ValueError):
                    materialize_selection(directory,source,self.html_figure(source,**changes),fetcher=lambda url:self.fail('Invalid selection fetched'))
            self.assertFalse((directory/'figures').exists())
            for src in ['https://arxiv.org/html/2610.12345v1/chosen.png','figures/chosen.png','selected:../a','selected:','selected:a/b','selected:a?x','selected:'+('a'*81)]:
                with self.subTest(src=src),self.assertRaises(ValueError):
                    validate_selection(directory,source,{**self.html_figure(source),'src':src})

    def test_duplicate_ids_or_outside_manuscript_are_rejected(self):
        for markup in ['<figure id="F1"><img src="a.png"></figure><figure id="F1"><img src="b.png"></figure>','<figure id="F2"><img src="a.png"></figure>']:
            with tempfile.TemporaryDirectory() as tmp:
                source,directory=self.html_source(tmp,markup,base='<figure id="F1"><img src="outside.png"></figure>')
                with self.assertRaises(ValueError):validate_selection(directory,source,self.html_figure(source))

    def test_asset_index_addresses_only_exact_element(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,directory=self.html_source(tmp,'<figure id="F1"><img src="unused.png"><object type="image/svg+xml" data="chosen.svg"></object></figure><figure id="F2"><img src="elsewhere.png"></figure>')
            requested=[]
            def fetcher(url):requested.append(url);return b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="3"/>','image/svg+xml',url
            materialize_selection(directory,source,self.html_figure(source,asset_index=1),fetcher=fetcher)
            self.assertEqual(requested,[source['url']+'/chosen.svg'])

    def test_html_selection_cannot_cross_figures_or_renumber_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            markup='<div id="container"><figure id="F1"><img src="one.png"><figcaption>Figure 8: original number</figcaption></figure><figure id="F2"><img src="two.png"></figure></div><img id="loose" src="outside.png">'
            source,directory=self.html_source(tmp,markup)
            for selection in ({'element_id':'container'},{'element_id':'loose'},{}):
                with self.subTest(selection=selection),self.assertRaises(ValueError):validate_selection(directory,source,self.html_figure(source,**selection))
            for number in ('Figure 8','Figure 8 (a)'):
                validate_selection(directory,source,{**self.html_figure(source),'number':number})

    def test_same_version_asset_policy_rejects_untrusted_references(self):
        refs=['https://evil.example/a.png','https://arxiv.org/html/2610.12345v2/a.png','https://arxiv.org/html/2610.99999v1/a.png','http://arxiv.org/html/2610.12345v1/a.png','https://user:password@arxiv.org/html/2610.12345v1/a.png','https://arxiv.org:443/html/2610.12345v1/a.png','../a.png','%2e%2e/a.png','%252e%252e/a.png','a.png?download=1','a.png#fragment','file:///tmp/a.png','data:image/png;base64,eA==','a\\b.png','']
        for ref in refs:
            with tempfile.TemporaryDirectory() as tmp,self.subTest(ref=ref):
                source,directory=self.html_source(tmp,'<figure id="F1"><img src="'+ref+'"></figure>')
                with self.assertRaises(ValueError):materialize_selection(directory,source,self.html_figure(source),fetcher=lambda url:self.fail('Untrusted reference fetched'))
        for base in ['https://evil.example/','/html/2610.12345v2/','/html/2610.12345v1/nested/']:
            with tempfile.TemporaryDirectory() as tmp,self.subTest(base=base):
                source,directory=self.html_source(tmp,'<figure id="F1"><img src="a.png"></figure>',base='<base href="'+base+'">')
                with self.assertRaises(ValueError):validate_selection(directory,source,self.html_figure(source))

    def test_object_types_and_asset_redirects_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,directory=self.html_source(tmp,'<figure id="F1"><object type="text/html" data="a.html"></object></figure>')
            with self.assertRaises(ValueError):validate_selection(directory,source,self.html_figure(source))
        with tempfile.TemporaryDirectory() as tmp:
            source,directory=self.html_source(tmp,'<figure id="F1"><img src="a.png"></figure>')
            for final,mime in [('https://evil.example/a.png','image/png'),(source['url']+'/other.png','image/png'),(source['url']+'/a.png','text/html')]:
                with self.subTest(final=final,mime=mime),self.assertRaises(ValueError):
                    materialize_selection(directory,source,self.html_figure(source),fetcher=lambda url:(b'untrusted',mime,final))
            self.assertEqual(list((directory/'figures').iterdir()),[])

    def test_modified_original_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,directory=self.html_source(tmp,'<figure id="F1"><img src="a.png"></figure>')
            original=directory/'source.html';saved=original.read_bytes();original.write_bytes(saved+b'changed')
            with self.assertRaisesRegex(ValueError,'hash changed'):validate_selection(directory,source,self.html_figure(source))
            original.unlink();outside=Path(tmp)/'outside.html';outside.write_bytes(saved);original.symlink_to(outside)
            with self.assertRaises(ValueError):validate_selection(directory,source,self.html_figure(source))
            original.unlink();original.write_bytes(saved)
            out=Path(tmp)/'outside-assets';out.mkdir();(directory/'figures').symlink_to(out,target_is_directory=True)
            with self.assertRaises(ValueError):materialize_selection(directory,source,self.html_figure(source),fetcher=lambda url:self.fail('Symlink directory fetched'))
            self.assertEqual(list(out.iterdir()),[])

    def pdf_source(self,tmp,rotation=0):
        import fitz
        doc=fitz.open();page=doc.new_page(width=240,height=180)
        page.draw_rect(fitz.Rect(20,30,100,90),color=(1,0,0),fill=(1,0,0));page.draw_rect(fitz.Rect(120,40,200,130),color=(0,0,1),fill=(0,0,1))
        page.insert_text((25,20),'Synthetic PDF selection fixture');page.set_rotation(rotation)
        body=doc.tobytes();doc.close()
        source=prepare_one(tmp,PAPER,fetcher=lambda url:(body,'application/pdf',url))
        return source,paper_path(tmp,PAPER)

    def pdf_figure(self,source,rotation=0,**changes):
        bounds=[0,0,180,240] if rotation in (90,270) else [0,0,240,180]
        return {'src':'selected:fig1','number':'Figure 1','caption':'Synthetic PDF fixture','source':source['url'],'selection':{'kind':'pdf','page':1,'bbox':[15,25,105,95],'units':'pt','page_bounds':bounds,**changes}}

    def test_pdf_validation_rejects_invalid_coordinates_without_crops(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,directory=self.pdf_source(tmp)
            cases=[{'page':0},{'page':2},{'page':True},{'page':1.0},{'units':'px'},{'bbox':[-1,0,50,50]},{'bbox':[0,0,241,100]},{'bbox':[0,0,0,100]},{'bbox':[10,10,5,20]},{'bbox':[0,0,float('nan'),50]},{'bbox':[0,0,float('inf'),50]},{'bbox':[False,0,50,50]},{'bbox':[0,0,50]},{'bbox':'0,0,50,50'},{'page_bounds':[0,0,612,792]},{'page_bounds':[0,0,240,float('inf')]},{'extra':0}]
            for changes in cases:
                with self.subTest(changes=changes),self.assertRaises(ValueError):
                    materialize_selection(directory,source,self.pdf_figure(source,**changes))
            self.assertFalse((directory/'figures').exists());self.assertFalse((directory/'figures.json').exists())

    def test_pdf_selected_crop_pixels_cover_rotation_and_cropbox(self):
        import fitz
        from PIL import Image
        for crop in (False,True):
            for rotation in (0,90,180,270):
                with tempfile.TemporaryDirectory() as tmp,self.subTest(crop=crop,rotation=rotation):
                    pdf=fitz.open();page=pdf.new_page(width=400,height=500)
                    quadrants=[((0,0,200,250),(1,0,0)),((200,0,400,250),(0,1,0)),((0,250,200,500),(0,0,1)),((200,250,400,500),(1,1,0))]
                    for rect,color in quadrants:page.draw_rect(rect,color=color,fill=color)
                    page.insert_text((60,90),'TOP LEFT',fontsize=25)
                    if crop:page.set_cropbox(fitz.Rect(40,50,360,450))
                    page.set_rotation(rotation);body=pdf.tobytes();pdf.close()
                    source=prepare_one(tmp,PAPER,fetcher=lambda url:(body,'application/pdf',url));directory=paper_path(tmp,PAPER)
                    with fitz.open(directory/'source.pdf') as doc:
                        page=doc[0];bounds=page.rect;clip=fitz.Rect(10,15,bounds.width*.6,bounds.height*.7)
                        figure=self.pdf_figure(source,bbox=list(clip),page_bounds=list(bounds))
                        expected=page.get_pixmap(matrix=fitz.Matrix(3,3),clip=clip,alpha=False)
                    validate_selection(directory,source,figure);self.assertFalse((directory/'figures').exists())
                    candidate=materialize_selection(directory,source,figure)
                    raw=(directory/candidate['local_file']).read_bytes()
                    self.assertEqual(candidate['source_sha256'],digest(raw));self.assertEqual(candidate['rasterization']['dpi'],216)
                    self.assertNotIn('original_src',candidate)
                    self.assertEqual(list((directory/'figures').glob('*.svg')),[])
                    with Image.open(io.BytesIO(raw)) as image:
                        self.assertEqual(image.size,(expected.width,expected.height))
                        self.assertEqual(image.convert('RGB').tobytes(),expected.samples)
                    store=MemoryStore();entry=store_figure(PAPER,figure,candidate,directory,store)
                    self.assertNotIn('original_object_key',entry)
                    self.assertEqual(len(store.objects),1)
                    self.assertEqual(entry['source_sha256'],candidate['source_sha256'])
                    self.assertEqual(entry['selection'],figure['selection'])

    def test_fulltext_excludes_page_chrome_and_active_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            body=('<nav>OUTSIDE NAV</nav><article><nav>INSIDE NAV</nav><script>ACTIVE SCRIPT</script><p>'+('Manuscript text. '*60)+'</p></article><footer>OUTSIDE FOOTER</footer>').encode()
            source=prepare_one(tmp,PAPER,fetcher=lambda url:(body,'text/html',url))
            self.assertEqual(source['kind'],'html')
            extracted=(paper_path(tmp,PAPER)/'fulltext.html.txt').read_text()
            self.assertNotIn('NAV',extracted);self.assertNotIn('SCRIPT',extracted);self.assertNotIn('FOOTER',extracted)
            self.assertIn('Manuscript text.',extracted)

if __name__=='__main__':unittest.main()
