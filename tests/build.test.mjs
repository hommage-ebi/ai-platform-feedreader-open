import {createHash} from 'node:crypto';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {stagePublic} from '../scripts/stage-public.mjs';
import {buildReaderArticles} from '../scripts/build-reader-articles.mjs';
import {verifyContent} from '../scripts/verify-content.mjs';
import {allowedTrackedPath} from '../scripts/check-retention.mjs';
async function fixture(fn) {
 const root=await mkdtemp(join(tmpdir(),'reader-build-')), articles=join(root,'articles'),out=join(root,'out');
 await mkdir(join(articles,'papers','2610.12345'),{recursive:true});
 const v={version:'v1',html_path:'papers/2610.12345/v1.html',lead_ja:['問い','方法','結果'],authors:['A'],affiliations:['B'],institution_names:['B'],stored_images:[]};
 const article={id:'2610.12345',title:'A paper',source:'arXiv',date:'2026-10-18',...v,versionHistory:[v]};
 await writeFile(join(articles,'papers.json'),JSON.stringify([article]));await writeFile(join(articles,'images-manifest.json'),'{"schema_version":1,"images":[]}');await writeFile(join(articles,'batch-status.json'),'{}');
 await writeFile(join(articles,v.html_path),String.raw`<!doctype html><html><head></head><body><header>A paper</header><section class="narrative"><p>日本語 \\(x+y\\)</p><p>\\[E=mc^2\\]</p></section></body></html>`);
 try{await fn({root,articles,out,article});}finally{await rm(root,{recursive:true,force:true});}
}
test('canonical articles become matching standalone and lazy body JSON without duplicate header',()=>fixture(async paths=>{
 await stagePublic(paths);await buildReaderArticles(paths);assert.equal((await verifyContent(paths)).articles,1);
 const json=JSON.parse(await readFile(join(paths.out,'reader-articles/2610.12345/v1.json'),'utf8'));
 assert.ok(json.html.includes('x+y'));assert.ok(!json.html.includes('<header>'));assert.deepEqual(json.lead_ja,['問い','方法','結果']);
 assert.ok((await readFile(join(paths.out,paths.article.html_path),'utf8')).includes('/standalone.js'));
}));
test('unsafe narrative cannot enter distributable body',()=>fixture(async paths=>{
 await writeFile(join(paths.articles,paths.article.html_path),'<section class="narrative"><img src="x" onerror="alert(1)"></section>');await assert.rejects(buildReaderArticles(paths));
}));
test('latest/header mismatch fails rather than publish inconsistent version',()=>fixture(async paths=>{
 paths.article.lead_ja=['wrong','wrong','wrong'];await writeFile(join(paths.articles,'papers.json'),JSON.stringify([paths.article]));await assert.rejects(stagePublic(paths),/differs/);
}));
test('public tree rejects detailed run records',()=>fixture(async paths=>{
 await stagePublic(paths);await buildReaderArticles(paths);await mkdir(join(paths.out,'runs'));await assert.rejects(verifyContent(paths),/Unexpected/);
}));
test('retention excludes originals images task paths caches secrets and keeps resume metadata',()=>{
 for(const p of ['data/runs/2026-10-18/x/run/source.pdf','data/runs/x/run/source.html','data/runs/x/run/fulltext.html.txt','data/runs/x/run/classifier_tasks.json','data/runs/x/site/result.json','.cache/arxiv/models/a.pt','.env'])assert.equal(allowedTrackedPath(p),false,p);
 for(const p of ['data/runs/x/ledger.json','data/runs/x/run/papers/a/v1/source.json','data/runs/x/run/papers/a/v1/summary.md','data/articles/papers/a/v1.html'])assert.equal(allowedTrackedPath(p),true,p);
});
test('generic versionless articles do not need arXiv-specific bibliography or leads',()=>fixture(async paths=>{
 const article={id:'blog:hello',title:'Generic article',source:'Blog',date:'2026-10-18',html_path:'papers/2610.12345/v1.html'};
 await writeFile(join(paths.articles,'papers.json'),JSON.stringify([article]));await stagePublic(paths);await buildReaderArticles(paths);
 const body=JSON.parse(await readFile(join(paths.out,'reader-articles/blog%3Ahello/current.json'),'utf8'));
 assert.equal(body.version,'current');assert.equal(body.lead_ja,undefined);assert.deepEqual(body.stored_images,[]);await verifyContent(paths);
}));
test('standalone rejects scripts and event handlers outside the narrative',()=>fixture(async paths=>{
 const path=join(paths.articles,paths.article.html_path),original=await readFile(path,'utf8');
 for(const injection of ['<script>alert(1)</script>','<script type="module" src="https://evil.example/x.js"></script>','<h1 onclick="alert(1)">Title</h1>','<img src="/api/images/'+ 'a'.repeat(64)+'" onerror="alert(1)">','<img src="/api/images/'+ 'a'.repeat(64)+'">']) {
  await writeFile(path,original.replace('<header>',injection+'<header>'));await assert.rejects(stagePublic(paths),/Unsafe|outside/);
 }
}));
test('standalone rejects external styles, CSS URLs, frames, forms and unregistered figures',()=>fixture(async paths=>{
 const path=join(paths.articles,paths.article.html_path),original=await readFile(path,'utf8');
 for(const injection of ['<link rel="stylesheet" href="https://evil.example/a.css">','<style>body{background:url(https://evil.example)}</style>','<div style="background:url(https://evil.example)"></div>','<iframe src="https://evil.example"></iframe>','<form action="https://evil.example"></form>','<object data="https://evil.example"></object>','<embed src="https://evil.example">','<meta http-equiv="refresh" content="0;url=https://evil.example">','<a href="java&#x09;script:alert(1)">link</a>','<a href="https://good.example" ping="https://evil.example">link</a>']) {
  await writeFile(path,original.replace('<header>',injection+'<header>'));await assert.rejects(stagePublic(paths),/Unsafe/);
 }
 await writeFile(path,original.replace('<p>','<img src="https://evil.example/tracker"><p>'));await assert.rejects(stagePublic(paths),/Unregistered/);
}));
test('standalone validates existing canonical script structurally and adds CSS without rewriting source',()=>fixture(async paths=>{
 const path=join(paths.articles,paths.article.html_path),original=await readFile(path,'utf8');
 const source=original.replace('</head>',"<script src='/standalone.js' type='module'></script></head>");
 await writeFile(path,source);await stagePublic(paths);
 const staged=await readFile(join(paths.out,paths.article.html_path),'utf8');
 assert.equal(staged,source.replace('</head>','<link rel="stylesheet" href="/assets/reader.css"></head>'));assert.equal((staged.match(/standalone\.js/g)||[]).length,1);
 await writeFile(path,source.replace("type='module'", "type='module' async"));await assert.rejects(stagePublic(paths),/Unsafe standalone script/);
 await writeFile(path,source.replace('</script>','alert(1)</script>'));await assert.rejects(stagePublic(paths),/Unsafe standalone script/);
}));
test('standalone canonical-looking comments do not suppress required assets',()=>fixture(async paths=>{
 const path=join(paths.articles,paths.article.html_path),original=await readFile(path,'utf8');
 const source=original.replace('</head>','<!-- href="/assets/reader.css" src="/standalone.js" --></head>');await writeFile(path,source);await stagePublic(paths);
 const staged=await readFile(join(paths.out,paths.article.html_path),'utf8');assert.ok(staged.includes('<link rel="stylesheet" href="/assets/reader.css">'));assert.ok(staged.includes('<script type="module" src="/standalone.js"></script>'));
}));

