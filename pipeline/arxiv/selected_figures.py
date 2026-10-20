"""Resolve model-chosen references against verified originals; extract only selections."""
import math, re
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from pipeline.shared.runtime import digest
from .prepare import fetch


def verified_original(directory, source):
    expected={'html':'source.html','pdf':'source.pdf'}.get(source.get('kind'))
    if not expected or source.get('local_file')!=expected:raise ValueError('Invalid original source path')
    if source.get('url')!=f"https://arxiv.org/{source['kind']}/{source['id']}{source['version']}":raise ValueError('Invalid original source URL')
    path=Path(directory)/expected
    if path.is_symlink():raise ValueError('Original source cannot be a symlink')
    data=path.read_bytes()
    if digest(data)!=source['sha256']:raise ValueError('Original source hash changed')
    return path,data


def asset_url(reference, base, source):
    if not isinstance(reference,str) or any(part in ('.','..') for part in unquote(urlparse(reference).path).split('/')):raise ValueError('Asset reference contains traversal')
    url=urljoin(base,reference);u=urlparse(url)
    prefix=urlparse(source['url']).path.rstrip('/')+'/'
    path=unquote(u.path)
    if (not reference or u.scheme!='https' or u.netloc!='arxiv.org' or u.query or u.fragment
        or not path.startswith(prefix) or any(part in ('.','..') for part in path.split('/'))
        or '\\' in path or '%' in path):raise ValueError('Asset outside exact-version original')
    return url


def resolve_selection(directory,source,figure):
    if not re.fullmatch(r'selected:[A-Za-z0-9_-]{1,80}',figure.get('src','')):raise ValueError('Invalid selected image reference')
    selection=figure.get('selection')
    if not isinstance(selection,dict) or selection.get('kind')!=source['kind']:raise ValueError('Selection/source kind mismatch')
    path,data=verified_original(directory,source)
    if source['kind']=='html':
        if set(selection)!={'kind','element_id','asset_index'}:raise ValueError('Invalid HTML selection fields')
        if not isinstance(selection['element_id'],str) or not selection['element_id']:raise ValueError('Missing HTML element id')
        index=selection['asset_index']
        if type(index) is not int or index<0:raise ValueError('Invalid asset index')
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(data,'html.parser');article=soup.find('article') or soup.find(class_='ltx_document')
        nodes=article.find_all(id=selection['element_id']) if article else []
        if len(nodes)!=1:raise ValueError('HTML element id must resolve uniquely within manuscript')
        node=nodes[0];assets=([node] if node.name in ('img','svg','object') else node.find_all(['img','svg','object']))
        if index>=len(assets):raise ValueError('HTML asset index out of bounds')
        owners=[asset.find_parent('figure') for asset in assets]
        if any(owner is None for owner in owners) or any(owner is not owners[0] for owner in owners):raise ValueError('Selection must identify assets from one manuscript figure')
        asset=assets[index];owner=owners[index];caption=owner.find('figcaption')
        caption_text=caption.get_text(' ',strip=True) if caption else ''
        number=re.match(r'(?:Figure|Fig\.?)[\s\u00a0]*(\d+[A-Za-z]?)\b',caption_text,re.I)
        if number and not re.fullmatch(r'Figure '+re.escape(number.group(1))+r'(?:\s*\([a-z]\))?',figure.get('number','')):raise ValueError('Figure number does not match original caption')
        if asset.name=='svg':return {'inline':str(asset).encode(),'mime':'image/svg+xml'}
        if asset.name=='object' and asset.get('type')!='image/svg+xml':raise ValueError('Unsupported HTML object')
        base=soup.find('base',href=True);base=urljoin(source['url'],base['href']) if base else source['url']
        # A base may only name the exact source directory.
        if base!=source['url'] and base!=source['url'].rstrip('/')+'/':raise ValueError('Invalid same-version HTML base')
        reference=asset.get('data' if asset.name=='object' else 'src','')
        return {'url':asset_url(reference,base,source)}
    if set(selection)!={'kind','page','bbox','units','page_bounds'} or selection['units']!='pt':raise ValueError('PDF selection requires explicit point coordinates')
    import fitz
    with fitz.open(path) as pdf:
        page=selection['page']
        if type(page) is not int or not 1<=page<=len(pdf):raise ValueError('PDF page out of bounds')
        bounds=selection['page_bounds'];bbox=selection['bbox']
        for values in (bounds,bbox):
            if not isinstance(values,list) or len(values)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise ValueError('Invalid finite PDF coordinates')
        actual=list(pdf[page-1].rect)
        if any(abs(a-b)>0.001 for a,b in zip(bounds,actual)):raise ValueError('PDF page bounds mismatch')
        x0,y0,x1,y1=bbox
        if not(actual[0]<=x0<x1<=actual[2] and actual[1]<=y0<y1<=actual[3]):raise ValueError('PDF crop outside visible page bounds')
    return {'path':path}


def validate_selection(directory,source,figure):
    resolved=resolve_selection(directory,source,figure)
    if 'url' in resolved:return digest({'url':resolved['url']})
    if 'inline' in resolved:return digest(resolved['inline'])
    return digest(figure['selection'])


def link_html_figure(directory,source,figure):
    """Keep exact-version arXiv assets remote; never download or upload them."""
    if source.get('kind')!='html':raise ValueError('Direct image links require HTML')
    resolved=resolve_selection(directory,source,figure)
    if 'url' not in resolved:raise ValueError('Selected HTML figure has no direct asset URL')
    return {'source_ref':figure['src'],'url':resolved['url'],'selection':figure['selection'],
            'source_document_sha256':source['sha256']}


def materialize_selection(directory,source,figure,fetcher=fetch):
    resolved=resolve_selection(directory,source,figure);selection=figure['selection']
    root=Path(directory)/'figures';root.mkdir(exist_ok=True)
    if root.is_symlink():raise ValueError('Selected figures directory cannot be a symlink')
    name=digest({'source_sha256':source['sha256'],'selection':selection})
    candidate={'selection':selection,'source':source['url'],'source_document_sha256':source['sha256']}
    if source['kind']=='html':
        if 'inline' in resolved:data,mime=resolved['inline'],resolved['mime']
        else:
            data,mime,final=fetcher(resolved['url'])
            if final!=resolved['url']:raise ValueError('Selected asset redirect changed source reference')
        suffix={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif','image/svg+xml':'.svg'}.get(mime)
        if suffix is None:raise ValueError('Unsupported selected image type')
        output=root/(name+suffix)
        if output.is_symlink():raise ValueError('Selected output cannot be a symlink')
        output.write_bytes(data)
    else:
        import fitz
        with fitz.open(resolved['path']) as pdf:
            page=pdf[selection['page']-1];clip=fitz.Rect(selection['bbox'])
            # Render the visible selected rectangle directly. Serializing a PDF
            # page's clipped SVG would retain off-crop paths and text.
            data=page.get_pixmap(matrix=fitz.Matrix(3,3),clip=clip,alpha=False).tobytes('png')
            candidate['rasterization']={'renderer':'PyMuPDF','dpi':216,'page':selection['page'],'bbox':selection['bbox'],'units':'pt'}
        output=root/(name+'.png')
        if output.is_symlink():raise ValueError('Selected output cannot be a symlink')
        output.write_bytes(data)
    candidate.update(local_file=str(output.relative_to(directory)),source_sha256=digest(data))
    return candidate
