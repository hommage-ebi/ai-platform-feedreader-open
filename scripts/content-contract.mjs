import {validArticleId,htmlPaths} from '../worker/content-identity.js';
import {validateImageManifest} from '../worker/image-contract.js';
import {createHash} from 'node:crypto';
import { readFile, access } from 'node:fs/promises';
import { resolve, relative, isAbsolute } from 'node:path';
export function safeRelative(path) {
  if (typeof path !== 'string' || !path || path.includes('\\') || path.includes('\0') || isAbsolute(path) || path.split('/').some(x => x === '..' || x === '.')) throw new Error(`Unsafe content path: ${path}`);
  return path;
}
export function contained(root, path) {
  safeRelative(path); const target = resolve(root,path); const rel = relative(resolve(root),target);
  if (rel.startsWith('..') || isAbsolute(rel)) throw new Error('Path escapes content root'); return target;
}
export const versionsOf = article => article.versionHistory?.length ? article.versionHistory : [{...article,version:article.version || 'current',stored_images:article.stored_images || []}];
export async function readCollection(root='data/articles') {
  try { await access(resolve(root,'.collection-transaction.json')); throw new Error('Interrupted collection transaction; recover with reader_adapter before building'); } catch(error) { if(error.code!=='ENOENT')throw error; }
  const papers=JSON.parse(await readFile(resolve(root,'papers.json'),'utf8'));
  const manifest=JSON.parse(await readFile(resolve(root,'images-manifest.json'),'utf8'));
  if (!Array.isArray(papers) || manifest.schema_version!==1 || !Array.isArray(manifest.images)) throw new Error('Invalid collection schema');
  const ids=new Set(), images=new Map();
  for (const image of manifest.images) {
    const key=JSON.stringify([image.article_id,image.version,image.source_ref]);
    if(images.has(key)) throw new Error('Duplicate manifest mapping');
    if(!/^[a-f0-9]{64}$/.test(image.sha256)||image.mime_type!=='image/webp'||!['width','height','byte_size'].every(k=>Number.isSafeInteger(image[k])&&image[k]>0)) throw new Error('Invalid image properties');
    safeRelative(image.object_key);
    if(image.serving_path!==`/api/images/${image.sha256}`)throw new Error('Unregistered image serving convention');
    images.set(key,image);
  }
  for (const article of papers) {
    if(!validArticleId(article.id)||ids.has(article.id))throw new Error('Invalid/duplicate article id'); ids.add(article.id);
    if(typeof article.title!=='string'||!article.title)throw new Error('Missing title');
    const seen=new Set();
    for(const version of versionsOf(article)) {
      if(typeof version.version!=='string'||!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(version.version)||seen.has(version.version))throw new Error('Invalid/duplicate version');seen.add(version.version);
      const arxiv=String(article.source).toLowerCase()==='arxiv';
      if((arxiv||version.lead_ja!==undefined)&&(!Array.isArray(version.lead_ja)||version.lead_ja.length!==3||version.lead_ja.some(x=>typeof x!=='string'||!x.trim())))throw new Error('Exactly three lead items required');
      for(const field of ['authors','affiliations','institution_names'])if((arxiv||version[field]!==undefined)&&!Array.isArray(version[field]))throw new Error(`Invalid ${field}`);
      if(!Array.isArray(version.stored_images))throw new Error('Missing stored_images');
      if(!safeRelative(version.html_path).startsWith('papers/')||!version.html_path.endsWith('.html'))throw new Error('Article path must be papers/*.html');
      htmlPaths(version.html_path);
      if(version.content_sha256!==undefined){
        if(!/^[a-f0-9]{64}$/.test(version.content_sha256)||createHash('sha256').update(await readFile(contained(root,version.html_path))).digest('hex')!==version.content_sha256)throw new Error('Canonical article content hash mismatch');
      }
      for(const image of version.stored_images) {
        const registered=images.get(JSON.stringify([article.id,version.version,image.source_ref]));
        if(!registered||JSON.stringify(Object.keys(registered).sort().map(k=>[k,registered[k]]))!==JSON.stringify(Object.keys(image).sort().map(k=>[k,image[k]])))throw new Error('stored_images must exactly match manifest');
      }
    }
    const latest=versionsOf(article).find(v=>v.version===(article.version||'current'));
    if(!latest)throw new Error('Current article version absent from history');
    for(const field of ['lead_ja','authors','affiliations','institution_names','stored_images','html_path'])if(JSON.stringify(field==='stored_images'?(article[field]||[]):article[field])!==JSON.stringify(latest[field]))throw new Error(`Latest article ${field} differs from version`);
  }
  validateImageManifest(manifest,ids);
  const referenced=new Set(papers.flatMap(article=>versionsOf(article).flatMap(version=>version.stored_images.map(image=>JSON.stringify([article.id,version.version,image.source_ref])))));
  if([...images.keys()].some(key=>!referenced.has(key)))throw new Error('Orphan manifest image is not referenced by an article version');
  return {papers,manifest};
}
