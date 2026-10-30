"""HTTPS Site status transport; request and credential are read only from stdin."""
import json, sys, urllib.request
from urllib.parse import urlparse

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('Redirects are not permitted')

def main():
    request=json.load(sys.stdin)
    url=urlparse(request['url']);method=request['method']
    if url.scheme!='https' or not url.netloc or url.username or url.password or url.path!='/api/deployment-status' or url.query or url.fragment or method not in ('GET','PUT'):
        raise ValueError('Invalid Site status request')
    body=request.get('body');data=None if body is None else body.encode('utf-8')
    req=urllib.request.Request(request['url'],data=data,headers=request['headers'],method=method)
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=60) as response:
        raw=response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('Site status response exceeds limit')
        print(json.dumps({'status':response.status,'body':raw.decode('utf-8')}))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Do not print request headers, response bodies, or credential values.
        print(json.dumps({'error':type(exc).__name__}));sys.exit(1)
