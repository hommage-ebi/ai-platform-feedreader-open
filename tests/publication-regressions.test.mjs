import {tmpdir} from 'node:os';
import {publishedCount} from '../scripts/publication-counts.mjs';
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,access} from 'node:fs/promises';import {join} from 'node:path';
import {readCollection} from '../scripts/content-contract.mjs';import {writeBuildSnapshot,prepareRelease,finalizeRelease} from '../scripts/release-snapshot.mjs';import {recordDeployment,deploymentRecordName} from '../scripts/record-deployment.mjs';import {syncReleaseStatus} from '../scripts/sync-release-status.mjs';import {deploymentStatusRequest,publishedBatch} from '../worker/release-status.js';
async function fixture(fn){const cwd=process.cwd(),root=await mkdtemp(join(tmpdir(),'publication-regression-'));process.chdir(root);const put=async(path,data)=>{await mkdir(join(root,path,'..'),{recursive:true});await writeFile(join(root,path),JSON.stringify(data));};try{await put('dist/client/papers.json',[]);await put('dist/client/batch-status.json',{});await put('dist/.openai/hosting.json',{project_id:'test-site'});await fn({root,put});}finally{process.chdir(cwd);await rm(root,{recursive:true,force:true});}}
test('orphan manifest and interrupted collection fail build contract',()=>fixture(async({put})=>{await put('data/articles/papers.json',[]);await put('data/articles/images-manifest.json',{schema_version:1,images:[{article_id:'absent',version:'v1',source_ref:'fig',sha256:'a'.repeat(64),serving_path:'/api/images/'+'a'.repeat(64),object_key:'papers/absent/v1/a.webp',mime_type:'image/webp',width:1,height:1,byte_size:20}]});await assert.rejects(readCollection(),/Invalid image entry/);await put('data/articles/.collection-transaction.json',{});await assert.rejects(readCollection(),/Interrupted collection/);}));
test('empty-run deployment records, publishes actual runtime status, retries and finalizes',()=>fixture(async({root})=>{
 const manifest=await writeBuildSnapshot();assert.deepEqual(manifest.runs,[]);const lease=await prepareRelease();await assert.rejects(finalizeRelease(),/not verified/);
 const update={site_id:'test-site',build_id:lease.build_id,deployment_id:'d1',status:'succeeded',tests_passed:true,build_passed:true,verified_url:'https://reader.example',verified_at:'2026-10-19T00:00:00Z'};await recordDeployment({...update,status:'verification_pending'});await assert.rejects(finalizeRelease(),/not verified/);await assert.rejects(recordDeployment({...update,deployment_id:'different'}),/another deployment/);await recordDeployment(update);await assert.rejects(finalizeRelease(),/not been synchronized/);
 const objects=new Map(),env={TRUSTED_AUTH_HEADER:'hosting',SITES_OWNER_PRIVATE:'confirmed',SITES_STORAGE_ENABLED:'true',OWNER_USER_ID:'owner',ASSETS:{fetch:async request=>{const path=new URL(request.url).pathname;return new Response(await readFile(join(lease.snapshot,'client',path)));}},IMAGES:{get:async key=>objects.has(key)?{text:async()=>objects.get(key)}:null,put:async(key,value)=>objects.set(key,value)}};
 const request=new Request('https://reader.example/batch-status.json');assert.equal((await (await publishedBatch(request,env)).json()).publication_status,'verification_pending');
 const fetcher=async(url,options)=>deploymentStatusRequest(new Request(url,options),env);
 await syncReleaseStatus('https://reader.example',{fetcher,token:'test-token'});await syncReleaseStatus('https://reader.example',{fetcher,token:'test-token'});
 assert.equal((await (await publishedBatch(request,env)).json()).publication_status,'succeeded');assert.deepEqual(JSON.parse(await readFile(join(lease.snapshot,'client/batch-status.json'),'utf8')),{});
 const denied=await deploymentStatusRequest(new Request('https://reader.example/api/deployment-status',{headers:{'oai-authenticated-user-id':'other'}}),env);assert.equal(denied.status,403);
 const receipt=JSON.parse([...objects.values()][0]);receipt.site_id='wrong';assert.equal((await deploymentStatusRequest(new Request('https://reader.example/api/deployment-status',{method:'PUT',headers:{'Content-Type':'application/json','X-Reader-Release':'1'},body:JSON.stringify(receipt)}),env)).status,409);
 const original=[...objects.values()][0];
 const putReceipt=value=>deploymentStatusRequest(new Request('https://reader.example/api/deployment-status',{method:'PUT',headers:{'Content-Type':'application/json','X-Reader-Release':'1'},body:JSON.stringify(value)}),env);
 const changed=JSON.parse(original);changed.latest_batch.completed_count=99;assert.equal((await putReceipt(changed)).status,409);
 const newer=JSON.parse(original);newer.deployment_id='newer';newer.verified_at='2026-10-20T00:00:00Z';newer.latest_batch.updated_at=newer.verified_at;assert.equal((await putReceipt(newer)).status,200);assert.equal((await putReceipt(JSON.parse(original))).status,409);
 objects.set([...objects.keys()][0],original);
 assert.equal((await deploymentStatusRequest(new Request('https://reader.example/api/deployment-status'),{...env,SITES_OWNER_PRIVATE:undefined})).status,503);
 assert.equal((await (await publishedBatch(request,{...env,IMAGES:{get:async()=>{throw new Error('storage outage');}}})).json()).publication_status,'verification_pending');
 assert.equal((await finalizeRelease()).finalized,true);await assert.rejects(access(join(root,'.sites-runtime/publish.lock')));
 await prepareRelease();await recordDeployment({...update,deployment_id:'later-failed',status:'failed'});await finalizeRelease();
 assert.equal(JSON.parse(await readFile(join(root,'data/releases',deploymentRecordName(lease.build_id,'d1')),'utf8')).status,'succeeded');

}));
test('empty-run failed deployment finalizes without publishing success',()=>fixture(async()=>{await writeBuildSnapshot();const lease=await prepareRelease();await recordDeployment({site_id:'test-site',build_id:lease.build_id,deployment_id:'failed1',status:'failed'});assert.equal((await finalizeRelease()).finalized,true);}));

