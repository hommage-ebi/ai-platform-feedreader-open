import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {createHash} from 'node:crypto';
import worker from '../dist/worker/index.js';
import sitesWorker from '../dist/server/index.js';
const bytes=Buffer.from('UklGRhwAAABXRUJQVlA4TA8AAAAvAYAAAAcQ/Y/+ByKi/wEA','base64');
const sha=createHash('sha256').update(bytes).digest('hex');
const image={article_id:'fixture',version:'v1',source_ref:'figures/test.png',object_key:`papers/fixture/v1/${sha}.webp`,serving_path:`/api/images/${sha}`,sha256:sha,width:2,height:3,byte_size:bytes.length,mime_type:'image/webp'};
const env={OWNER_USER_ID:'owner',TRUSTED_AUTH_HEADER:'hosting',ASSETS:{async fetch(req){const path=new URL(req.url).pathname;if(path==='/papers.json')return Response.json([{id:'fixture',version:'v1',stored_images:[image]}]);if(path==='/images-manifest.json')return Response.json({schema_version:1,images:[image]});return new Response(await readFile('dist/client/reader-shell.html'),{headers:{'Content-Type':'text/html'}});}},IMAGES:{async get(key){assert.equal(key,image.object_key);return {arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),httpMetadata:{contentType:'image/webp'}}}}};
const request=(path,user)=>new Request('https://reader.example'+path,{headers:user?{'oai-authenticated-user-id':user}:{}});
test('built Worker + manifest serves actual lossless WebP bytes only across configured owner boundary',async()=>{assert.equal((await worker.fetch(request(image.serving_path),env)).status,401);assert.equal((await worker.fetch(request(image.serving_path,'other'),env)).status,403);const response=await worker.fetch(request(image.serving_path,'owner'),env);assert.equal(response.status,200);assert.equal(response.headers.get('Cache-Control'),'private, no-store');assert.deepEqual(Buffer.from(await response.arrayBuffer()),bytes);assert.equal((await worker.fetch(request('/api/images/'+ '0'.repeat(64),'owner'),env)).status,404);assert.equal((await worker.fetch(request(image.serving_path+'?key=private','owner'),env)).status,404);});
test('actual generated hosting config protects all assets and keeps buckets private by binding',async()=>{const config=JSON.parse(await readFile('dist/worker/wrangler.json','utf8'));assert.equal(config.assets.run_worker_first,true);assert.equal(config.assets.html_handling,'none');assert.equal(config.assets.binding,'ASSETS');assert.equal(config.r2_buckets[0].binding,'IMAGES');assert.equal(config.vars.OWNER_USER_ID,undefined);assert.equal((await worker.fetch(request('/'),env)).status,401);assert.equal((await worker.fetch(request('/','owner'),env)).status,200);});

test('Sites server entrypoint is the same protected Worker with the same asset configuration',async()=>{
 assert.equal(await readFile('dist/server/index.js','utf8'),await readFile('dist/worker/index.js','utf8'));
 assert.deepEqual(JSON.parse(await readFile('dist/server/wrangler.json','utf8')),JSON.parse(await readFile('dist/worker/wrangler.json','utf8')));
 assert.equal((await sitesWorker.fetch(request('/'),env)).status,401);
 assert.equal((await sitesWorker.fetch(request('/','owner'),env)).status,200);
});

// This smoke test uses the actual produced JSON rather than substituting fixtures.
test('built Worker serves the real built collection and registered article bodies',async()=>{
 const actualEnv={OWNER_USER_ID:'owner',TRUSTED_AUTH_HEADER:'hosting',ASSETS:{async fetch(request){const path=decodeURIComponent(new URL(request.url).pathname);try{return new Response(await readFile('dist/client'+(path==='/'?'/reader-shell.html':path)));}catch{return new Response('Not found',{status:404});}}}};
 const paths=['/','/papers.json','/images-manifest.json','/batch-status.json','/api/batch-status'];
 const papers=JSON.parse(await readFile('dist/client/papers.json','utf8'));
 for(const paper of papers)for(const version of paper.versionHistory?.length?paper.versionHistory:[paper])paths.push('/'+version.html_path,`/reader-articles/${encodeURIComponent(paper.id)}/${encodeURIComponent(version.version)}.json`);
 for(const path of paths)assert.equal((await worker.fetch(request(path,'owner'),actualEnv)).status,200,path);
});

// Sites serves matching static paths before invoking the Worker.
test('owner-private Sites login opens Reader without setup screens or registration queries',async()=>{
 await assert.rejects(readFile('dist/client/index.html'));
 assert.match(await readFile('dist/client/reader-shell.html','utf8'),/ai-platform-feedreader/);
 const sitesEnv={...env,OWNER_USER_ID:undefined,SITES_OWNER_PRIVATE:'confirmed',DB:{prepare:()=>{throw Error('UI must not query owner registration');}}};
 const page=await sitesWorker.fetch(request('/','site-owner'),sitesEnv);
 assert.equal(page.status,200);assert.match(await page.text(),/YOUR READING DESK/);
 assert.doesNotMatch(await readFile('dist/server/index.js','utf8'),/利用を開始する|site_owner_setup/);
 assert.equal((await sitesWorker.fetch(request('/'),sitesEnv)).status,401);
 assert.equal((await sitesWorker.fetch(request('/api/setup/activate','site-owner'),sitesEnv)).status,404);
});
