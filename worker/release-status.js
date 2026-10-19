import {privateSitesBoundary} from './storage.js';
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
async function asset(env,origin,path){const response=await env.ASSETS.fetch(new Request(origin+path));if(!response.ok)throw new Error('Missing release asset');return response.json();}
async function identity(env,origin){const value=await asset(env,origin,'/release-identity.json');if(value.schema_version!==1||!/^[a-f0-9]{64}$/.test(value.build_id))throw new Error('Missing release identity');return value;}
const key=id=>`release-status/${id}.json`;
function exactPublishedCount(batch,articles){
 if(!Array.isArray(articles))return null;
 if(!Array.isArray(batch.completed_articles)){
  if(batch.completed_articles!==undefined)return null;
  // Old empty batches have no article identities to prove. Nonempty legacy
  // batches must be regenerated rather than trusting a caller-provided count.
  return batch.completed_count===undefined||batch.completed_count===0?0:null;
 }
 if(!Number.isSafeInteger(batch.completed_count)||batch.completed_count!==batch.completed_articles.length)return null;
 const key=p=>JSON.stringify([p.article_id,p.version,p.content_sha256]);
 const completed=batch.completed_articles;
 if(completed.some(p=>!p||typeof p.article_id!=='string'||!p.article_id||typeof p.version!=='string'||!p.version||!/^[a-f0-9]{64}$/.test(p.content_sha256||'')))return null;
 const unique=new Set(completed.map(key));if(unique.size!==completed.length)return null;
 const deployed=new Set(articles.map(key));return [...unique].filter(id=>deployed.has(id)).length;
}
function validBatch(receipt,batch,articles){
 const value=receipt.latest_batch;
 if(!value||typeof value!=='object'||Array.isArray(value))return false;
 const expected=exactPublishedCount(batch,articles);if(expected===null)return false;
 const mutable=new Set(['published_count','publication_status','updated_at']);
 const stable=value=>Object.keys(value).filter(k=>!mutable.has(k)).sort().map(k=>[k,value[k]]);
 return JSON.stringify(stable(value))===JSON.stringify(stable(batch))&&value.publication_status==='succeeded'&&Number.isSafeInteger(value.published_count)&&value.published_count===expected&&value.updated_at===receipt.verified_at;
}
function validReceipt(receipt,current){
 return receipt&&receipt.schema_version===1&&receipt.status==='succeeded'&&receipt.build_id===current.build_id&&receipt.site_id===current.site_id&&receipt.collection_sha256===current.collection_sha256&&typeof receipt.deployment_id==='string'&&receipt.deployment_id.length>0&&typeof receipt.verified_url==='string'&&/^https:\/\//.test(receipt.verified_url)&&typeof receipt.verified_at==='string'&&Number.isFinite(Date.parse(receipt.verified_at));
}
export async function deploymentStatusRequest(request,env){
 if(!privateSitesBoundary(env)||env.SITES_STORAGE_ENABLED!=='true'||!env.IMAGES)return json({error:'Publication status unavailable'},503);
 const visitor=request.headers.get('oai-authenticated-user-id');
 if(visitor&&(!env.OWNER_USER_ID||visitor!==env.OWNER_USER_ID))return json({error:'Forbidden'},403);
 const url=new URL(request.url),origin=request.headers.get('Origin');
 if(origin&&origin!==url.origin)return json({error:'Same-origin request required'},403);
 if(url.search||!['GET','PUT'].includes(request.method))return json({error:'Invalid status request'},400);
 const current=await identity(env,url.origin);
 if(request.method==='PUT'){
  if(request.headers.get('X-Reader-Release')!=='1')return json({error:'Release header required'},403);
  if(request.headers.get('Content-Type')!=='application/json')return json({error:'JSON required'},415);
  const reader=request.body?.getReader();let size=0,parts=[];
  if(reader)while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>1024*1024){await reader.cancel();return json({error:'Receipt too large'},413);}parts.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length;}
  let receipt;try{receipt=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));}catch{return json({error:'Invalid JSON'},400);}
  const papers=await asset(env,url.origin,'/papers.json');
  const articles=papers.flatMap(p=>(p.versionHistory?.length?p.versionHistory:[p]).map(v=>({article_id:p.id,version:v.version||'current',...(v.content_sha256?{content_sha256:v.content_sha256}:{})})));
  if(!validReceipt(receipt,current)||JSON.stringify(receipt.articles)!==JSON.stringify(articles))return json({error:'Receipt does not match deployed build'},409);
  const batch=await asset(env,url.origin,'/batch-status.json');
  if(!validBatch(receipt,batch,articles))return json({error:'Batch does not match deployed collection'},409);
  const previous=await env.IMAGES.get(key(current.build_id));
  if(previous){
   const existing=JSON.parse(await previous.text());
   if(JSON.stringify(existing)!==JSON.stringify(receipt)){
    const sameBuild=['site_id','build_id','collection_sha256','articles'].every(field=>JSON.stringify(existing[field])===JSON.stringify(receipt[field]));
    const stableBatch=value=>({...value,updated_at:null});
    if(existing.deployment_id===receipt.deployment_id||!sameBuild||JSON.stringify(stableBatch(existing.latest_batch))!==JSON.stringify(stableBatch(receipt.latest_batch))||Date.parse(receipt.verified_at)<=Date.parse(existing.verified_at))return json({error:'Conflicting or stale publication receipt'},409);
    await env.IMAGES.put(key(current.build_id),JSON.stringify(receipt),{httpMetadata:{contentType:'application/json'}});
   }
  }else await env.IMAGES.put(key(current.build_id),JSON.stringify(receipt),{httpMetadata:{contentType:'application/json'}});
 }
 const object=await env.IMAGES.get(key(current.build_id));
 return object?json(JSON.parse(await object.text())):json({status:'verification_pending',build_id:current.build_id},404);
}
export async function publishedBatch(request,env){
 const origin=new URL(request.url).origin,batch=await asset(env,origin,'/batch-status.json');
 let result={...batch,published_count:0,publication_status:'verification_pending'};
 try{const current=await identity(env,origin),object=await env.IMAGES?.get(key(current.build_id));if(object){const receipt=JSON.parse(await object.text());if(validReceipt(receipt,current)&&Array.isArray(receipt.articles)&&validBatch(receipt,batch,receipt.articles))result=receipt.latest_batch;}}catch{}
 return request.method==='HEAD'?new Response(null,{headers:{'Content-Type':'application/json'}}):json(result);
}
