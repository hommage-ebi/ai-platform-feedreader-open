import {syncReleaseStatus} from '../scripts/sync-release-status.mjs';
const SITE_ID='example-site';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {writeBuildSnapshot,prepareRelease,fingerprintDist,finalizeRelease} from '../scripts/release-snapshot.mjs';
import {recordRelease} from '../scripts/release-record.mjs';
async function fixture(fn){const root=await mkdtemp(join(tmpdir(),'release-'));try{for(const d of ['client','worker','.openai'])await mkdir(join(root,'dist',d),{recursive:true});await writeFile(join(root,'dist/client/papers.json'),'[{"id":"a","version":"v1"}]');await writeFile(join(root,'dist/client/batch-status.json'),'{}');await writeFile(join(root,'dist/worker/index.js'),'export default {}');await writeFile(join(root,'dist/.openai/hosting.json'),JSON.stringify({project_id:SITE_ID}));await fn(root);}finally{await rm(root,{recursive:true,force:true});}}
test('release freezes the built article IDs even after source/build output changes',()=>fixture(async root=>{const manifest=await writeBuildSnapshot(join(root,'dist'));const lease=await prepareRelease(root);assert.equal(lease.build_id,manifest.build_id);await writeFile(join(root,'dist/client/papers.json'),'[{"id":"a","version":"v1"},{"id":"b","version":"v1"}]');const frozen=JSON.parse(await readFile(join(lease.snapshot,'.openai/release-manifest.json'),'utf8'));assert.deepEqual(frozen.articles,[{article_id:'a',version:'v1'}]);assert.equal(await fingerprintDist(lease.snapshot),manifest.artifact_sha256);await assert.rejects(prepareRelease(root),/publication is already pending/);}));
test('tampered build and simultaneous build cannot become publication snapshots',()=>fixture(async root=>{await writeBuildSnapshot(join(root,'dist'));await writeFile(join(root,'dist/worker/index.js'),'tampered');await assert.rejects(prepareRelease(root),/changed/);await writeBuildSnapshot(join(root,'dist'));await mkdir(join(root,'.sites-runtime/build.lock'));await assert.rejects(prepareRelease(root),/Build is running/);}));

test('recorded publication counts only frozen deployed versions, not later canonical additions',()=>fixture(async root=>{
 await writeBuildSnapshot(join(root,'dist'));const lease=await prepareRelease(root);
 const run=join(root,'data/runs/2026-10-18/test'),articles=join(root,'data/articles');await mkdir(join(run,'run'),{recursive:true});await mkdir(join(articles,'batch-history'),{recursive:true});
 await writeFile(join(run,'ledger.json'),JSON.stringify({target_date:'2026-10-18',run_id:'test'}));await writeFile(join(run,'run/candidates.json'),JSON.stringify([{id:'a',version:'v1'},{id:'b',version:'v1'}]));
 const batch={target_date:'2026-10-18',run_id:'test',completed_count:2};await writeFile(join(articles,'batch-status.json'),JSON.stringify(batch));await writeFile(join(articles,'batch-history/2026-10-18-test.json'),JSON.stringify(batch));
 await writeFile(join(articles,'papers.json'),'[{"id":"a","version":"v1"},{"id":"b","version":"v1"}]');
 const cwd=process.cwd();try{process.chdir(root);const record=await recordRelease(run,{site_id:SITE_ID,build_id:lease.build_id,deployment_id:'d1',status:'succeeded',tests_passed:true,build_passed:true,verified_url:'https://reader.example',verified_at:new Date().toISOString()},articles);assert.deepEqual(record.articles,[{article_id:'a',version:'v1'}]);assert.equal(JSON.parse(await readFile(join(articles,'batch-status.json'),'utf8')).published_count,1);}finally{process.chdir(cwd);}
}));

test('one deployment records today and backlog before explicit finalization; lease binds deployment ID',()=>fixture(async root=>{
 const manifest=await writeBuildSnapshot(join(root,'dist'));manifest.runs=['2026-10-18/today','2026-10-17/backlog'];await writeFile(join(root,'dist/.openai/release-manifest.json'),JSON.stringify(manifest));const lease=await prepareRelease(root);
 const articles=join(root,'data/articles');await mkdir(join(articles,'batch-history'),{recursive:true});await writeFile(join(articles,'batch-status.json'),'{}');
 const dirs=[];for(const identity of manifest.runs){const [date,id]=identity.split('/'),run=join(root,'data/runs',date,id);dirs.push(run);await mkdir(run,{recursive:true});await writeFile(join(run,'ledger.json'),JSON.stringify({target_date:date,run_id:id}));}
 const update={site_id:SITE_ID,build_id:lease.build_id,deployment_id:'shared',status:'verification_pending'};
 const cwd=process.cwd();try{process.chdir(root);await recordRelease(dirs[0],update,articles);await assert.rejects(recordRelease(dirs[1],{...update,deployment_id:'different'},articles),/another deployment/);await assert.rejects(finalizeRelease(root),/not verified/);
 const success={...update,status:'succeeded',tests_passed:true,build_passed:true,verified_url:'https://reader.example',verified_at:new Date().toISOString()};await recordRelease(dirs[0],success,articles);await assert.rejects(finalizeRelease(root),/Unrecorded runs/);await recordRelease(dirs[1],success,articles);let receipt;await syncReleaseStatus('https://reader.example',{token:'test',fetcher:async(url,options)=>{if(options.method==='PUT')receipt=JSON.parse(options.body);return Response.json(receipt);}});assert.equal((await finalizeRelease(root)).finalized,true);
 }finally{process.chdir(cwd);}
}));
test('build discovers only unfinished runs intersecting deployed versions from real batch history',()=>fixture(async root=>{
 const history=join(root,'data/articles/batch-history');await mkdir(history,{recursive:true});
 for(const [id,article,status,published] of [['pending','a','not_deployed',0],['done','a','succeeded',1],['unrelated','b','not_deployed',0]]){
  const run=join(root,'data/runs/2026-10-18',id,'run');await mkdir(run,{recursive:true});await writeFile(join(run,'candidates.json'),JSON.stringify([{id:article,version:'v1'}]));await writeFile(join(history,`2026-10-18-${id}.json`),JSON.stringify({target_date:'2026-10-18',run_id:id,publication_status:status,published_count:published,completed_count:1}));
 }
 const cwd=process.cwd();try{process.chdir(root);const manifest=await writeBuildSnapshot(join(root,'dist'));assert.deepEqual(manifest.runs,['2026-10-18/pending']);}finally{process.chdir(cwd);}
}));

test('manifest article tampering is rejected before prepare and after freeze',()=>fixture(async root=>{
 await writeBuildSnapshot(join(root,'dist'));const path=join(root,'dist/.openai/release-manifest.json');const valid=JSON.parse(await readFile(path,'utf8'));
 await writeFile(path,JSON.stringify({...valid,articles:[{article_id:'never-deployed',version:'v9'}]}));await assert.rejects(prepareRelease(root),/manifest does not match/);
 await writeFile(path,JSON.stringify(valid));const lease=await prepareRelease(root);await writeFile(join(lease.snapshot,'.openai/release-manifest.json'),JSON.stringify({...valid,articles:[{article_id:'never-deployed',version:'v9'}]}));
 const cwd=process.cwd();try{process.chdir(root);await assert.rejects(recordRelease(join(root,'run'),{site_id:SITE_ID,build_id:lease.build_id,deployment_id:'d1',status:'verification_pending'},join(root,'data/articles')),/manifest was modified/);}finally{process.chdir(cwd);}
}));
