import {frontendLicenseFile, frontendLicenses} from './third-party-licenses.mjs';
import { mkdir, readFile, writeFile, copyFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { readCollection, contained, versionsOf } from './content-contract.mjs';
import {validateArticleImagePaths} from '../worker/image-contract.js';
const allowedTags = new Set('html head body title meta link script article section main header footer nav aside div span p h1 h2 h3 h4 h5 h6 a img figure figcaption ul ol li dl dt dd table caption colgroup col thead tbody tfoot tr th td pre code blockquote strong em b i u s del ins sub sup small mark abbr time br hr wbr details summary'.split(' '));
function safeLink(value) {
 if (/[\u0000-\u0020\u007f\\]/.test(value) || value.startsWith('//')) return false;
 if (value.startsWith('#') || value.startsWith('/') && !value.startsWith('//')) return true;
 try { return ['http:', 'https:'].includes(new URL(value).protocol); } catch { return false; }
}
/** Reject unsafe source without rewriting it: only inject the shared CSS/module. */
export function stageHTML(html, version = {}) {
 const dom = new JSDOM(html, {includeNodeLocations: true});
 try {
  const doc = dom.window.document;
  if (doc.querySelectorAll('.narrative').length !== 1) throw new Error('Standalone must contain exactly one narrative');
  const scripts = [], styles = [], images = [];
  for (const element of doc.querySelectorAll('*')) {
   const tag = element.localName;
   if (element.namespaceURI !== 'http://www.w3.org/1999/xhtml' || !allowedTags.has(tag)) throw new Error(`Unsafe standalone element: ${tag}`);
   for (const attr of element.attributes) {
    const name = attr.name.toLowerCase();
    if (name.startsWith('on') || ['style','srcset','srcdoc','background','poster','action','formaction','ping','autofocus','is','http-equiv'].includes(name)) throw new Error(`Unsafe standalone attribute: ${name}`);
    if (name === 'src' && tag !== 'script' && tag !== 'img') throw new Error('Unexpected standalone source');
    if (name === 'href' && tag !== 'a' && tag !== 'link') throw new Error('Unexpected standalone link');
    if (name === 'href' && tag === 'a' && !safeLink(attr.value)) throw new Error('Unsafe standalone URL');
    if (name === 'cite' && !safeLink(attr.value)) throw new Error('Unsafe standalone citation URL');
   }
   if (tag === 'script') {
    if (element.getAttribute('type') !== 'module' || element.getAttribute('src') !== '/standalone.js' || element.textContent.trim() || element.attributes.length !== 2 || element.closest('.narrative')) throw new Error('Unsafe standalone script');
    scripts.push(element);
   }
   if (tag === 'link') {
    if (element.getAttribute('rel') !== 'stylesheet' || element.getAttribute('href') !== '/assets/reader.css' || [...element.attributes].some(a => !['rel','href'].includes(a.name))) throw new Error('Unsafe standalone stylesheet');
    styles.push(element);
   }
   if (tag === 'img') {
    if (!element.closest('.narrative')) throw new Error('Standalone image outside narrative');
    const path = element.getAttribute('src');
    if(!(version.stored_images||[]).some(image=>image.serving_path===path)&&!(version.linked_images||[]).some(image=>image.url===path))throw new Error('Unregistered standalone image');
    images.push(path);
   }
  }
  if (scripts.length > 1 || styles.length > 1) throw new Error('Duplicate standalone assets');
  validateArticleImagePaths(images,version);
  const additions = [];
  if (!styles.length) { const end = dom.nodeLocation(doc.head)?.endTag?.startOffset; if (end === undefined) throw new Error('Standalone requires an explicit closing head'); additions.push([end, '<link rel="stylesheet" href="/assets/reader.css">']); }
  if (!scripts.length) { const end = dom.nodeLocation(doc.body)?.endTag?.startOffset; if (end === undefined) throw new Error('Standalone requires an explicit closing body'); additions.push([end, '<script type="module" src="/standalone.js"></script>']); }
  for (const [offset, markup] of additions.sort((a,b) => b[0]-a[0])) html = html.slice(0,offset) + markup + html.slice(offset);
  return html;
 } finally { dom.window.close(); }
}
export async function stagePublic({articles='data/articles',out='dist/client'}={}) {
 const {papers}=await readCollection(articles);await mkdir(out,{recursive:true});
 await writeFile(resolve(out,frontendLicenseFile),await frontendLicenses());
 for(const name of ['papers.json','images-manifest.json','batch-status.json'])await copyFile(resolve(articles,name),resolve(out,name));
 for(const article of papers)for(const version of versionsOf(article)) {
   const from=contained(articles,version.html_path),to=contained(out,version.html_path);
   let html=await readFile(from,'utf8');
   // Standalone uses the same bundled math/image implementation as the Reader.
   html=stageHTML(html,version);
   await mkdir(dirname(to),{recursive:true});await writeFile(to,html);
 }
}
if(process.argv[1]===fileURLToPath(import.meta.url))await stagePublic();
