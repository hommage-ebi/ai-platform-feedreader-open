/** Count the exact completed content in this run, not every candidate retained from older publications. */
export function publishedCount(batch,articles,candidates=[]){
 const completed=batch.completed_articles;
 let count;
 if(Array.isArray(completed)){
  const deployed=new Set(articles.map(p=>JSON.stringify([p.article_id,p.version,p.content_sha256])));
  const unique=new Set(completed.map(p=>JSON.stringify([p.article_id,p.version,p.content_sha256])));
  if(unique.size!==completed.length||completed.some(p=>typeof p.article_id!=='string'||typeof p.version!=='string'||!/^[a-f0-9]{64}$/.test(p.content_sha256||'')))throw new Error('Invalid completed article identities');
  count=[...unique].filter(key=>deployed.has(key)).length;
 }else{
  const ids=new Set(candidates.map(p=>JSON.stringify([p.id,p.version])));
  count=articles.filter(p=>ids.has(JSON.stringify([p.article_id,p.version]))).length;
 }
 if(Number.isSafeInteger(batch.completed_count)&&count>batch.completed_count)throw new Error('Publication count exceeds completed batch; regenerate batch with exact completed article identities');
 return count;
}
