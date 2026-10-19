import {withCollectionLock} from './collection-lock.mjs';
import {readFile,readdir,writeFile,mkdir,cp,rm} from 'node:fs/promises';
import {resolve,relative} from 'node:path';import {createHash} from 'node:crypto';
export const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
async function files(root,dir=root){let result=[];for(const entry of await readdir(dir,{withFileTypes:true})){const path=resolve(dir,entry.name);if(entry.isDirectory())result.push(...await files(root,path));else result.push(relative(root,path));}return result.sort();}
export async function fingerprintDist(dist){const entries=[];for(const path of await files(dist)){if(['.openai/release-manifest.json','client/release-identity.json'].includes(path))continue;entries.push([path,hash(await readFile(resolve(dist,path)))]);}return hash(JSON.stringify(entries));}
function articleIdentities(papers){return papers.flatMap(p=>(p.versionHistory?.length?p.versionHistory:[p]).map(v=>({article_id:p.id,version:v.version||'current',...(v.content_sha256?{content_sha256:v.content_sha256}:{})})));}
export async function validateSnapshot(dist,manifest){
 const bytes=await readFile(resolve(dist,'client/papers.json'));
 const config=JSON.parse(await readFile(resolve(dist,'.openai/hosting.json'),'utf8'));
 if(manifest.collection_sha256!==hash(bytes)||JSON.stringify(manifest.articles)!==JSON.stringify(articleIdentities(JSON.parse(bytes)))||manifest.site_id!==config.project_id||manifest.build_id!==manifest.artifact_sha256)throw new Error('Release manifest does not match built collection/configuration');
 const identity=JSON.parse(await readFile(resolve(dist,'client/release-identity.json'),'utf8'));
 if(identity.schema_version!==1||Object.keys(identity).sort().join(',')!=='build_id,collection_sha256,schema_version,site_id'||identity.build_id!==manifest.build_id||identity.site_id!==manifest.site_id||identity.collection_sha256!==manifest.collection_sha256)throw new Error('Release identity does not match snapshot');
 if(await fingerprintDist(dist)!==manifest.artifact_sha256)throw new Error('Build artifact changed since validation');
}
export async function writeBuildSnapshot(dist='dist'){
 const papersBytes=await readFile(resolve(dist,'client/papers.json'));const papers=JSON.parse(papersBytes);
 const config=JSON.parse(await readFile(resolve(dist,'.openai/hosting.json'),'utf8'));
 const deployedKeys=new Set(papers.flatMap(p=>(p.versionHistory?.length?p.versionHistory:[p]).map(v=>JSON.stringify([p.id,v.version||'current']))));
 let runs=[],batchFiles=[];try{batchFiles=await readdir('data/articles/batch-history');}catch(error){if(error.code!=='ENOENT')throw error;}
 for(const name of batchFiles){
  if(!name.endsWith('.json'))continue;const batch=JSON.parse(await readFile(resolve('data/articles/batch-history',name),'utf8'));
  if(!batch.target_date||!batch.run_id||batch.publication_status==='succeeded'&&batch.published_count>=batch.completed_count)continue;
  if(!/^\d{4}-\d{2}-\d{2}$/.test(batch.target_date)||!/^[A-Za-z0-9_-]+$/.test(batch.run_id))throw new Error('Invalid batch identity');
  const candidates=JSON.parse(await readFile(resolve('data/runs',batch.target_date,batch.run_id,'run/candidates.json'),'utf8'));
  if(candidates.some(p=>deployedKeys.has(JSON.stringify([p.id,p.version]))))runs.push(`${batch.target_date}/${batch.run_id}`);
 }

 const manifest={schema_version:1,runs,site_id:config.project_id,created_at:new Date().toISOString(),collection_sha256:hash(papersBytes),articles:articleIdentities(papers),artifact_sha256:await fingerprintDist(dist)};
 manifest.build_id=manifest.artifact_sha256;
 await writeFile(resolve(dist,'client/release-identity.json'),JSON.stringify({schema_version:1,build_id:manifest.build_id,site_id:manifest.site_id,collection_sha256:manifest.collection_sha256})+'\n');
 await writeFile(resolve(dist,'.openai/release-manifest.json'),JSON.stringify(manifest,null,2)+'\n');return manifest;
}
export async function prepareRelease(root=process.cwd()){
 const runtime=resolve(root,'.sites-runtime'),lock=resolve(runtime,'publish.lock'),buildLock=resolve(runtime,'build.lock');await mkdir(runtime,{recursive:true});
 try{await mkdir(lock);}catch{throw new Error('A publication is already pending; reconcile it before preparing another');}
 let ownsBuildLock=false;
 try{
  try{await mkdir(buildLock);ownsBuildLock=true;}catch{throw new Error('Build is running; wait before freezing a release');}
  return await withCollectionLock(resolve(root,'data/articles'),async()=>{
  const dist=resolve(root,'dist'),manifest=JSON.parse(await readFile(resolve(dist,'.openai/release-manifest.json'),'utf8'));
  if(!manifest.site_id||manifest.site_id.includes('CONFIGURE_'))throw new Error('Configure your private Site project ID before publication');
  await validateSnapshot(dist,manifest);
  const snapshot=resolve(runtime,'releases',manifest.build_id,'dist');await mkdir(resolve(snapshot,'..'),{recursive:true});await cp(dist,snapshot,{recursive:true});
  await validateSnapshot(snapshot,manifest);
  const lease={manifest_sha256:hash(await readFile(resolve(snapshot,'.openai/release-manifest.json'))),site_id:manifest.site_id,build_id:manifest.build_id,required_runs:manifest.runs||[],recorded_runs:[],snapshot,prepared_at:new Date().toISOString()};
  await writeFile(resolve(lock,'lease.json'),JSON.stringify(lease,null,2)+'\n');return lease;
  });
 }catch(error){await rm(lock,{recursive:true,force:true});throw error;}finally{if(ownsBuildLock)await rm(buildLock,{recursive:true,force:true});}
}

export async function finalizeRelease(root=process.cwd()) {
 const lock=resolve(root,'.sites-runtime/publish.lock'),recordLock=resolve(lock,'record.lock');
 try{await mkdir(recordLock);}catch{throw new Error('Publication is being recorded or has no active lease');}
 try{const lease=JSON.parse(await readFile(resolve(lock,'lease.json'),'utf8'));
 if(!lease.deployment_id||!['succeeded','failed'].includes(lease.status))throw new Error('Deployment outcome is not verified');
 const missing=(lease.required_runs||[]).filter(run=>!(lease.recorded_runs||[]).includes(run));
 if(missing.length)throw new Error(`Unrecorded runs: ${missing.join(', ')}`);
 if(lease.status==='succeeded'&&!lease.remote_status_verified)throw new Error('Published status has not been synchronized and verified');
 await rm(lock,{recursive:true,force:true});return {finalized:true,build_id:lease.build_id,deployment_id:lease.deployment_id};
 }finally{await rm(recordLock,{recursive:true,force:true});}
}
