"""Lossless WebP + Sites-managed private R2 upload and full-byte readback verification."""
import base64, io, os, re, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote,urlparse
from .runtime import digest,now

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Sites storage redirects are forbidden')

class SitesStore:
    """Supported Sites service access; token is consumed by hosting dispatch.

    Obtain an existing service token through get_site, never an invented visitor
    header. Only use the exact confirmed owner-private Site's HTTPS origin.
    """
    def __init__(self):
        self.origin=os.environ.get('READER_SITE_URL','').rstrip('/')
        parsed=urlparse(self.origin)
        self.token=os.environ.get('SITES_SERVICE_TOKEN','')
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not self.token:
            raise RuntimeError('Sites HTTPS origin/service access not configured')
        self.opener=urllib.request.build_opener(_NoRedirect())
    def _request(self,key,data=None,mime=None):
        headers={'OAI-Sites-Authorization':'Bearer '+self.token}
        if data is not None:headers.update({'Content-Type':mime,'X-Reader-Upload':'1'})
        request=urllib.request.Request(self.origin+'/api/storage?key='+quote(key,safe=''),data=data,headers=headers,method='PUT' if data is not None else 'GET')
        with self.opener.open(request,timeout=60) as response:
            body=response.read(32*1024*1024+1)
            if len(body)>32*1024*1024:raise ValueError('Sites object exceeds size limit')
            return body,response.headers.get_content_type()
    def put(self,key,data,mime):
        if len(data)>32*1024*1024:raise ValueError('Sites object exceeds size limit')
        body,stored_mime=self._request(key,data,mime)
        if body!=data or stored_mime!=mime:raise ValueError('Sites upload readback mismatch')
    def get(self,key):return self._request(key)

def source_bytes(paper_dir,src):
    p=(Path(paper_dir)/src).resolve();base=(Path(paper_dir)/'figures').resolve()
    if not p.is_relative_to(base):raise ValueError('Figure path escapes figures directory')
    data=p.read_bytes();return data,'image/svg+xml' if p.suffix.lower()=='.svg' else 'application/octet-stream'

def validate_svg(vector):
    # Parse before checking references so XML character entities cannot hide a URL.
    if re.search(br'<!DOCTYPE|<!ENTITY',vector,re.I):raise ValueError('SVG entities not allowed')
    try:root=ET.fromstring(vector)
    except ET.ParseError as error:raise ValueError('Invalid SVG') from error
    if root.tag.split('}')[-1]!='svg':raise ValueError('Invalid SVG root')
    for element in root.iter():
        name=element.tag.split('}')[-1].lower()
        if name in ('script','foreignobject','style','animate','animatetransform','set'):raise ValueError('SVG active content not allowed')
        for key,value in element.attrib.items():
            key=key.split('}')[-1].lower()
            if key.startswith('on') or key=='base':raise ValueError('SVG active attributes not allowed')
            if key in ('href','src') and not value.startswith('#'):
                embedded=re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=\s]+)',value)
                if not embedded:raise ValueError('SVG external references not allowed')
                from PIL import Image
                try:
                    raw=base64.b64decode(re.sub(r'\s','',embedded[2]),validate=True)
                    image=Image.open(io.BytesIO(raw));image.verify()
                    if image.format != {'png':'PNG','jpeg':'JPEG'}[embedded[1]]:raise ValueError('Embedded SVG image MIME mismatch')
                except Exception as error:raise ValueError('Invalid embedded SVG image') from error
            # CSS escapes/comments may disguise url/import; conservative refusal.
            if '\\' in value or '/*' in value or '@' in value:raise ValueError('SVG escaped CSS not allowed')
            for ref in re.findall(r'url\(\s*([^)]*)\)',value,re.I):
                if not ref.strip(' "\'').startswith('#'):raise ValueError('SVG external CSS reference not allowed')


