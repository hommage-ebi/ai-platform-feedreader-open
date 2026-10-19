import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {createHash} from 'node:crypto';
import {initSite} from '../scripts/init-site.mjs';import {createWorker} from '../worker/index.js';
const worker=createWorker({papers:[],manifest:{schema_version:1,images:[]}});
const base={TRUSTED_AUTH_HEADER:'hosting',SITES_OWNER_PRIVATE:'confirmed',SITES_STORAGE_ENABLED:'true'};
function bucket(){const values=new Map();return {values,async put(k,b,opts){values.set(k,{bytes:Uint8Array.from(b),httpMetadata:opts.httpMetadata});},async get(k){const v=values.get(k);return v?{...v,arrayBuffer:async()=>v.bytes.buffer}:null;}};}
// Fake dispatch tests the documented integration contract, not live Sites auth.
async function dispatch(request,env,{user,token}={}) {const h=new Headers(request.headers);h.delete('oai-authenticated-user-id');h.delete('OAI-Sites-Authorization');if(user!=='owner'&&token!=='fixture-service')return new Response(null,{status:401});if(user)h.set('oai-authenticated-user-id',user);return worker.fetch(new Request(request,{headers:h}),env);}
test('clean init generates bindings and refuses retargeting, no credentials',async()=>{const root=await mkdtemp(join(tmpdir(),'site-init-'));try{const result=await initSite({root,projectId:'fixture-site'});assert.deepEqual(JSON.parse(await readFile(join(root,'.openai/hosting.json'),'utf8')),{project_id:'fixture-site',d1:'DB',r2:'IMAGES'});assert.equal(result.runtime_variables.SITES_STORAGE_ENABLED,'false');await initSite({root,projectId:'fixture-site'});await assert.rejects(initSite({root,projectId:'other'}),/retarget/);}finally{await rm(root,{recursive:true,force:true});}});
test('legacy manual setup endpoints are unavailable and never register a caller',async()=>{const env={...base};for(const path of ['/api/setup/identity','/api/setup/activate']){const req=new Request('https://reader.example'+path);assert.equal((await dispatch(req,env)).status,401);assert.equal((await dispatch(req,env,{token:'fixture-service'})).status,404);assert.equal((await dispatch(req,env,{user:'owner'})).status,404);}assert.equal(env.OWNER_USER_ID,undefined);});
test('managed R2 service upload + readback is digest-bound, gated by dispatch and runtime, separate from user records',async()=>{const bytes=Buffer.from('UklGRhwAAABXRUJQVlA4TA8AAAAvAYAAAAcQ/Y/+ByKi/wEA','base64'),sha=createHash('sha256').update(bytes).digest('hex'),key=`papers/fixture/v1/${sha}.webp`,url='https://reader.example/api/storage?key='+encodeURIComponent(key),env={...base,OWNER_USER_ID:'owner',IMAGES:bucket()};const put=()=>new Request(url,{method:'PUT',headers:{'Content-Type':'image/webp','X-Reader-Upload':'1'},body:bytes});assert.equal((await dispatch(put(),env)).status,401);assert.equal((await dispatch(put(),{...env,SITES_STORAGE_ENABLED:'false'},{token:'fixture-service'})).status,503);let response=await dispatch(put(),env,{token:'fixture-service'});assert.equal(response.status,200);assert.deepEqual(Buffer.from(await response.arrayBuffer()),bytes);response=await dispatch(new Request(url),env,{token:'fixture-service'});assert.equal(response.status,200);assert.deepEqual(Buffer.from(await response.arrayBuffer()),bytes);assert.equal((await dispatch(new Request('https://reader.example/api/reading-state'),env,{token:'fixture-service'})).status,401);assert.equal((await dispatch(new Request(url,{method:'PUT',headers:{'Content-Type':'image/webp','X-Reader-Upload':'1'},body:'bad'}),env,{token:'fixture-service'})).status,400);env.IMAGES.values.get(key).bytes=Uint8Array.from([1]);assert.equal((await dispatch(new Request(url),env,{token:'fixture-service'})).status,503);});
test('storage rejects cross-origin requests and plain browser forms; no fake service marker bypass',async()=>{const url='https://reader.example/api/storage?key='+encodeURIComponent(`papers/x/v1/${'a'.repeat(64)}.webp`),env={...base,OWNER_USER_ID:'owner',IMAGES:bucket()};for(const headers of [{'Content-Type':'image/webp'},{'Content-Type':'image/webp','X-Reader-Upload':'1',Origin:'https://evil.example'}])assert.equal((await dispatch(new Request(url,{method:'PUT',headers,body:'x'}),env,{user:'owner'})).status,403);const forged=new Request(url,{headers:{'oai-authenticated-user-id':'owner','OAI-Sites-Authorization':'Bearer invented'}});assert.equal((await dispatch(forged,env)).status,401);});
test('documented owner browser upload bridge allows only same-origin owner writes',async()=>{
 const bytes=Buffer.from('UklGRhwAAABXRUJQVlA4TA8AAAAvAYAAAAcQ/Y/+ByKi/wEA','base64');
 const sha=createHash('sha256').update(bytes).digest('hex');
 const url='https://reader.example/api/storage?key='+encodeURIComponent(`papers/fixture/v1/${sha}.webp`);
 const env={...base,OWNER_USER_ID:'owner',IMAGES:bucket()};
 const request=()=>new Request(url,{method:'PUT',headers:{Origin:'https://reader.example','Content-Type':'image/webp','X-Reader-Upload':'1'},body:bytes});
 const response=await dispatch(request(),env,{user:'owner'});
 assert.equal(response.status,200);assert.deepEqual(Buffer.from(await response.arrayBuffer()),bytes);
 assert.equal(response.headers.get('Cache-Control'),'private, no-store');
 assert.equal(response.headers.get('Access-Control-Allow-Origin'),null);
 assert.equal((await dispatch(request(),{...env,SITES_STORAGE_ENABLED:'false'},{user:'owner'})).status,503);
 assert.equal((await dispatch(request(),{...env,SITES_OWNER_PRIVATE:undefined},{user:'owner'})).status,503);
 const forged=request();forged.headers.set('oai-authenticated-user-id','other');
 assert.equal((await worker.fetch(forged,env)).status,403);
});

