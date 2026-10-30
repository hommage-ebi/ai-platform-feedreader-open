import {publishedCount} from './publication-counts.mjs';
import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {validateSnapshot,hash} from './release-snapshot.mjs';
// urllib uses the runtime's HTTPS proxy and CA configuration. Credentials and
// payload travel through stdin, never in arguments or temporary files.
export function pythonStatusFetcher(python=process.env.READER_PYTHON||'python3'){
 return (url,options={})=>new Promise((resolveRequest,reject)=>{
  const child=spawn(python,[fileURLToPath(new URL('./site-status-fetch.py',import.meta.url))],{stdio:['pipe','pipe','pipe']});
  let stdout='',stderr='';const timer=setTimeout(()=>child.kill(),65000);
  child.stdout.on('data',chunk=>{stdout+=chunk;if(stdout.length>8*1024*1024)child.kill();});
  child.stderr.on('data',chunk=>{stderr+=chunk;if(stderr.length>4096)child.kill();});
  child.on('error',error=>{clearTimeout(timer);reject(error);});
  child.on('close',code=>{clearTimeout(timer);try{const result=JSON.parse(stdout);if(code!==0||result.error)throw new Error(result.error||'Site status transport failed');resolveRequest({ok:result.status>=200&&result.status<300,status:result.status,json:async()=>JSON.parse(result.body)});}catch(error){reject(new Error('Site status transport failed: '+error.message));}});
  child.stdin.on('error',()=>{});
  child.stdin.end(JSON.stringify({url,method:options.method||'GET',headers:options.headers,body:options.body??null}));
 });
}
export async function syncReleaseStatus(siteURL,{fetcher=fetch,token=process.env.SITES_SERVICE_TOKEN,timeoutMs=60000}={}){
 const url=new URL(siteURL);if(url.protocol!=='https:'||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw new Error('Use the exact HTTPS Site origin');
 if(!token)throw new Error('SITES_SERVICE_TOKEN is required in the process environment');
 const lock=resolve('.sites-runtime/publish.lock'),guard=resolve(lock,'record.lock');await mkdir(guard);
 try{
 const lease=JSON.parse(await readFile(resolve(lock,'lease.json'),'utf8'));
 if(lease.status!=='succeeded')throw new Error('Only verified successful deployments publish status');
 if((lease.required_runs||[]).some(id=>!(lease.recorded_runs||[]).includes(id)))throw new Error('Record every required run before synchronizing publication status');
 const bytes=await readFile(resolve(lease.snapshot,'.openai/release-manifest.json'));if(hash(bytes)!==lease.manifest_sha256)throw new Error('Frozen release manifest was modified');
 await validateSnapshot(lease.snapshot,JSON.parse(bytes));
 const record=JSON.parse(await readFile(resolve('data/releases',lease.build_id+'.json'),'utf8'));
 if(record.status!=='succeeded'||record.deployment_id!==lease.deployment_id||record.site_id!==lease.site_id)throw new Error('Deployment record does not match lease');
 if(new URL(record.verified_url).origin!==url.origin)throw new Error('Status destination differs from verified deployment');
 const batch=JSON.parse(await readFile(resolve(lease.snapshot,'client/batch-status.json'),'utf8'));
 if(!Array.isArray(batch.completed_articles)&&(batch.completed_articles!==undefined||batch.completed_count!==undefined&&batch.completed_count!==0))throw new Error('Legacy nonempty batch lacks exact completed article identities; regenerate the batch and rebuild before publishing status');
 let count=0;
 if(batch.target_date&&batch.run_id){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(batch.target_date)||!/^[A-Za-z0-9_-]+$/.test(batch.run_id))throw new Error('Invalid batch identity');
  const candidates=Array.isArray(batch.completed_articles)?[]:JSON.parse(await readFile(resolve('data/runs',batch.target_date,batch.run_id,'run/candidates.json'),'utf8'));
  count=publishedCount(batch,record.articles,candidates);
 }
 const receipt={schema_version:1,site_id:record.site_id,build_id:record.build_id,collection_sha256:record.collection_sha256,deployment_id:record.deployment_id,status:'succeeded',verified_url:record.verified_url,verified_at:record.verified_at,articles:record.articles,latest_batch:{...batch,published_count:count,publication_status:'succeeded',updated_at:record.verified_at}};
 const target=url.origin+'/api/deployment-status';
 const headers={'OAI-Sites-Authorization':`Bearer ${token}`,'X-Reader-Release':'1','Content-Type':'application/json'};
 const written=await fetcher(target,{method:'PUT',headers,body:JSON.stringify(receipt),redirect:'error',signal:AbortSignal.timeout(timeoutMs)});if(!written.ok)throw new Error(`Publication status upload failed (${written.status})`);
 const response=await fetcher(target,{headers:{'OAI-Sites-Authorization':`Bearer ${token}`},redirect:'error',signal:AbortSignal.timeout(timeoutMs)});if(!response.ok||JSON.stringify(await response.json())!==JSON.stringify(receipt))throw new Error('Publication status read-back mismatch');
 lease.remote_status_verified=true;lease.remote_status_url=target;
 await writeFile(resolve(lock,'lease.json.tmp'),JSON.stringify(lease,null,2)+'\n');await rename(resolve(lock,'lease.json.tmp'),resolve(lock,'lease.json'));
 return {verified:true,build_id:lease.build_id};
 }finally{await rm(guard,{recursive:true,force:true});}
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
 if(process.argv.length<3||process.argv.length>4||(process.argv[3]&&process.argv[3]!=='--transport=python'))throw new Error('Usage: node scripts/sync-release-status.mjs HTTPS_SITE_ORIGIN [--transport=python]');
 console.log(await syncReleaseStatus(process.argv[2],process.argv[3]?{fetcher:pythonStatusFetcher()}:{}));
}
