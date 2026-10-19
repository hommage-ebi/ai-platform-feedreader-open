import test from 'node:test';
import assert from 'node:assert/strict';
import {deploymentStatusRequest,publishedBatch} from '../worker/release-status.js';
const sha='a'.repeat(64),entry={article_id:'2610.12345',version:'v1',content_sha256:sha};
function fixture(batch){
 const identity={schema_version:1,build_id:sha,collection_sha256:sha,site_id:'site'};
 let stored;
 const assets={'/release-identity.json':identity,'/papers.json':[{id:entry.article_id,version:entry.version,content_sha256:sha}],'/batch-status.json':batch};
 const env={TRUSTED_AUTH_HEADER:'hosting',SITES_OWNER_PRIVATE:'confirmed',SITES_STORAGE_ENABLED:'true',ASSETS:{fetch:async r=>new Response(JSON.stringify(assets[new URL(r.url).pathname]))},IMAGES:{get:async()=>stored?{text:async()=>stored}:null,put:async(k,v)=>{stored=v;}}};
 const receipt=count=>({schema_version:1,...identity,status:'succeeded',deployment_id:'d1',verified_url:'https://reader.example',verified_at:'2026-10-19T00:00:00Z',articles:[entry],latest_batch:{...batch,published_count:count,publication_status:'succeeded',updated_at:'2026-10-19T00:00:00Z'}});
 return {receipt,put:count=>deploymentStatusRequest(new Request('https://reader.example/api/deployment-status',{method:'PUT',headers:{'Content-Type':'application/json','X-Reader-Release':'1'},body:JSON.stringify(receipt(count))}),env),read:async()=> (await publishedBatch(new Request('https://reader.example/batch-status.json'),env)).json(),corrupt:count=>{stored=JSON.stringify(receipt(count));}};
}
test('publication receipt requires exact completed/deployed hash intersection',async()=>{
 for(const [name,completed,count,expected] of [
  ['exact',[entry],1,200],['undercount',[entry],0,409],['overcount',[entry],2,409],
  ['stale hash',[{...entry,content_sha256:'b'.repeat(64)}],1,409],
  ['stale hash honestly zero',[{...entry,content_sha256:'b'.repeat(64)}],0,200],
  ['different version',[{...entry,version:'v2'}],1,409],
  ['different article',[{...entry,article_id:'other'}],1,409],
 ]){const f=fixture({completed_count:completed.length,completed_articles:completed});assert.equal((await f.put(count)).status,expected,name);}
});
test('malformed completed identities and incoherent totals fail closed',async()=>{
 for(const batch of [
  {completed_count:2,completed_articles:[entry]},
  {completed_count:2,completed_articles:[entry,entry]},
  {completed_count:1,completed_articles:[{...entry,content_sha256:null}]},
  {completed_count:1,completed_articles:[null]},
  {completed_count:'1',completed_articles:[entry]},
  {completed_count:0,completed_articles:null},
  {completed_count:0,completed_articles:{}},
 ]) assert.equal((await fixture(batch).put(1)).status,409);
});
test('nonempty legacy batches cannot claim unproven publication; empty legacy stays valid',async()=>{
 for(const count of [0,1])assert.equal((await fixture({completed_count:1}).put(count)).status,409);
 for(const batch of [{},{completed_count:0}])assert.equal((await fixture(batch).put(0)).status,200);
});
test('runtime read rejects stored incorrect counts rather than displaying false success',async()=>{
 const f=fixture({completed_count:1,completed_articles:[entry]});f.corrupt(0);
 assert.equal((await f.read()).publication_status,'verification_pending');
 assert.equal((await f.put(1)).status,409); // Existing conflicting receipt needs operator reconciliation.
});

test('status synchronizer rejects legacy nonempty evidence before any remote request',async()=>{
 const {mkdtemp,mkdir,writeFile,rm}=await import('node:fs/promises');
 const {join}=await import('node:path');
 const {writeBuildSnapshot,prepareRelease}=await import('../scripts/release-snapshot.mjs');
 const {recordDeployment}=await import('../scripts/record-deployment.mjs');
 const {syncReleaseStatus}=await import('../scripts/sync-release-status.mjs');
 const cwd=process.cwd(),root=await mkdtemp('/tmp/legacy-publication-count-');process.chdir(root);
 try{
  for(const [path,value] of [['dist/client/papers.json',[]],['dist/client/batch-status.json',{completed_count:1}],['dist/.openai/hosting.json',{project_id:'site'}]]){
   await mkdir(join(root,path,'..'),{recursive:true});await writeFile(join(root,path),JSON.stringify(value));
  }
  await writeBuildSnapshot();const lease=await prepareRelease();
  await recordDeployment({site_id:'site',build_id:lease.build_id,deployment_id:'legacy',status:'succeeded',tests_passed:true,build_passed:true,verified_url:'https://reader.example',verified_at:'2026-10-19T00:00:00Z'});
  let requests=0;
  await assert.rejects(syncReleaseStatus('https://reader.example',{token:'fixture',fetcher:async()=>{requests++;throw new Error('Unexpected request');}}),/regenerate the batch/);
  assert.equal(requests,0);
 }finally{process.chdir(cwd);await rm(root,{recursive:true,force:true});}
});