test('canonical content hash binds published metadata to actual standalone source',()=>fixture(async paths=>{
 const path=join(paths.articles,paths.article.html_path),bytes=await readFile(path);const sha=createHash('sha256').update(bytes).digest('hex');
 paths.article.content_sha256=sha;paths.article.versionHistory[0].content_sha256=sha;await writeFile(join(paths.articles,'papers.json'),JSON.stringify([paths.article]));await stagePublic(paths);
 await writeFile(path,bytes.toString().replace('A paper','Changed title'));await assert.rejects(stagePublic(paths),/content hash mismatch/);
}));

test('browser distributions retain exact upstream licenses and reject missing or altered notices',()=>fixture(async paths=>{
 await stagePublic(paths);await buildReaderArticles(paths);
 const path=join(paths.out,'THIRD_PARTY_LICENSES.txt'),notice=await readFile(path,'utf8');
 for(const [name,file] of [['katex','LICENSE'],['dompurify','LICENSE'],['dompurify','LICENSE-MPL']]) {
  const license=await readFile(new URL(`../node_modules/${name}/${file}`,import.meta.url),'utf8');
  assert.ok(notice.includes(license),`${name}/${file} must be retained verbatim`);
 }
 assert.ok(notice.includes('(c) Cure53 and other contributors'));
 assert.doesNotMatch(await readFile(new URL('../reader/index.html',import.meta.url),'utf8'),/href="\/THIRD_PARTY_LICENSES\.txt"/);
 await verifyContent(paths);
 await writeFile(path,'incomplete');await assert.rejects(verifyContent(paths),/license copy mismatch/);
 await rm(path);await assert.rejects(verifyContent(paths),/ENOENT/);
}));

test('unsupported IDs fail before any body output; benign generic names remain supported',()=>fixture(async paths=>{
 for(const id of ['.','..','x/../y','x//y','x\\y','x%2Fy','x\u0000y','\ud800']){
  const article={id,title:'Generic',html_path:paths.article.html_path};await writeFile(join(paths.articles,'papers.json'),JSON.stringify([article]));
  await assert.rejects(buildReaderArticles(paths),/Invalid\/duplicate article id/);
 }
 for(const id of ['a..b','資料/記事','news:123','__proto__','constructor','toString']){
  const article={id,title:'Generic',html_path:paths.article.html_path};await writeFile(join(paths.articles,'papers.json'),JSON.stringify([article]));
  await buildReaderArticles(paths);const body=JSON.parse(await readFile(join(paths.out,'reader-articles',encodeURIComponent(id),'current.json'),'utf8'));assert.equal(body.id,id);
 }
}));

test('verification binds the complete Reader body to canonical source and metadata',()=>fixture(async paths=>{
 await stagePublic(paths);await buildReaderArticles(paths);
 const path=join(paths.out,'reader-articles/2610.12345/v1.json');
 const canonical=JSON.parse(await readFile(path,'utf8'));
 const mutations=[
  body=>{body.html='<p>Injected replacement narrative</p>';},
  body=>{body.title='Changed title';},
  body=>{body.source='Changed source';},
  body=>{body.date='1900-01-01';},
  body=>{body.html_path='papers/other/v1.html';},
  body=>{delete body.authors;},
  body=>{body.unexpected_field='extra';},
 ];
 for(const mutate of mutations) {
  const body=structuredClone(canonical);mutate(body);await writeFile(path,JSON.stringify(body));
  await assert.rejects(verifyContent(paths),/Reader body mismatch/);
 }
 // Object key order and whitespace are representation details, not changes.
 await writeFile(path,JSON.stringify(Object.fromEntries(Object.entries(canonical).reverse())));
 assert.equal((await verifyContent(paths)).verified,true);
}));