test('generic storage identities and versions share safe manifest keys without relaxing path guards',async()=>{
 const {storageRequest}=await import('../worker/storage.js');
 const {validateImageManifest}=await import('../worker/image-contract.js');
 const bytes=Buffer.from('UklGRhwAAABXRUJQVlA4TA8AAAAvAYAAAAcQ/Y/+ByKi/wEA','base64');
 const sha=createHash('sha256').update(bytes).digest('hex'),env={...base,IMAGES:bucket()},entries=[];
 const quoted=id=>encodeURIComponent(id).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase());
 const put=key=>storageRequest(new Request('https://reader.example/api/storage?key='+encodeURIComponent(key),{method:'PUT',headers:{'Content-Type':'image/webp','X-Reader-Upload':'1'},body:bytes}),env);
 for(const id of ['example-source:item-42','資料/研究','a..b','source~item','legacy/0301234',"punctuation!'()*"]){
  for(const version of ['v1','current','revision-a','revision..a']){
   const key=`papers/${quoted(id)}/${version}/${sha}.webp`;
   assert.equal((await put(key)).status,200,key);
   const get=await storageRequest(new Request('https://reader.example/api/storage?key='+encodeURIComponent(key)),env);
   assert.equal(get.status,200);assert.deepEqual(Buffer.from(await get.arrayBuffer()),bytes);
   entries.push({article_id:id,version,source_ref:'figure1',object_key:key,mime_type:'image/webp',sha256:sha,serving_path:'/api/images/'+sha,width:2,height:3,byte_size:bytes.length});
  }
 }
 validateImageManifest({schema_version:1,images:entries},new Set(entries.map(entry=>entry.article_id)));
 for(const version of ['.','..','v1/extra','revision%2Fa','revision%252Fa','bad\\version','bad\u0000version','-revision','a'.repeat(65)])assert.equal((await put(`papers/fixture/${version}/${sha}.webp`)).status,400,version);
 for(const id of ['.','..','x%2F..%2Fy','x%252Fy','x%5Cy','x%00y','x%ZZy','x%2fy','x%2Ey'])assert.equal((await put(`papers/${id}/current/${sha}.webp`)).status,400,id);
 assert.equal(env.IMAGES.values.size,24);
});
