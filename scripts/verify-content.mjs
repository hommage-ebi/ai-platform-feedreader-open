import {isDeepStrictEqual} from 'node:util';
import {expectedReaderBody} from './build-reader-articles.mjs';
import {frontendLicenseFile, frontendLicenses} from './third-party-licenses.mjs';
import { readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stageHTML } from './stage-public.mjs';
import { readCollection, versionsOf, contained } from './content-contract.mjs';
export async function verifyContent({articles='data/articles',out='dist/client'}={}) {
 const {papers}=await readCollection(articles);
 if(await readFile(resolve(out,frontendLicenseFile),'utf8')!==await frontendLicenses())throw new Error('Third-party license copy mismatch');
 for(const name of ['papers.json','images-manifest.json','batch-status.json'])if(!(await readFile(resolve(articles,name))).equals(await readFile(resolve(out,name))))throw new Error(`Copy mismatch ${name}`);
 for(const article of papers)for(const version of versionsOf(article)) {
  const source=await readFile(contained(articles,version.html_path),'utf8');
  const staged=await readFile(contained(out,version.html_path),'utf8');
  const expected=stageHTML(source,version);
  if(staged!==expected)throw new Error('Article copy changed');
  const body=JSON.parse(await readFile(resolve(out,'reader-articles',encodeURIComponent(article.id),`${version.version}.json`),'utf8'));
  const expectedBody=expectedReaderBody(source,article,version);
  if(!isDeepStrictEqual(body,expectedBody))throw new Error(`Reader body mismatch: ${article.id}@${version.version}`);
 }
 const allowed=new Set(['release-identity.json',frontendLicenseFile,'.assetsignore','reader-shell.html','assets','standalone.js','papers','papers.json','images-manifest.json','batch-status.json','reader-articles']);
 for(const name of await readdir(out))if(!allowed.has(name))throw new Error(`Unexpected public artifact: ${name}`);
 return {articles:papers.length,verified:true};
}
if(process.argv[1]===fileURLToPath(import.meta.url))console.log(await verifyContent());
