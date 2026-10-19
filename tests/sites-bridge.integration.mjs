// Local integration only: Python -> HTTP fake Sites dispatch -> real Worker ->
// in-memory R2. Does not validate live Sites authentication or provisioning.
import {validateImageManifest} from '../worker/image-contract.js';
import test from 'node:test';import assert from 'node:assert/strict';import {createServer} from 'node:http';import {spawn} from 'node:child_process';import {createWorker} from '../worker/index.js';
test('Python lossless figure storage crosses managed upload bridge and readback',async()=>{
 const objects=new Map(),worker=createWorker({papers:[],manifest:{schema_version:1,images:[]}}),env={TRUSTED_AUTH_HEADER:'hosting',SITES_OWNER_PRIVATE:'confirmed',SITES_STORAGE_ENABLED:'true',IMAGES:{async put(key,bytes,metadata){objects.set(key,{bytes:Uint8Array.from(bytes),...metadata});},async get(key){const v=objects.get(key);return v?{...v,arrayBuffer:async()=>v.bytes.buffer}:null;}}};
 const server=createServer(async(req,res)=>{try{if(req.headers['oai-sites-authorization']!=='Bearer fixture-token'){res.writeHead(401).end();return;}const headers=new Headers(req.headers);headers.delete('oai-sites-authorization');headers.delete('oai-authenticated-user-id');const chunks=[];for await(const c of req)chunks.push(c);const request=new Request('https://fixture.example'+req.url,{method:req.method,headers,...(req.method==='PUT'?{body:Buffer.concat(chunks)}:{})});const response=await worker.fetch(request,env);res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));}catch{res.writeHead(500).end();}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const program=String.raw`
import os,sys,io,tempfile,urllib.request,urllib.error,json
from pathlib import Path
from pipeline.shared.image_store import SitesStore,store_figure
from PIL import Image
store=SitesStore()
class LocalTestDispatch:
 def open(self,request,timeout):
  # Test adapter only: production SitesStore permits HTTPS and rejects redirects.
  request.full_url='http://127.0.0.1:'+os.environ['TEST_PORT']+request.selector
  return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=timeout)
store.opener=LocalTestDispatch()
with tempfile.TemporaryDirectory() as root:
 p=Path(root)/'figures';p.mkdir();Image.new('RGBA',(7,11),(1,2,3,128)).save(p/'chosen.png')
 entries=[]
 for article_id,version in [('fixture','v1'),('example-source:item-42','current'),('example-source:item-42','revision-a'),('資料/研究','current'),('news/item','revision-a'),('a..b','current'),('source~item','revision-a'),('legacy/0301234','v1')]:
  entry=store_figure({'id':article_id,'version':version},{'src':'figures/chosen.png'},{'local_file':'figures/chosen.png'},root,store)
  entries.append(entry)
  assert entry['width']==7 and entry['height']==11
  encoded,mime=store.get(entry['object_key']);assert mime=='image/webp'
 # The shared helper rejects bad versions before any upload; the actual HTTP
 # boundary independently rejects callers bypassing that helper.
 for version in ['..','v1/extra','revision%2Fa','revision%252Fa',r'bad\version','bad'+chr(0)+'version']:
  try:
   store_figure({'id':'fixture','version':version},{'src':'figures/chosen.png'},{'local_file':'figures/chosen.png'},root,store)
   raise AssertionError('unsafe version accepted')
  except ValueError:pass
  key='papers/fixture/'+version+'/'+entry['sha256']+'.webp'
  try:
   store.put(key,encoded,mime);raise AssertionError('unsafe HTTP key accepted')
  except urllib.error.HTTPError as exc:assert exc.code==400
 for article_key in ['%2e%2e','bad%5Cid','bad%252Fid','bad%ZZ']:
  try:
   store.put('papers/'+article_key+'/current/'+entry['sha256']+'.webp',encoded,mime)
   raise AssertionError('unsafe article encoding accepted')
  except urllib.error.HTTPError as exc:assert exc.code==400
 print('MANIFEST '+json.dumps(entries))
 print('lossless upload/readback/hash verified')
`;
 try{const result=await new Promise((resolve,reject)=>{const child=spawn('pipeline/.venv/bin/python',['-c',program],{env:{...process.env,READER_SITE_URL:'https://fixture.example',SITES_SERVICE_TOKEN:'fixture-token',TEST_PORT:String(server.address().port)}});let out='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>out+=c);child.on('error',reject);child.on('exit',code=>resolve({code,out}));});assert.equal(result.code,0,result.out);assert.match(result.out,/hash verified/);const entries=JSON.parse(result.out.split('\n').find(line=>line.startsWith('MANIFEST ')).slice(9));validateImageManifest({schema_version:1,images:entries},new Set(entries.map(entry=>entry.article_id)));assert.equal(entries.length,8);assert.equal(objects.size,8);}finally{await new Promise(r=>server.close(r));}
});