def validate_raster_source(encoded,image):
    """Reject decoder precision/color conversion before defining pixel baseline.

    Pillow's RGB mode does not prove an eight-bit source: RGB16 PNG/TIFF
    decoders can already have discarded low bits. Only audited formats enter
    this pipeline; expanding this list requires encoded sample-depth tests.
    """
    if image.format not in ('PNG','JPEG','GIF','WEBP'):
        raise ValueError('Unsupported raster format for lossless preservation')
    if image.format == 'PNG':
        if len(encoded)<33 or encoded[:8]!=b'\x89PNG\r\n\x1a\n' or encoded[8:16]!=b'\x00\x00\x00\rIHDR':
            raise ValueError('Invalid PNG header')
        depth,color=encoded[24],encoded[25]
        if depth>8:raise ValueError('High-bit-depth source cannot be preserved losslessly as 8-bit WebP')
        allowed={0:(1,2,4,8),2:(8,),3:(1,2,4,8),4:(8,),6:(8,)}
        if depth not in allowed.get(color,()):raise ValueError('Unsupported PNG sample format')
    elif image.format == 'JPEG' and getattr(image,'bits',None)!=8:
        raise ValueError('Unsupported JPEG sample precision')
    if image.mode not in ('1','L','LA','P','RGB','RGBA'):
        raise ValueError('Unsupported raster color model for lossless preservation')
    if getattr(image,'n_frames',1)!=1:
        raise ValueError('Animated source requires explicit handling; refusing to discard frames')


def store_figure(paper,figure,candidate,paper_dir,store,loader=source_bytes):
    from PIL import Image
    # Shared storage accepts source-neutral identities, but never raw path
    # separators/escapes in the version segment or ambiguous encoded IDs.
    article_id=paper.get('id');version=paper.get('version')
    if not isinstance(article_id,str) or not article_id or re.search(r'[\x00-\x1f\x7f\\%]',article_id) or any(part in ('','.','..') for part in article_id.split('/')):
        raise ValueError('Invalid image article identity')
    if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',version):
        raise ValueError('Invalid image version segment')
    try:article_id.encode('utf-8')
    except UnicodeEncodeError as exc:raise ValueError('Invalid image article identity') from exc
    original,mime=loader(paper_dir,candidate.get('local_file',figure['src']));source_sha=digest(original)
    if candidate.get('source_sha256') and candidate['source_sha256']!=source_sha:raise ValueError('Prepared figure source hash mismatch')
    vector=original if mime=='image/svg+xml' or original.lstrip().startswith(b'<svg') else None
    rasterization=None
    if vector is not None:
        validate_svg(vector)
        import cairosvg
        png=cairosvg.svg2png(bytestring=vector,unsafe=False);image=Image.open(io.BytesIO(png));rasterization={'renderer':'CairoSVG','width':image.width,'height':image.height}
        validate_raster_source(png,image)
    else:
        image=Image.open(io.BytesIO(original))
        validate_raster_source(original,image)
    image.load()
    # Indexed PNG/GIF and RGB color-key transparency have no A band. Preserve
    # their decoded alpha before encoding and before the pixel equality check.
    has_alpha='A' in image.getbands() or 'transparency' in image.info
    image=image.convert('RGBA' if has_alpha else 'RGB');width,height=image.size
    buf=io.BytesIO();image.save(buf,format='WEBP',lossless=True,exact=True);webp=buf.getvalue()
    decoded=Image.open(io.BytesIO(webp));decoded.load()
    if decoded.size!=(width,height) or decoded.convert(image.mode).tobytes()!=image.tobytes():raise ValueError('WebP lossless pixel verification failed')
    sha=digest(webp);key=f"papers/{quote(paper['id'],safe='')}/{paper['version']}/{sha}.webp"
    store.put(key,webp,'image/webp');retrieved,stored_mime=store.get(key)
    verified=Image.open(io.BytesIO(retrieved));verified.load()
    if stored_mime!='image/webp' or digest(retrieved)!=sha or len(retrieved)!=len(webp) or verified.size!=(width,height):raise ValueError('R2 image readback integrity failure')
    entry={'article_id':paper['id'],'version':paper['version'],'source_ref':figure['src'],'object_key':key,'serving_path':f'/api/images/{sha}','mime_type':'image/webp','sha256':sha,'width':width,'height':height,'byte_size':len(webp),'source_sha256':source_sha,'original_mime_type':mime,'original_byte_size':len(original),'uploaded_at':now(),'verified_at':now()}
    for field in ('selection','source_document_sha256'):
        if field in candidate:entry[field]=candidate[field]
    if candidate.get('rasterization'):entry['rasterization']=candidate['rasterization']
    if vector is not None:
        original_key=f"papers/{quote(paper['id'],safe='')}/{paper['version']}/{digest(vector)}.svg"
        store.put(original_key,vector,'image/svg+xml');verify,verify_mime=store.get(original_key)
        if verify!=vector or verify_mime!='image/svg+xml':raise ValueError('R2 vector readback failed')
        entry.update(original_object_key=original_key,rasterization=rasterization)
    return entry
