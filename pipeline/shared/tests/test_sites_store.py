import io, os, sys, unittest
from pathlib import Path
from unittest.mock import patch
from pipeline.shared.image_store import SitesStore, source_bytes, validate_svg, _NoRedirect
class SitesStoreTest(unittest.TestCase):
    def test_official_transport_and_readback(self):
        class Response(io.BytesIO):
            class Headers:
                def get_content_type(self):return 'image/webp'
            headers=Headers()
        class Opener:
            calls=[]
            def open(self,request,timeout):
                self.calls.append(request)
                return Response(b'fixture')
        with patch.dict(os.environ,{'READER_SITE_URL':'https://fixture.example','SITES_SERVICE_TOKEN':'test-only-token'}):
            store=SitesStore();self.assertIsInstance(store,SitesStore);store.opener=Opener()
            store.put('papers/x/v1/key.webp',b'fixture','image/webp');self.assertEqual(store.get('papers/x/v1/key.webp'),(b'fixture','image/webp'))
            req=store.opener.calls[0];self.assertEqual(req.get_header('Oai-sites-authorization'),'Bearer test-only-token');self.assertIsNone(req.get_header('Oai-authenticated-user-id'));self.assertEqual(req.method,'PUT');self.assertEqual(req.get_header('X-reader-upload'),'1')
    def test_redirect_and_unconfigured_rejected(self):
        with self.assertRaises(ValueError):_NoRedirect().redirect_request(None,None,302,'',{},'https://evil.example')
        for origin in ['http://fixture.example','https://fixture.example/path','https://user:pass@fixture.example']:
            with patch.dict(os.environ,{'READER_SITE_URL':origin,'SITES_SERVICE_TOKEN':'fixture'}),self.assertRaises(RuntimeError):SitesStore()
    def test_missing_sites_configuration_fails_closed(self):
        for config in [{}, {'READER_SITE_URL':'https://fixture.example'}, {'SITES_SERVICE_TOKEN':'fixture'}]:
            with patch.dict(os.environ,config,clear=True),self.assertRaisesRegex(RuntimeError,'Sites HTTPS origin/service access not configured'):
                SitesStore()
    def test_storage_backend_has_no_direct_client(self):
        from pipeline.shared import image_store
        self.assertFalse(hasattr(image_store,'R2Store'))
        self.assertFalse(hasattr(image_store,'boto3'))
    def test_source_files_only_and_svg_canonical_attributes(self):
        with self.assertRaises(ValueError):source_bytes('/tmp','https://arxiv.org/html/a/fig.png')
        for svg in [b'<svg><image href="&#104;ttps://evil.example/a"/></svg>',b'<svg><style>body{}</style></svg>',b'<svg><image href="data:image/svg+xml;base64,AA=="/></svg>',b'<svg><image href="file:///tmp/a"/></svg>',b'<!DOCTYPE svg><svg/>']:
            with self.assertRaises(ValueError):validate_svg(svg)
        validate_svg(b'<svg><use href="#local"/></svg>')
        from PIL import Image
        import base64
        out=io.BytesIO();Image.new('RGB',(1,1)).save(out,format='PNG')
        validate_svg(b'<svg><image href="data:image/png;base64,'+base64.b64encode(out.getvalue())+b'"/></svg>')
if __name__=='__main__':unittest.main()