test('publication counts match completed content hashes, not stale candidates',()=>{const hash='a'.repeat(64),entry={article_id:'a',version:'v1',content_sha256:hash};const batch={completed_count:1,completed_articles:[entry]};assert.equal(publishedCount(batch,[entry,{article_id:'older',version:'v1'}]),1);assert.equal(publishedCount(batch,[{...entry,content_sha256:'b'.repeat(64)}]),0);assert.throws(()=>publishedCount({completed_count:0},[entry],[{id:'a',version:'v1'}]),/exceeds/);});

test('prepare rejects altered excluded release identity schema',()=>fixture(async({put})=>{const manifest=await writeBuildSnapshot();await put('dist/client/release-identity.json',{schema_version:2,build_id:manifest.build_id,site_id:manifest.site_id,collection_sha256:manifest.collection_sha256});await assert.rejects(prepareRelease(),/Release identity/);}));

test('published latest batch is served with exact completed count without changing frozen bytes',()=>fixture(async({root,put})=>{
 const entry={article_id:'a',version:'v1',content_sha256:'a'.repeat(64)};
 const batch={target_date:'2026-10-19',run_id:'today',completed_count:1,completed_articles:[entry],published_count:0,publication_status:'not_deployed'};
 await put('dist/client/papers.json',[{id:'a',version:'v1',content_sha256:entry.content_sha256}]);await put('dist/client/batch-status.json',batch);
 await put('data/articles/batch-status.json',batch);await put('data/articles/batch-history/2026-10-19-today.json',batch);await put('data/runs/2026-10-19/today/run/candidates.json',[{id:'a',version:'v1'}]);await put('data/runs/2026-10-19/today/ledger.json',{target_date:'2026-10-19',run_id:'today'});
 await writeBuildSnapshot();const lease=await prepareRelease();
 const update={site_id:'test-site',build_id:lease.build_id,deployment_id:'article-deployment',status:'succeeded',tests_passed:true,build_passed:true,verified_url:'https://reader.example',verified_at:'2026-10-19T00:00:00Z'};
 await recordDeployment(update);await rm('data/runs/2026-10-19/today/run/candidates.json');
 const {recordRelease}=await import('../scripts/release-record.mjs');await recordRelease('data/runs/2026-10-19/today',update);
 let stored;const env={TRUSTED_AUTH_HEADER:'hosting',SITES_OWNER_PRIVATE:'confirmed',SITES_STORAGE_ENABLED:'true',ASSETS:{fetch:async request=>new Response(await readFile(join(lease.snapshot,'client',new URL(request.url).pathname)))},IMAGES:{get:async()=>stored?{text:async()=>stored}:null,put:async(key,value)=>{stored=value;}}};
 await syncReleaseStatus('https://reader.example',{token:'fixture',fetcher:(url,options)=>deploymentStatusRequest(new Request(url,options),env)});
 const live=await (await publishedBatch(new Request('https://reader.example/batch-status.json'),env)).json();assert.equal(live.published_count,1);assert.equal(live.publication_status,'succeeded');
 assert.equal(JSON.parse(await readFile(join(lease.snapshot,'client/batch-status.json'),'utf8')).published_count,0);assert.equal((await finalizeRelease()).finalized,true);
}));

