// Builds isolated real artifacts and exercises Wrangler's local ASSETS + R2.
// No live Site, credentials, deployment, or external paper downloads are used.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,cp,symlink,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {resolve,join,basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
const source=resolve(fileURLToPath(new URL('..',import.meta.url)));
function command(program,args,options){return new Promise((accept,reject)=>{const child=spawn(program,args,options);let output='';child.stdout.on('data',d=>output+=d);child.stderr.on('data',d=>output+=d);child.on('error',reject);child.on('exit',code=>code===0?accept(output):reject(Error(output)));});}
test('actual built ASSETS preserves legacy paths; private R2 status updates frozen batch', {timeout:90000},async()=>{
 const root=await mkdtemp('/tmp/reader-asset-runtime-');let server,logs='';
 try{
  await cp(source,root,{recursive:true,filter:path=>!['node_modules','dist','.sites-runtime','.wrangler','.venv','__pycache__'].includes(basename(path))});
  await symlink(join(source,'node_modules'),join(root,'node_modules'),'dir');
  const articles=join(root,'data/articles'),htmlPath='papers/hep-th%2F9901001/v1.html';
  const html='<!doctype html><html><head></head><body><section class="narrative"><p>LEGACY ACTUAL ARTIFACT</p></section></body></html>';
  const version={version:'v1',html_path:htmlPath,lead_ja:['a','b','c'],authors:[],affiliations:[],institution_names:[],stored_images:[],content_sha256:createHash('sha256').update(html).digest('hex')};
  const generic={id:'blog/post',title:'Generic versionless fixture',source:'Blog',html_path:'papers/blog-post.html'};
  const unicode={id:'資料/記事..v1',title:'Unicode generic fixture',source:'News',html_path:'papers/資料..記事.html'};
  const paper={id:'hep-th/9901001',title:'Runtime fixture',source:'arXiv',...version,versionHistory:[version]};
  await rm(articles,{recursive:true,force:true});await mkdir(join(articles,'papers/hep-th%2F9901001'),{recursive:true});
  await writeFile(join(articles,unicode.html_path),html);await writeFile(join(articles,generic.html_path),html);await writeFile(join(articles,htmlPath),html);await writeFile(join(articles,'papers.json'),JSON.stringify([paper,generic,unicode]));
  await writeFile(join(articles,'images-manifest.json'),'{"schema_version":1,"images":[]}');const batch={completed_count:1,completed_articles:[{article_id:paper.id,version:version.version,content_sha256:version.content_sha256}],published_count:0,publication_status:'not_deployed'};await writeFile(join(articles,'batch-status.json'),JSON.stringify(batch));
  await writeFile(join(root,'.openai/hosting.json'),'{"project_id":"runtime-fixture","d1":"DB","r2":"IMAGES"}');
  const env={...process.env,CLOUDFLARE_CF_FETCH_ENABLED:'false',WRANGLER_SEND_METRICS:'false',XDG_CONFIG_HOME:join(root,'.sites-runtime/config'),WRANGLER_LOG_PATH:join(root,'.sites-runtime/logs')};
  await command(process.execPath,['scripts/build.mjs'],{cwd:root,env});
  server=spawn(process.execPath,[join(source,'node_modules/wrangler/bin/wrangler.js'),'dev','-c','dist/worker/wrangler.json','--local','--ip','127.0.0.1','--port','0','--inspector-port','0','--var','OWNER_USER_ID:owner','--var','TRUSTED_AUTH_HEADER:hosting','--var','SITES_OWNER_PRIVATE:confirmed','--var','SITES_STORAGE_ENABLED:true'],{cwd:root,env,detached:true});
  const base=await new Promise((accept,reject)=>{const timer=setTimeout(()=>reject(Error('Runtime startup timeout: '+logs)),30000);const data=d=>{logs+=d;const found=logs.match(/Ready on (http:\/\/127\.0\.0\.1:\d+)/);if(found){clearTimeout(timer);accept(found[1]);}};server.stdout.on('data',data);server.stderr.on('data',data);server.on('error',reject);server.on('exit',code=>{clearTimeout(timer);reject(Error('Runtime exited '+code+': '+logs));});});
  const owner={'oai-authenticated-user-id':'owner'};
  for(const path of ['/'+htmlPath,'/reader-articles/hep-th%2F9901001/v1.json','/reader-articles/blog%2Fpost/current.json','/'+generic.html_path,`/reader-articles/${encodeURIComponent(unicode.id)}/current.json`,'/'+unicode.html_path]){
   const response=await fetch(base+path,{headers:owner,redirect:'manual'});assert.equal(response.status,200,path);assert.match(await response.text(),/LEGACY ACTUAL ARTIFACT/);assert.equal(response.headers.get('Cache-Control'),'private, no-store');
   const head=await fetch(base+path,{method:'HEAD',headers:owner,redirect:'manual'});assert.equal(head.status,200);assert.equal(await head.text(),'');
   assert.equal((await fetch(base+path,{redirect:'manual'})).status,401);
   assert.equal((await fetch(base+path,{headers:{'oai-authenticated-user-id':'other'},redirect:'manual'})).status,403);
  }
  for(const path of ['/papers/hep-th%252F9901001/v1.html','/papers/unknown%2F9901001/v1.html','/papers/hep-th%2F9901001/v2.html','/reader-articles/hep-th%252F9901001/v1.json','/reader-articles/blog%2Fpost/v1.json','/reader-articles/blog%252Fpost/current.json'])assert.equal((await fetch(base+path,{headers:owner,redirect:'manual'})).status,400,path);
  const status=async()=>{const response=await fetch(base+'/batch-status.json',{headers:owner});assert.equal(response.status,200);return response.json();};
  assert.equal((await status()).publication_status,'verification_pending');
  const manifest=JSON.parse(await readFile(join(root,'dist/.openai/release-manifest.json')));
  const receipt={schema_version:1,status:'succeeded',site_id:manifest.site_id,build_id:manifest.build_id,collection_sha256:manifest.collection_sha256,deployment_id:'local-runtime',verified_url:'https://reader.example',verified_at:'2026-10-19T00:00:00Z',articles:manifest.articles,latest_batch:{...batch,published_count:1,publication_status:'succeeded',updated_at:'2026-10-19T00:00:00Z'}};
  const put=(body,headers={})=>fetch(base+'/api/deployment-status',{method:'PUT',headers:{'Content-Type':'application/json','X-Reader-Release':'1',...headers},body:JSON.stringify(body)});
  assert.equal((await put(receipt,{'oai-authenticated-user-id':'other'})).status,403);
  assert.equal((await put(receipt,{Origin:'https://attacker.invalid'})).status,403);
  assert.equal((await put({...receipt,site_id:'wrong'})).status,409);
  assert.equal((await put({...receipt,build_id:'0'.repeat(64)})).status,409);
  assert.equal((await put({...receipt,latest_batch:{...receipt.latest_batch,published_count:0}})).status,409);
  assert.equal((await put({...receipt,latest_batch:{...receipt.latest_batch,published_count:2}})).status,409);
  for(let i=0;i<2;i++){const response=await put(receipt);assert.equal(response.status,200);assert.deepEqual(await response.json(),receipt);}
  assert.equal((await status()).publication_status,'succeeded');assert.equal((await status()).published_count,1);
  assert.deepEqual(JSON.parse(await readFile(join(root,'dist/client/batch-status.json'))),batch);
  assert.equal((await fetch(base+'/batch-status.json')).status,401);
 }finally{
  if(server){try{process.kill(-server.pid,'SIGTERM');}catch{}await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));}
  await rm(root,{recursive:true,force:true});
 }
});
