import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createWorker, validateImageManifest, verifyImageBytes} from '../worker/index.js';
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const content = {papers:[{id:'2601.00001'}],manifest:{schema_version:1,images:[]}};
function database() {
  const reading = new Map(), prefs = new Map(), calls=[];
  return {reading,prefs,calls,prepare(sql) {return {bind(...args) {calls.push({sql,args}); return {
    async all() {const user=args[0]; return {success:true,results:[...(sql.includes('FROM reading_state') ? reading : prefs).values()].filter(r=>r.user_id===user)};},
    async run() {
      const [user,id,val,time,source]=args, key=JSON.stringify([user,id]);
      if(sql.startsWith('DELETE')) reading.delete(key);
      else if(sql.includes('INTO reading_state')) reading.set(key,{user_id:user,article_id:id,read_at:val});
      else {const row=prefs.get(key)||{user_id:user,article_id:id,starred:0,bad:0};
        if(sql.includes('starred_updated_at')) Object.assign(row,{starred:val,starred_updated_at:time});
        else Object.assign(row,{bad:val,bad_updated_at:time,bad_reason:null,bad_source:source,bad_source_message_id:null});
        prefs.set(key,row);
      }
      return {success:true};
    }
  };}};}};
}
function environment(overrides={}) {return {OWNER_USER_ID:'owner',TRUSTED_AUTH_HEADER:'hosting',DB:database(),ASSETS:{fetch:async()=>new Response('private asset')},...overrides};}
function request(path, body, extra={}) {
  const headers={'oai-authenticated-user-id':'owner',...(body===undefined ? {} : {'Content-Type':'application/json',Origin:'https://reader.example'}),...extra};
  for (const key of Object.keys(headers)) if(headers[key]===null) delete headers[key];
  return new Request('https://reader.example'+path,{method:body===undefined?'GET':'POST',headers,...(body===undefined ? {} : {body:typeof body==='string'?body:JSON.stringify(body)})});
}
const worker=createWorker(content);
test('owner boundary gates UI, content, API and images; caller identity never supplies configuration',async()=>{
  for(const path of ['/','/papers.json','/reader-articles/2601.00001/v1.json','/api/reading-state','/api/images/'+ 'a'.repeat(64)]) {
    assert.equal((await worker.fetch(request(path,undefined,{'oai-authenticated-user-id':null,'user_id':'owner','Authorization':'Bearer owner'}),environment())).status,401);
    assert.equal((await worker.fetch(request(path,undefined,{'oai-authenticated-user-id':'outsider'}),environment())).status,403);
    assert.equal((await worker.fetch(request(path),environment({TRUSTED_AUTH_HEADER:undefined}))).status,503);
    assert.equal((await worker.fetch(request(path),environment({OWNER_USER_ID:undefined}))).status,503);
  }
});
test('private responses always disable shared caching, including errors',async()=>{
  for(const req of [request('/'),request('/api/reading-state'),request('/api/nope'),request('/',undefined,{'oai-authenticated-user-id':null})]) {
    const response=await worker.fetch(req,environment());
    assert.equal(response.headers.get('Cache-Control'),'private, no-store');
    assert.match(response.headers.get('Vary'),/Cookie.*Authorization.*oai-authenticated-user-id/);
  }
});
test('reading state upsert/delete and per-user isolation',async()=>{
  const env=environment();
  env.DB.reading.set('other',{user_id:'other',article_id:'other-secret'});
  assert.deepEqual(await (await worker.fetch(request('/api/reading-state'),env)).json(),{readIds:[]});
  assert.equal((await worker.fetch(request('/api/reading-state',{articleId:'2601.00001',operation:'read'}),env)).status,200);
  assert.deepEqual(await (await worker.fetch(request('/api/reading-state'),env)).json(),{readIds:['2601.00001']});
  assert.match([...env.DB.reading.values()].find(r=>r.user_id==='owner').read_at,/^\d{4}-\d\d-\d\dT/);
  await worker.fetch(request('/api/reading-state',{articleId:'2601.00001',operation:'unread'}),env);
  assert.deepEqual(await (await worker.fetch(request('/api/reading-state'),env)).json(),{readIds:[]});
});
test('preference updates preserve independent flags and reset bad metadata',async()=>{
  const env=environment();
  for(const operation of ['star','bad','unstar']) assert.equal((await worker.fetch(request('/api/article-preferences',{articleId:'2601.00001',operation}),env)).status,200);
  assert.deepEqual(await (await worker.fetch(request('/api/article-preferences'),env)).json(),{starredIds:[],badIds:['2601.00001']});
  const row=[...env.DB.prefs.values()][0]; row.bad_reason='old'; row.bad_source_message_id='old-message';
  await worker.fetch(request('/api/article-preferences',{articleId:'2601.00001',operation:'unbad'}),env);
  assert.equal(row.bad_reason,null); assert.equal(row.bad_source_message_id,null); assert.equal(row.bad_source,'reader:unbad');
  assert.ok(row.starred_updated_at); assert.ok(row.bad_updated_at); assert.equal(env.DB.prefs.size,1);
  const starSql=env.DB.calls.find(c=>c.sql.includes('INSERT INTO article_preferences')&&c.sql.includes('starred_updated_at')).sql;
  assert.doesNotMatch(starSql,/\bbad\b/);
  const badSql=env.DB.calls.find(c=>c.sql.includes('INSERT INTO article_preferences')&&c.sql.includes('bad_updated_at')).sql;
  assert.doesNotMatch(badSql,/\bstarred\b/);
});
test('writes reject foreign/missing origin, malformed/oversized JSON, unknown IDs/operations and forged user fields',async()=>{
  const good={articleId:'2601.00001',operation:'read'};
  for (const [body,headers,status] of [
    [good,{Origin:'https://evil.example'},403],[good,{Origin:null},403],
    [good,{'Content-Type':'text/plain'},415],['{',{},400],['x'.repeat(4097),{},413],
    [{...good,articleId:'unknown'}, {},400],[{...good,operation:'reset-all'}, {},400],
    [{...good,user_id:'other'}, {},400],[[],{},400],['null',{},400]
  ]) assert.equal((await worker.fetch(request('/api/reading-state',body,headers),environment())).status,status);
});
test('D1 unavailable and read/write errors return 503',async()=>{
  assert.equal((await worker.fetch(request('/api/reading-state'),environment({DB:null}))).status,503);
  const DB={prepare(){throw Error('private SQL details');}};
  for(const req of [request('/api/reading-state'),request('/api/reading-state',{articleId:'2601.00001',operation:'read'})]) {
    const response=await worker.fetch(req,environment({DB})); assert.equal(response.status,503); assert.doesNotMatch(await response.text(),/SQL details/);
  }
});
async function fixture() {
  // Minimal RIFF/VP8L header: sufficient for metadata parser; production encoder
  // and upload readback additionally decode the image in pipeline validation.
  const bytes=new Uint8Array(26); bytes.set(Buffer.from('RIFF')); new DataView(bytes.buffer).setUint32(4,18,true); bytes.set(Buffer.from('WEBPVP8L'),8); new DataView(bytes.buffer).setUint32(16,5,true); bytes[20]=0x2f;
  const sha256=Buffer.from(await crypto.subtle.digest('SHA-256',bytes)).toString('hex');
  const entry={article_id:'2601.00001',version:'v1',source_ref:'figure1.png',object_key:`papers/2601.00001/v1/${sha256}.webp`,serving_path:`/api/images/${sha256}`,mime_type:'image/webp',sha256,width:1,height:1,byte_size:26};
  return {bytes,entry};
}
test('R2 routes resolve only allowlisted manifest paths and verify MIME/hash/dimensions/size',async()=>{
  const {bytes,entry}=await fixture(); const imageWorker=createWorker({...content,manifest:{schema_version:1,images:[entry]}}); let called;
  const env=environment({IMAGES:{async get(key){called=key; return {httpMetadata:{contentType:'image/webp'},arrayBuffer:async()=>bytes.buffer};}}});
  const response=await imageWorker.fetch(request(entry.serving_path),env); assert.equal(response.status,200); assert.equal(called,entry.object_key); assert.equal(response.headers.get('Content-Type'),'image/webp');
  for(const path of ['/api/images/unregistered','/api/images/%2e%2e%2fsecret',entry.serving_path+'?key=secret']) assert.ok([400,404].includes((await imageWorker.fetch(request(path),env)).status));
  await assert.rejects(verifyImageBytes(bytes.buffer,{...entry,width:2},'image/webp'));
  await assert.rejects(verifyImageBytes(bytes.buffer,entry,'image/png'));
  await assert.rejects(verifyImageBytes(bytes.buffer,{...entry,sha256:'a'.repeat(64)},'image/webp'));
  await assert.rejects(verifyImageBytes(bytes.buffer,{...entry,byte_size:27},'image/webp'));
  assert.equal((await imageWorker.fetch(request(entry.serving_path),environment({IMAGES:{get:async()=>null}}))).status,503);
  assert.throws(()=>validateImageManifest({schema_version:1,images:[{...entry,object_key:'../secret'}]},new Set(['2601.00001'])));
});
test('default content loading uses private ASSETS and rejects missing content',async()=>{
  const defaultWorker=createWorker();
  const env=environment({ASSETS:{fetch:async req=>new Response(JSON.stringify(new URL(req.url).pathname==='/papers.json'?content.papers:content.manifest),{headers:{'Content-Type':'application/json'}})}});
  assert.equal((await defaultWorker.fetch(request('/api/reading-state'),env)).status,200);
  assert.equal((await defaultWorker.fetch(request('/api/reading-state'),environment({ASSETS:{fetch:async()=>new Response('missing',{status:404})}}))).status,503);
});
test('legacy article IDs use only explicit registered encoded body routes',async()=>{
  const legacy=createWorker({papers:[{id:'hep-th/9901001',version:'v1',versionHistory:[]}],manifest:{schema_version:1,images:[]}});
  assert.equal((await legacy.fetch(request('/reader-articles/hep-th%2F9901001/v1.json'),environment())).status,200);
  assert.equal((await legacy.fetch(request('/reader-articles/other%2F9901001/v1.json'),environment())).status,400);
});
test('alternate identity adapter is constructor-only and cannot bypass owner authorization',async()=>{
  const otherProvider=createWorker(content,{authenticate:async()=>({userId:'owner'})});
  const noHeader=request('/api/reading-state',undefined,{'oai-authenticated-user-id':null});
  assert.equal((await otherProvider.fetch(noHeader,environment({TRUSTED_AUTH_HEADER:undefined}))).status,200);
  assert.equal((await otherProvider.fetch(noHeader,environment({OWNER_USER_ID:'different'}))).status,403);
  assert.equal((await otherProvider.fetch(noHeader,environment({OWNER_USER_ID:undefined}))).status,503);
  assert.equal((await worker.fetch(noHeader,environment({AUTH_MODE:'permissive',AUTHENTICATE:()=>({userId:'owner'})}))).status,401);
});
test('manifest object keys may contain literal encoded legacy IDs without decoding them',()=>{
 const sha='a'.repeat(64);const manifest={schema_version:1,images:[{article_id:'hep-ph/1234567',version:'v1',source_ref:'figures/a.png',object_key:`papers/hep-ph%2F1234567/v1/${sha}.webp`,serving_path:`/api/images/${sha}`,mime_type:'image/webp',sha256:sha,width:1,height:1,byte_size:10}]};
 assert.equal(validateImageManifest(manifest,new Set(['hep-ph/1234567'])).size,1);
 manifest.images[0].object_key='papers/%2e%2e/private.webp';assert.throws(()=>validateImageManifest(manifest,new Set(['hep-ph/1234567'])));
});