test('build never accepts manifest field mutations that make the Worker reject all reader routes',()=>fixture(async({put})=>{
 const {createWorker}=await import('../worker/index.js');
 const base={article_id:'generic',version:'v1',source_ref:'figure1',object_key:'papers/generic/v1/image.webp',mime_type:'image/webp',sha256:'a'.repeat(64),serving_path:'/api/images/'+'a'.repeat(64),width:1,height:1,byte_size:20};
 const cases=[['valid',()=>{}],['orphan',x=>x.article_id='absent'],['empty version',x=>x.version=''],['numeric version',x=>x.version=1],['null version',x=>x.version=null],['other version',x=>x.version='v2'],['missing reference',x=>delete x.source_ref],['empty reference',x=>x.source_ref=''],['object reference',x=>x.source_ref={}],['absolute key',x=>x.object_key='/abs'],['traversal key',x=>x.object_key='a/../b'],['encoded traversal',x=>x.object_key='a/%2e%2e/b'],['backslash key',x=>x.object_key='a\\b'],['space key',x=>x.object_key='a b'],['unicode key',x=>x.object_key='図.webp'],['double slash',x=>x.object_key='a//b'],['wrong MIME',x=>x.mime_type='image/png'],['invalid hash',x=>x.sha256='A'.repeat(64)],['oversize image',x=>x.byte_size=32*1024*1024+1],['fractional width',x=>x.width=1.5],['zero height',x=>x.height=0],['wrong serving path',x=>x.serving_path='/api/images/b'],['encoded legacy key',x=>x.object_key='papers/hep-th%2F123/v1/image.webp']];
 for(const [name,mutate]of cases){const image={...base};mutate(image);const papers=[{id:'generic',source:'Other',title:'Generic',version:'v1',html_path:'papers/generic/v1.html',stored_images:[image]}],manifest={schema_version:1,images:[image]};await put('data/articles/papers.json',papers);await put('data/articles/images-manifest.json',manifest);let accepted=true;try{await readCollection();}catch{accepted=false;}
 const response=await createWorker({papers,manifest},{authenticate:async()=>({userId:'owner'})}).fetch(new Request('https://reader.example/'),{OWNER_USER_ID:'owner',ASSETS:{fetch:async()=>new Response('ok')}});
 if(accepted)assert.equal(response.status,200,name);
 if(!['valid','encoded legacy key'].includes(name))assert.equal(accepted,false,name);
 }
 const papers=[{id:'generic',title:'Generic without arXiv metadata',html_path:'papers/generic.html'}],manifest={schema_version:1,images:[]};await put('data/articles/papers.json',papers);await put('data/articles/images-manifest.json',manifest);await readCollection();assert.equal((await createWorker({papers,manifest},{authenticate:async()=>({userId:'owner'})}).fetch(new Request('https://reader.example/'),{OWNER_USER_ID:'owner',ASSETS:{fetch:async()=>new Response('ok')}})).status,200);
}));
