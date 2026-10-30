"""Fetch and verify a same-version arXiv manuscript and its header metadata."""
import shutil, urllib.request
from pathlib import Path
from urllib.parse import urlparse
from pipeline.shared.runtime import digest, now, read, record, write
from .common import identity, paper_path
from .header_metadata import metadata

def fetch(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.netloc not in ('arxiv.org','export.arxiv.org') or parsed.query or parsed.fragment:raise ValueError('Source must be official HTTPS arXiv')
    allowed={url}
    if parsed.path.startswith('/pdf/') and not parsed.path.endswith('.pdf'):allowed.add(url+'.pdf')
    class SourceRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            if newurl not in allowed:raise ValueError('Unexpected source redirect')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    opener=urllib.request.build_opener(SourceRedirect())
    with opener.open(urllib.request.Request(url,headers={'User-Agent':'ai-platform-feedreader/1.0'}),timeout=90) as response:
        if response.url not in allowed:raise ValueError('Unexpected source redirect')
        return response.read(),response.headers.get_content_type(),response.url

class PaperHTML:
    """Extract manuscript text only; surrounding navigation is not source evidence."""
    def __init__(self,url):
        self.url=url;self.text=[];self.is_article=False;self.missing_figures=False
    def feed(self,text):
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(text,'html.parser')
        article=soup.find('article') or soup.find(class_='ltx_document')
        self.is_article=article is not None
        if article is None:return
        self.missing_figures=any(not image.get('src') or 'ltx_missing_image' in image.get('class',[]) for image in article.select('figure img'))
        for node in article.find_all(['script','style','nav']):node.decompose()
        self.text=[article.get_text('\n',strip=True)]

def prepare_one(run,p,fetcher=fetch,*,switch_to_pdf=False):
    identity(p);dest=paper_path(run,p);dest.mkdir(parents=True,exist_ok=True)
    old=read(dest/'source.json');stem=p['id']+p['version']; html_url=f'https://arxiv.org/html/{stem}';pdf_url=f'https://arxiv.org/pdf/{stem}'
    html_error=None
    try:
        if switch_to_pdf:raise ValueError('Explicitly selected same-version PDF')
        body,mime,final=fetcher(html_url)
        if final != html_url or mime not in ('text/html','application/xhtml+xml'):raise ValueError('HTML source version/type mismatch')
        text=body.decode('utf-8');parser=PaperHTML(html_url);parser.feed(text)
        if not parser.is_article or len(''.join(parser.text).strip())<500:raise ValueError('Incomplete/abstract-only HTML')
        if parser.missing_figures:raise ValueError('HTML manuscript contains missing figure images')
        kind='html';url=html_url;local='source.html'
    except Exception as exc:
        html_error=str(exc);body,mime,final=fetcher(pdf_url)
        if final not in (pdf_url,pdf_url+'.pdf') or mime!='application/pdf' or not body.startswith(b'%PDF-'):raise ValueError('PDF source version/type mismatch')
        kind='pdf';url=pdf_url;local='source.pdf';text=None
    sha=digest(body)
    if old and old.get('kind')==kind and old.get('sha256')!=sha:raise ValueError('Same-version source hash changed; review required')
    if old and old.get('kind')!=kind:
        if not switch_to_pdf or old.get('kind')!='html' or kind!='pdf':raise ValueError('Previously verified source kind changed; explicit review required')
        history=read(dest/'source-history.json',[])
        if not isinstance(history,list):raise ValueError('Invalid source history')
        if old not in history:history.append(old);write(dest/'source-history.json',history)
    (dest/local).write_bytes(body)
    if kind=='html':(dest/'fulltext.html.txt').write_text(''.join(parser.text))
    source={'schema_version':1,'id':p['id'],'version':p['version'],'source_url':p['source_url'],'url':url,'kind':kind,'sha256':sha,'byte_size':len(body),'verified':True,'verified_at':now(),'local_file':local,'html_failure':html_error}
    if kind=='html':source['fulltext_sha256']=digest((dest/'fulltext.html.txt').read_bytes())
    write(dest/'source.json',source)
    if not (dest/'metadata.json').exists():write(dest/'metadata.json',metadata(p,text))
    return source

def prepare_pdf(run,p,fetcher=fetch):
    """Explicit recovery for an unpublished HTML source with missing figures."""
    from .common import protected_versions
    from .selected_figures import verified_original
    from .make_tasks import active_claims,state_dir,relative_run
    from pipeline.shared.runtime import lock,project_root
    run=Path(run);dest=paper_path(run,p);old=read(dest/'source.json',{})
    if old.get('kind')!='html' or old.get('verified') is not True or identity(old)!=identity(p):raise ValueError('A verified HTML source for this paper is required')
    if identity(p) in protected_versions(run):raise ValueError('Published or pending versions cannot switch source')
    if (dest/'model-output-receipt.json').exists():raise ValueError('Verified model output exists; review before changing source')
    if any((dest/name).exists() for name in ('result.json','summary.md')):raise ValueError('Unverified draft output exists; review and archive it before changing source')
    _,body=verified_original(dest,old);parser=PaperHTML(old['url']);parser.feed(body.decode('utf-8'))
    if not parser.missing_figures:raise ValueError('No missing HTML figure images detected')
    root=project_root(run)
    with lock(state_dir(root)/'registry.lock'):
        if any(claim.get('run_dir')==relative_run(root,str(run)) for claim in active_claims(root,state_dir(root)).values()):raise ValueError('Model task is running in this run; wait before changing source')
        result=prepare_one(run,p,fetcher,switch_to_pdf=True)
    record(run,'prepare','complete',{'sha256':result['sha256'],'previous_sha256':old['sha256'],'source_switch':'html-to-pdf'},'/'.join(identity(p)))
    return result

def prepare(run,fetcher=fetch):
    results=[]
    for p in read(Path(run)/'run/selected.json',[]):
        try:
            source=prepare_one(run,p,fetcher);record(run,'prepare','complete',{'sha256':source['sha256']},'/'.join(identity(p)));results.append(p)
        except Exception as exc:record(run,'prepare','failed',{'error':str(exc)},'/'.join(identity(p)))
    return results

def cleanup(paper_dir):
    p=Path(paper_dir)
    for name in ('source.html','source.pdf','fulltext.html.txt'):(p/name).unlink(missing_ok=True)
    shutil.rmtree(p/'figures',ignore_errors=True)
