import test from 'node:test';
import assert from 'node:assert/strict';
import {isArxivImageURL,validateLinkedImages} from '../worker/image-contract.js';
import {stageHTML} from '../scripts/stage-public.mjs';
import {expectedReaderBody} from '../scripts/build-reader-articles.mjs';
const url='https://arxiv.org/html/2610.22855v1/x1.svg';
const image={source_ref:'selected:fig1',url,selection:{kind:'html',element_id:'F1',asset_index:0},source_document_sha256:'a'.repeat(64)};
const article={id:'2610.22855',source:'arxiv'},version={version:'v1',stored_images:[],linked_images:[image]};
test('arXiv HTML figures survive standalone and Reader projection as direct links',()=>{
 validateLinkedImages(article,version);
 const html=`<!doctype html><html><head></head><body><article><div class="narrative"><figure><img src="${url}" referrerpolicy="no-referrer"><figcaption>Figure 1 原図</figcaption></figure></div></article></body></html>`;
 assert.ok(stageHTML(html,version).includes(`src="${url}"`));
 assert.ok(expectedReaderBody(html,article,version).html.includes(`src="${url}"`));
 assert.throws(()=>stageHTML(html,{stored_images:[]}),/Unregistered/);
});
test('direct image registration rejects wrong versions, hosts and ambiguous paths',()=>{
 for(const bad of [url.replace('v1/','v2/'),url.replace('arxiv.org/','arxiv.org.evil.test/'),url.replace('/x1.svg','/../x1.svg'),url+'?track=1',url.replace('/x1.svg','/%2e%2e/x1.svg')])assert.throws(()=>validateLinkedImages(article,{...version,linked_images:[{...image,url:bad}]}),/Invalid direct/);
 assert.equal(isArxivImageURL(url),true);
 for(const bad of ['https://evil.test/x.png','javascript:bad','https://arxiv.org/html/2610.22855v1v1/x.png'])assert.equal(isArxivImageURL(bad),false);
});
