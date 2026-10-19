import {publishedCount} from './publication-counts.mjs';
import {saveDeployment} from './record-deployment.mjs';
import {withCollectionLock} from './collection-lock.mjs';
import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fingerprintDist,hash,validateSnapshot} from './release-snapshot.mjs';
import {fileURLToPath} from 'node:url';
import {readCollection,versionsOf} from './content-contract.mjs';
export function transitionRelease(previous, update) {
 if(typeof update.site_id!=='string'||!update.site_id||update.site_id.includes('CONFIGURE_')||(previous?.site_id&&previous.site_id!==update.site_id))throw new Error('Wrong publication destination');
 if(!['verification_pending','succeeded','failed'].includes(update.status))throw new Error('Unknown release status');
 if(previous?.status==='verification_pending'&&previous?.deployment_id&&previous.deployment_id!==update.deployment_id)throw new Error('Reconcile existing deployment before recording a different deployment');
 if(!update.deployment_id||typeof update.deployment_id!=='string')throw new Error('Deployment identifier required');
 if(update.status==='succeeded'&&(!update.verified_url||!update.verified_at||update.tests_passed!==true||update.build_passed!==true))throw new Error('Success requires actual deployment, test and build evidence');
 return {...previous,...update,recorded_at:new Date().toISOString()};
}
export async function recordRelease(runDir,update,articles='data/articles') {
 return withCollectionLock(articles,()=>recordReleaseLocked(runDir,update,articles));
}
async function recordReleaseLocked(runDir,update,articles) {
 const path=resolve(runDir,'release.json'),lock=resolve(runDir,'.release.lock');await mkdir(runDir,{recursive:true});
 try{await mkdir(lock);}catch{throw new Error('Release record is already being written');}
 const globalLock=resolve('.sites-runtime/publish.lock/record.lock');let ownsGlobal=false;
 try{
  try{await mkdir(globalLock);ownsGlobal=true;}catch{throw new Error('Publication record is unavailable or another run is being recorded');}
  let previous;try{previous=JSON.parse(await readFile(path,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
  const publishLock=resolve('.sites-runtime/publish.lock');
  const lease=JSON.parse(await readFile(resolve(publishLock,'lease.json'),'utf8'));
  const manifestBytes=await readFile(resolve(lease.snapshot,'.openai/release-manifest.json'));
  if(hash(manifestBytes)!==lease.manifest_sha256)throw new Error('Frozen release manifest was modified');
  const snapshot=JSON.parse(manifestBytes);
  await validateSnapshot(lease.snapshot,snapshot);
  if(update.build_id!==lease.build_id||update.site_id!==lease.site_id)throw new Error('Deployment does not match the prepared build lease');
  if(lease.deployment_id&&lease.deployment_id!==update.deployment_id)throw new Error('Prepared build is already bound to another deployment');
  if(lease.status&&lease.status!=='verification_pending'&&lease.status!==update.status)throw new Error('Conflicting result for one deployment');
  if(await fingerprintDist(lease.snapshot)!==snapshot.artifact_sha256)throw new Error('Frozen deployment snapshot was modified');
  const record=transitionRelease(previous,{...update,collection_sha256:snapshot.collection_sha256,artifact_sha256:snapshot.artifact_sha256,articles:snapshot.articles});
  // Bind the deployment before any per-run writes, so interrupted recording
  // cannot allow another run to attach this snapshot to a different deployment.
  lease.deployment_id=record.deployment_id;lease.status=record.status;
  await writeFile(resolve(publishLock,'lease.json.tmp'),JSON.stringify(lease,null,2)+'\n');await rename(resolve(publishLock,'lease.json.tmp'),resolve(publishLock,'lease.json'));
  await saveDeployment(record);
  await writeFile(path+'.tmp',JSON.stringify(record,null,2)+'\n');await rename(path+'.tmp',path);
  // Public counts reflect verified publication only, never a build or upload alone.
  const ledgerPath=resolve(runDir,'ledger.json');
  let ledger;try{ledger=JSON.parse(await readFile(ledgerPath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
  if(ledger?.target_date&&ledger?.run_id) {
    const name=`${ledger.target_date}-${ledger.run_id}.json`;
    if(!/^[0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+\.json$/.test(name))throw new Error('Unsafe run identity');
    const batchPath=resolve(articles,'batch-history',name);
    let batch;try{batch=JSON.parse(await readFile(batchPath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
    if(batch) {
      batch.publication_status=record.status;
      const candidates=Array.isArray(batch.completed_articles)?[]:JSON.parse(await readFile(resolve(runDir,'run/candidates.json'),'utf8'));
      batch.published_count=record.status==='succeeded'?publishedCount(batch,record.articles,candidates):0;
      batch.updated_at=record.recorded_at;
      await writeFile(batchPath+'.tmp',JSON.stringify(batch,null,2)+'\n');await rename(batchPath+'.tmp',batchPath);
      const latestPath=resolve(articles,'batch-status.json');
      const latest=JSON.parse(await readFile(latestPath,'utf8'));
      if(latest.target_date===batch.target_date&&latest.run_id===batch.run_id){await writeFile(latestPath+'.tmp',JSON.stringify(batch,null,2)+'\n');await rename(latestPath+'.tmp',latestPath);}
    }
  }
  lease.deployment_id=record.deployment_id;lease.status=record.status;lease.recorded_runs=lease.recorded_runs||[];
  const runIdentity=ledger?.target_date&&ledger?.run_id?`${ledger.target_date}/${ledger.run_id}`:null;
  if(runIdentity){lease.recorded_runs=lease.recorded_runs.filter(id=>id!==runIdentity);if(record.status!=='verification_pending')lease.recorded_runs.push(runIdentity);}
  await writeFile(resolve(publishLock,'lease.json.tmp'),JSON.stringify(lease,null,2)+'\n');await rename(resolve(publishLock,'lease.json.tmp'),resolve(publishLock,'lease.json'));
  return record;
 }finally{if(ownsGlobal)await rm(globalLock,{recursive:true,force:true});await rm(lock,{recursive:true,force:true});}
}
if(process.argv[1]===fileURLToPath(import.meta.url)) {
 if(process.argv.length!==4)throw new Error('Usage: node scripts/release-record.mjs RUN_DIR DEPLOYMENT_RESULT_JSON');
 console.log(await recordRelease(process.argv[2],JSON.parse(await readFile(process.argv[3],'utf8'))));
}
