import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {withCollectionLock} from './collection-lock.mjs';
import {hash,validateSnapshot} from './release-snapshot.mjs';
import {transitionRelease} from './release-record.mjs';
export const deploymentRecordName=(buildId,deploymentId)=>`${buildId}-${hash(deploymentId)}.json`;
export async function saveDeployment(record){
 const directory=resolve('data/releases');await mkdir(directory,{recursive:true});
 const attempt=resolve(directory,deploymentRecordName(record.build_id,record.deployment_id));
 let existing;try{existing=JSON.parse(await readFile(attempt,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 if(existing&&existing.status!=='verification_pending'&&existing.status!==record.status)throw new Error('Conflicting terminal deployment outcome');
 await writeFile(attempt+'.tmp',JSON.stringify(record,null,2)+'\n');await rename(attempt+'.tmp',attempt);
 const path=resolve(directory,record.build_id+'.json');
 await writeFile(path+'.tmp',JSON.stringify(record,null,2)+'\n');await rename(path+'.tmp',path);
}
export async function recordDeployment(update){
 return withCollectionLock('data/articles',async()=>{
 const lock=resolve('.sites-runtime/publish.lock'),guard=resolve(lock,'record.lock');await mkdir(guard);
 try{
 const lease=JSON.parse(await readFile(resolve(lock,'lease.json'),'utf8'));
 const bytes=await readFile(resolve(lease.snapshot,'.openai/release-manifest.json'));if(hash(bytes)!==lease.manifest_sha256)throw new Error('Frozen release manifest was modified');
 const manifest=JSON.parse(bytes);await validateSnapshot(lease.snapshot,manifest);
 if(update.build_id!==lease.build_id||update.site_id!==lease.site_id)throw new Error('Deployment does not match prepared build');
 if(lease.deployment_id&&lease.deployment_id!==update.deployment_id)throw new Error('Prepared build is bound to another deployment');
 if(lease.status&&lease.status!=='verification_pending'&&lease.status!==update.status)throw new Error('Conflicting deployment result');
 let previous;try{previous=JSON.parse(await readFile(resolve('data/releases',deploymentRecordName(lease.build_id,update.deployment_id)),'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 const record=transitionRelease(previous,{...update,collection_sha256:manifest.collection_sha256,artifact_sha256:manifest.artifact_sha256,articles:manifest.articles});
 lease.deployment_id=record.deployment_id;lease.status=record.status;
 await writeFile(resolve(lock,'lease.json.tmp'),JSON.stringify(lease,null,2)+'\n');await rename(resolve(lock,'lease.json.tmp'),resolve(lock,'lease.json'));
 await saveDeployment(record);return record;
 }finally{await rm(guard,{recursive:true,force:true});}
 });
}
if(process.argv[1]===fileURLToPath(import.meta.url)){if(process.argv.length!==3)throw new Error('Usage: node scripts/record-deployment.mjs DEPLOYMENT_RESULT_JSON');console.log(await recordDeployment(JSON.parse(await readFile(process.argv[2],'utf8'))));}