test('registered legacy standalone paths work without exempting traversal or unknown versions',async()=>{
 const id='cond-mat/0301234';
 const legacy=createWorker({papers:[{id,version:'v2',html_path:'papers/cond-mat%2F0301234/v2.html',versionHistory:[{version:'v1',html_path:'papers/cond-mat%2F0301234/v1.html'}]}],manifest:{schema_version:1,images:[]}});
 for(const path of ['/papers/cond-mat%2F0301234/v1.html','/papers/cond-mat%2F0301234/v2.html','/reader-articles/cond-mat%2F0301234/v1.json']){
  assert.equal((await legacy.fetch(request(path),environment())).status,200,path);
  assert.equal((await legacy.fetch(new Request('https://reader.example'+path,{method:'HEAD',headers:{'oai-authenticated-user-id':'owner'}}),environment())).status,200,path);
  assert.equal((await legacy.fetch(request(path,undefined,{'oai-authenticated-user-id':null}),environment())).status,401);
 }
 for(const path of ['/papers/cond-mat%2F0301234/v3.html','/papers/other%2F0301234/v1.html','/papers/cond-mat%252F0301234/v1.html','/papers/cond-mat%5C0301234/v1.html','/papers/cond-mat%2F0301234/extra/v1.html'])
  assert.equal((await legacy.fetch(request(path),environment())).status,400,path);
 const unsafe=createWorker({papers:[{id:'x/../private',version:'v1',html_path:'papers/x%2F%2e%2e%2Fprivate/v1.html'}],manifest:{schema_version:1,images:[]}});
 for(const path of ['/papers/x%2F%2e%2e%2Fprivate/v1.html','/reader-articles/x%2F..%2Fprivate/v1.json'])
  assert.equal((await unsafe.fetch(request(path),environment())).status,503,path); // invalid collection fails closed before routing
});
test('legacy paths map literal percent filenames only inside the ASSETS boundary',async()=>{
 const paths=[];const env=environment({ASSETS:{fetch:async req=>{paths.push(new URL(req.url).pathname);return new Response('actual asset');}}});
 const legacy=createWorker({papers:[{id:'cond-mat/0301234',version:'v1',html_path:'papers/cond-mat%2F0301234/v1.html'}],manifest:{schema_version:1,images:[]}});
 for(const path of ['/papers/cond-mat%2F0301234/v1.html','/reader-articles/cond-mat%2F0301234/v1.json']){
  const req=request(path);const response=await legacy.fetch(req,env);
  assert.equal(response.status,200);assert.equal(await response.text(),'actual asset');
  assert.equal(new URL(req.url).pathname,path);
  assert.equal(paths.at(-1),path.replaceAll('%','%25'));
 }
 const before=paths.length;
 for(const path of ['/papers/cond-mat%252F0301234/v1.html','/reader-articles/cond-mat%252F0301234/v1.json'])assert.equal((await legacy.fetch(request(path),env)).status,400);
 assert.equal(paths.length,before);
});

test('versionless generic slash IDs use current body path and literal ASSETS filename',async()=>{
 const paths=[],worker=createWorker({papers:[{id:'blog/post',title:'Generic',html_path:'papers/blog-post.html'}],manifest:{schema_version:1,images:[]}});
 const env=environment({ASSETS:{fetch:async req=>{paths.push(new URL(req.url).pathname);return new Response('body');}}});
 assert.equal((await worker.fetch(request('/reader-articles/blog%2Fpost/current.json'),env)).status,200);
 assert.deepEqual(paths,['/reader-articles/blog%252Fpost/current.json']);
 for(const path of ['/reader-articles/blog%2Fpost/v1.json','/reader-articles/blog%252Fpost/current.json'])assert.equal((await worker.fetch(request(path),env)).status,400);
});
