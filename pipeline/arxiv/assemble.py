"""Validate model outputs, verify images in private storage, render safe articles."""
import html,re
from pathlib import Path
from urllib.parse import quote
from pipeline.shared.runtime import digest, lock, project_root, read, record, write
from .common import identity, paper_path
from pipeline.shared.image_store import store_figure,SitesStore
from .selected_figures import validate_selection,materialize_selection,link_html_figure
IMAGE_RE=re.compile(r'^!\[([^\]\n]*)\]\(([^\s)]+)\)\s*$',re.M)

def validate_output(paper_dir, require_provenance=False):
    p=Path(paper_dir);result=read(p/'result.json');meta=read(p/'metadata.json');source=read(p/'source.json')
    if not result or not meta or not source:raise ValueError('Required model/source JSON missing')
    key=identity(source)
    if identity(result)!=key or identity(meta)!=key or meta.get('schema_version')!=1 or source.get('verified') is not True:raise ValueError('Source/result/metadata identity mismatch')
    if result.get('source_url')!=source['source_url'] or result.get('summary_file')!='summary.md':raise ValueError('Wrong original source/summary file')
    for field in ('title_ja',):
        if not isinstance(result.get(field),str) or not result[field].strip():raise ValueError('Missing '+field)
    lead=result.get('lead_ja')
    if not isinstance(lead,list) or len(lead)!=3 or any(not isinstance(x,str) or not x.strip() for x in lead):raise ValueError('Lead must contain three nonempty strings')
    if meta.get('source')!=source['source_url'] or not isinstance(meta.get('field_sources'),dict) or not isinstance(meta.get('notes'),list) or not isinstance(meta.get('missing_fields'),list):raise ValueError('Metadata provenance fields missing or changed')
    if not isinstance(meta.get('title'),str) or not meta['title'].strip():raise ValueError('Original title missing')
    for field in ('authors','affiliations','institution_names'):
        values=meta.get(field)
        if not isinstance(values,list) or any(not isinstance(x,str) or not x.strip() for x in values):raise ValueError('Invalid metadata '+field)
    if len(set(meta['institution_names']))!=len(meta['institution_names']):raise ValueError('Duplicate institution names')
    summary=(p/'summary.md').read_text()
    if not summary.strip() or re.search(r'^#\s',summary,re.M) or re.search(r'<\s*/?\s*[A-Za-z][^>]*>',summary):raise ValueError('Summary empty or contains H1/raw HTML')
    if any(x in summary for x in lead):raise ValueError('Lead repeated in narrative')
    if re.search(r'\]\(\s*(?:javascript:|data:|file:)',summary,re.I):raise ValueError('Unsafe Markdown URL')
    image_refs=[m.group(2) for m in IMAGE_RE.finditer(summary)]
    if summary.count('![')!=len(image_refs):raise ValueError('Images must be standalone Markdown image lines')
    chosen=result.get('figures')
    if not isinstance(chosen,list) or image_refs!=[f.get('src') for f in chosen] or len(set(image_refs))!=len(image_refs):raise ValueError('Image/result one-to-one order mismatch')
    candidates={};selection_hashes=set()
    for f in chosen:
        for keyname in ('number','caption','source'):
            if not isinstance(f.get(keyname),str) or not f[keyname].strip():raise ValueError('Missing figure '+keyname)
        if f['source']!=source['url']:raise ValueError('Figure provenance mismatch')
        selection_hash=validate_selection(p,source,f)
        if selection_hash in selection_hashes:raise ValueError('Duplicate selected asset or crop')
        selection_hashes.add(selection_hash)
        candidates[f['src']]={'selection':f['selection'],'source_sha256':source['sha256']}
    if require_provenance:
        from .make_tasks import summary_fingerprint
        from .policy import summary_template,agent_settings
        template=summary_template(p.parents[3],source);settings=agent_settings(p.parents[3],source,'summary')
        key=digest({'kind':'summary','input':summary_fingerprint(p),'prompt':template,'model':settings['model'],'reasoning_effort':settings['reasoning_effort']})
        receipt=read(p/'model-output-receipt.json',{})
        if receipt.get('cache_key')!=key or receipt.get('outputs')!={name:digest((p/name).read_bytes()) for name in ('metadata.json','result.json','summary.md')}:raise ValueError('Model output lacks current verified input/prompt receipt; run finish after model execution')
    return result,meta,source,summary,candidates

def render_narrative(summary,figures,stored):
    import markdown,bleach
    fragments={};counter=0
    def protect(match):
        nonlocal counter
        token=f'READERTOKEN{counter:08d}END';counter+=1;fragments[token]=html.escape(match.group(0));return token
    # Protect backslashes before Markdown can treat delimiters as escapes.
    text=re.sub(r'\\\[.*?\\\]|\\\(.*?\\\)',protect,summary,flags=re.S)
    matches=list(IMAGE_RE.finditer(text))
    for match,fig,img in reversed(list(zip(matches,figures,stored))):
        token=f'READERFIGURE{counter:08d}END';counter+=1
        dimensions=f' width="{img["width"]}" height="{img["height"]}"' if 'width' in img and 'height' in img else ''
        url=img.get('url') or img['serving_path']
        fragments[token]=f'<figure><img src="{html.escape(url,quote=True)}" alt="{html.escape(match.group(1),quote=True)}"{dimensions} loading="lazy" referrerpolicy="no-referrer"><figcaption>{html.escape(fig["number"])} {html.escape(fig["caption"])}</figcaption></figure>'
        text=text[:match.start()]+token+text[match.end():]
    rendered=markdown.markdown(text,extensions=['sane_lists'])
    rendered=bleach.clean(rendered,tags=['p','h2','h3','h4','h5','h6','ul','ol','li','strong','em','a','blockquote','code','pre','br','hr'],attributes={'a':['href','title']},protocols=['https','http'],strip=True)
    for token,value in fragments.items():
        if value.startswith('<figure>'):rendered=rendered.replace('<p>'+token+'</p>',value)
        rendered=rendered.replace(token,value)
    return rendered

