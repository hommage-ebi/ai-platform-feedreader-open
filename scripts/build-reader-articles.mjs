import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import createDOMPurify from 'dompurify';
import { contained, readCollection, versionsOf } from './content-contract.mjs';
// One canonical, side-effect-free projection for generation and verification.
export function expectedReaderBody(source,article,version) {
 const window=new JSDOM(source).window;
 try {
  const doc=window.document;
  const narratives=doc.querySelectorAll('.narrative');
  if(narratives.length!==1)throw new Error(`Expected one narrative: ${version.html_path}`);
  const narrative=narratives[0];
  const images=[...narrative.querySelectorAll('img')];
  if(images.length!==version.stored_images.length||images.some((img,i)=>img.getAttribute('src')!==version.stored_images[i].serving_path))throw new Error('Article images mismatch stored_images');
  for(const figure of narrative.querySelectorAll('figure'))if(!figure.querySelector('figcaption')?.textContent.trim())throw new Error('Missing caption');
  const purify=createDOMPurify(window);
  const html=purify.sanitize(narrative.innerHTML,{FORBID_TAGS:['style','script','iframe','object','embed','form'],FORBID_ATTR:['style'],ALLOW_DATA_ATTR:false});
  if(narrative.querySelector('script,iframe,object,embed,form')||[...narrative.querySelectorAll('*')].some(el=>[...el.attributes].some(a=>/^on/i.test(a.name))))throw new Error('Unsafe article HTML');
  const record={...article,...version,id:article.id,html};delete record.versionHistory;
  return record;
 } finally {window.close();}
}
export async function buildReaderArticles({articles='data/articles',out='dist/client'}={}) {
 const {papers}=await readCollection(articles);
 for(const article of papers) for(const version of versionsOf(article)) {
  const source=await readFile(contained(articles,version.html_path),'utf8');
  const record=expectedReaderBody(source,article,version);
  const target=resolve(out,'reader-articles',encodeURIComponent(article.id),`${version.version}.json`);
  await mkdir(dirname(target),{recursive:true});await writeFile(target,JSON.stringify(record,null,2)+'\n');
 }
 return papers.length;
}
if(process.argv[1]===fileURLToPath(import.meta.url))await buildReaderArticles();