def generation_identity(result,metadata,source,summary,figures,topics):
    # Scientific output reuse and classification presentation are independent.
    # Topic IDs still belong to the assembled record's identity.
    return digest({'image_policy':'arxiv-html-direct-v1','result':result,'metadata':metadata,'source_hash':source['sha256'],
                   'summary':summary,'figures':figures,'topics':topics})

def assemble_one(run,paper,store=None):
    from .policy import run_policy
    run_policy(run)
    p=paper_path(run,paper);result,meta,source,summary,candidates=validate_output(p,require_provenance=True)
    generation_hash=generation_identity(result,meta,source,summary,candidates,paper.get('topics',[]))
    for directory in (Path(run)/'site',project_root(run)/'data/articles'):
        for entry in read(directory/'papers.json',[]):
            # The canonical collection is source-agnostic; only arXiv entries
            # participate in arXiv generation reuse.
            if str(entry.get('source','')).lower()!='arxiv':continue
            versions=entry.get('versionHistory',[entry])
            for old in versions:
                if (old.get('id',entry.get('id')),old.get('version'))!=identity(paper) or old.get('generation_hash')!=generation_hash:continue
                src=(directory/old['html_path']).resolve()
                if not src.is_relative_to(directory.resolve()):raise ValueError('Reused article path escapes collection')
                if not src.is_file() or digest(src.read_bytes())!=old.get('content_sha256'):continue
                target=Path(run)/'site'/old['html_path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(src.read_bytes())
                with lock(Path(run)/'.site.lock'):
                    all_records=read(Path(run)/'site/papers.json',[]);indexed={identity(r):r for r in all_records};indexed[identity(old)]=old;write(Path(run)/'site/papers.json',list(indexed.values()))
                record(run,'assemble','complete',{'reused':True,'content_sha256':old['content_sha256']},'/'.join(identity(paper)))
                return old
    linked=[];stored=[]
    if source['kind']=='html':
        linked=[link_html_figure(p,source,f) for f in result['figures']]
    else:
        if result['figures'] and store is None:store=SitesStore()
        stored=[store_figure(paper,f,materialize_selection(p,source,f),p,store) for f in result['figures']]
    body=render_narrative(summary,result['figures'],linked or stored)
    record_data={**{k:paper.get(k,[]) for k in ['id','version','date']},'title':meta['title'],'source':'arxiv','title_ja':result['title_ja'],'lead_ja':result['lead_ja'],'authors':meta['authors'],'affiliations':meta['affiliations'],'institution_names':meta['institution_names'],'source_url':result['source_url'],'tags':paper.get('topics',[]),'stored_images':stored,'html_path':f"papers/{quote(paper['id'],safe='')}/{paper['version']}.html"}
    record_data['linked_images']=linked
    esc=lambda s:html.escape(str(s),quote=True)
    header=f'<header><h1>{esc(record_data["title"])}</h1><p class="title-ja">{esc(result["title_ja"])}</p><p class="authors">{esc(", ".join(meta["authors"]))}</p><p class="institutions">{esc(", ".join(meta["institution_names"]))}</p><ol class="lead">'+''.join('<li>'+esc(x)+'</li>' for x in result['lead_ja'])+'</ol></header>'
    document='<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+esc(record_data['title'])+'</title><script type="module" src="/standalone.js"></script></head><body><article>'+header+'<div class="narrative">'+body+'</div><a href="'+esc(result['source_url'])+'">原論文</a></article></body></html>'
    target=Path(run)/'site'/record_data['html_path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_text(document)
    record_data['generation_hash']=generation_hash;record_data['content_sha256']=digest(document.encode());write(target.with_suffix('.json'),record_data)
    # Completion index only contains independently verified ready articles.
    with lock(Path(run)/'.site.lock'):
        all_records=read(Path(run)/'site/papers.json',[]);indexed={identity(r):r for r in all_records};indexed[identity(record_data)]=record_data;write(Path(run)/'site/papers.json',list(indexed.values()))
    record(run,'assemble','complete',{'content_sha256':record_data['content_sha256']},'/'.join(identity(paper)))
    return record_data

def assemble(run,store=None):
    from .policy import run_policy
    run_policy(run)
    # Rebuild the staging completion index, never retain a previous successful
    # generation after exclusion or a failed current provenance check.
    completed=[]
    with lock(Path(run)/'.site.lock'):
        write(Path(run)/'site/papers.json',[])
    for p in read(Path(run)/'run/selected.json',[]):
        try:completed.append(assemble_one(run,p,store))
        except Exception as exc:record(run,'assemble','failed',{'error':str(exc)},'/'.join(identity(p)))
    return completed
