import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
const root = new URL('../', import.meta.url).pathname;
const bundle = async file => (await build({entryPoints:[`${root}reader/${file}`],bundle:true,write:false,format:'iife',globalName:'ReaderModule',footer:{js:'globalThis.ReaderModule = ReaderModule;'},loader:{'.css':'empty'}})).outputFiles[0].text;
const rendererCode = await bundle('article-renderer.js');
const appCode = await bundle('app.js');
const html = await readFile(`${root}reader/index.html`,'utf8');
function windowFor(markup='<article id="article"></article>') { const dom = new JSDOM(markup.startsWith('<!doctype') ? markup : '<!doctype html>' + markup,{url:'https://reader.example/',runScripts:'outside-only',pretendToBeVisual:true}); dom.window.structuredClone = structuredClone; dom.window.scrollTo = ({top=0}) => {dom.window.scrollY=top;}; return dom.window; }
const tick = () => new Promise(resolve=>setTimeout(resolve,35));
test('sanitizer removes executable content and remote figures while keeping semantics',()=>{
 const w=windowFor(); w.eval(rendererCode);
 const fragment=w.ReaderModule.safeHTML('<h2 onclick="alert(1)">Title</h2><script>bad()</script><iframe src="https://evil.test"></iframe><img src="https://evil.test/tracker" onerror="bad()"><img src="/api/images/abc"><img src="https://arxiv.org/html/2610.22855v1/x1.png"><img src="https://arxiv.org.evil.test/html/2610.22855v1/x1.png"><a href="javascript:bad()">bad</a><figure><figcaption>図1 内容</figcaption></figure>');
 const article=w.document.querySelector('article'); article.append(fragment);
 assert.equal(article.querySelector('script,iframe,[onclick],[onerror]'),null); assert.equal(article.querySelector('a').hasAttribute('href'),false); assert.equal(article.querySelectorAll('img')[0].hasAttribute('src'),false); assert.equal(article.querySelectorAll('img')[1].getAttribute('src'),'/api/images/abc'); assert.equal(article.querySelectorAll('img')[2].getAttribute('src'),'https://arxiv.org/html/2610.22855v1/x1.png'); assert.equal(article.querySelectorAll('img')[2].referrerPolicy,'no-referrer'); assert.equal(article.querySelectorAll('img')[3].hasAttribute('src'),false); assert.equal(article.querySelector('figcaption').textContent,'図1 内容'); w.close();
});
test('dynamic render handles inline and display math, version replacement, and caching',async()=>{
 const w=windowFor(); w.eval(rendererCode); let calls=0;
 const renderer=w.ReaderModule.createArticleRenderer({fetcher:async url=>{calls++;const version=url.split('/').pop().replace('.json','');return {ok:true,json:async()=>({id:'a',version,html:`<p>${version} \\(x^2\\)</p><p>\\[\\sum_{i=1}^{n} i\\]</p>`})};}});
 const container=w.document.querySelector('article'); await renderer.render(container,'a','v1'); assert.equal(container.querySelectorAll('.katex').length,2);
 await renderer.render(container,'a','v2'); assert.match(container.textContent,/v2/); assert.equal(container.querySelectorAll('.katex').length,2); await renderer.render(container,'a','v1'); assert.equal(calls,2); w.close();
});
test('late response cannot replace a newer article and failed body is retryable',async()=>{
 const w=windowFor();w.eval(rendererCode);let resolveA;
 const renderer=w.ReaderModule.createArticleRenderer({fetcher:async url=>url.includes('/a/')?new Promise(resolve=>resolveA=resolve):{ok:true,json:async()=>({id:'b',version:'v1',html:'<p>B</p>'})}});
 const container=w.document.querySelector('article'), pending=renderer.render(container,'a','v1');await renderer.render(container,'b','v1');resolveA({ok:true,json:async()=>({id:'a',version:'v1',html:'<p>A</p>'})});await pending;assert.equal(container.textContent,'B');
 const failing=w.ReaderModule.createArticleRenderer({fetcher:async()=>({ok:false,status:503})});await failing.render(container,'a','v1');assert.match(container.textContent,/503/);assert.ok(container.querySelector('button'));w.close();
});
test('image error exposes a lazy retry without delaying text',async()=>{
 const w=windowFor();w.eval(rendererCode);const article=w.document.querySelector('article');article.innerHTML='<p>Ready</p><img src="/api/images/abc">';w.ReaderModule.enhanceImages(article);const image=article.querySelector('img'),button=article.querySelector('button');assert.equal(image.loading,'lazy');assert.equal(button.hidden,true);image.dispatchEvent(new w.Event('error'));assert.equal(button.hidden,false);button.click();await tick();assert.equal(image.getAttribute('src'),'/api/images/abc');w.close();
});
test('app keeps unread snapshot after selection, bulk uses active source, and native find is not intercepted',async()=>{
 const w=windowFor(html), requests=[];
 const papers=[{id:'a',source:'Journal',title:'Original English title A',title_ja:'日本語見出し',date:'2026-01-01',version:'v1',lead_ja:['一','二','三']},{id:'b',source:'Other',title:'B',date:'2026-02-01',version:'v1'}];
 w.fetch=async(url,options)=>{requests.push([url,options]);if(options?.method==='POST')return {ok:true,json:async()=>({ok:true})};let data=url==='/papers.json'?papers:url==='/api/reading-state'?{readIds:[]}:url==='/api/article-preferences'?{starredIds:[],badIds:[]}:{id:'a',version:'v1',title:papers[0].title,html:'<p>Body \\(x\\)</p>'};return {ok:true,json:async()=>data};};
 w.eval(appCode);await tick();assert.equal(w.document.querySelectorAll('.paper-row').length,2);
 [...w.document.querySelectorAll('#feeds button')].find(b=>b.querySelector('.feed-name')?.textContent==='Journal').click();w.document.querySelector('#read-filter').value='unread';w.document.querySelector('#read-filter').dispatchEvent(new w.Event('change'));w.document.querySelector('.paper-open').click();await tick();assert.equal(w.document.querySelectorAll('.paper-row').length,1);assert.ok(w.document.querySelector('#article .katex'));
 w.document.querySelector('#bulk').click();await tick();assert.ok(requests.filter(r=>r[1]?.method==='POST').every(r=>JSON.parse(r[1].body).articleId==='a'));
 const event=new w.KeyboardEvent('keydown',{key:'f',ctrlKey:true,bubbles:true,cancelable:true});w.document.dispatchEvent(event);assert.equal(event.defaultPrevented,false);
 assert.ok(w.document.querySelector('input[type=search]'));assert.equal(w.document.querySelector('.paper-lead'),null);w.close();
});
test('app switches historical bibliography and body together; swipe preview and cancellation do not mutate state',async()=>{
 const w=windowFor(html), writes=[];w.innerWidth=390;
 const paper={id:'a',source:'Research',title:'Newest title',date:'2026-02-01',version:'v2',lead_ja:['new lead'],authors:['New author'],versionHistory:[{version:'v1',title:'Old title',lead_ja:['old lead'],authors:['Old author'],institution_names:['Old institution'],stored_images:[]}]};
 w.fetch=async(url,options)=>{if(options?.method==='POST'){writes.push(JSON.parse(options.body));return {ok:true,json:async()=>({ok:true})};}let data=url==='/papers.json'?[paper]:url==='/api/reading-state'?{readIds:[]}:url==='/api/article-preferences'?{starredIds:[],badIds:[]}:url.endsWith('v1.json')?{...paper.versionHistory[0],id:'a',html:'<p>Old body \\[x=1\\]</p>'}:{...paper,html:'<p>New body</p>'};return {ok:true,json:async()=>data};};
 w.eval(appCode);await tick();w.document.querySelector('.paper-open').click();await tick();
 const version=w.document.querySelector('#version');version.value='v1';version.dispatchEvent(new w.Event('change'));await tick();
 const article=w.document.querySelector('#article');assert.match(article.textContent,/Old title/);assert.match(article.textContent,/Old author/);assert.match(article.textContent,/old lead/);assert.doesNotMatch(article.textContent,/New author|new lead|New body/);assert.equal(article.querySelectorAll('.katex').length,1);
 const count=writes.length, pane=w.document.querySelector('#article-pane');
 const pointer=(name,x,y)=>{const e=new w.Event(name,{bubbles:true});Object.assign(e,{pointerType:'touch',isPrimary:true,clientX:x,clientY:y});pane.dispatchEvent(e);};
 pointer('pointerdown',30,100);pointer('pointermove',190,102);assert.ok(w.document.body.classList.contains('sidebar-preview'));assert.equal(writes.length,count);pointer('pointercancel',190,102);assert.equal(w.document.body.classList.contains('sidebar-preview'),false);assert.equal(w.document.body.classList.contains('sidebar-open'),false);assert.match(article.textContent,/Old title/);assert.equal(writes.length,count);
 w.document.querySelector('#back').click();assert.match(article.textContent,/読む記事/);assert.equal(w.document.querySelector('#original').hidden,true);w.close();
});
test('last update uses publication status in JST and status-fetch failure does not block Reader',async()=>{
 for(const fail of [false,true]){
  const w=windowFor(html);w.fetch=async url=>{if(url==='/api/batch-status'){if(fail)throw Error('offline');return{ok:true,json:async()=>({updated_at:'2026-10-18T12:34:00Z'})};}const data=url==='/papers.json'?[]:url==='/api/reading-state'?{readIds:[]}:{starredIds:[],badIds:[]};return{ok:true,json:async()=>data};};
  w.eval(appCode);await tick();assert.ok(w.document.querySelector('#feeds button'));if(!fail)assert.match(w.document.querySelector('#updated').textContent,/2026\/10\/18 21:34 JST/);else assert.equal(w.document.querySelector('#updated').textContent,'最終更新 —');w.close();
 }
});
test('mobile touch gestures preserve native table/math scrolling and pinch; safe swipes commit only on release',async()=>{
 const w=windowFor(html), writes=[];w.innerWidth=390;
 const papers=[{id:'a',source:'Research',title:'A',date:'2026-02-01',version:'v1'},{id:'b',source:'Research',title:'B',date:'2026-01-01',version:'v1'}];
 w.fetch=async(url,options)=>{if(options?.method==='POST'){writes.push(JSON.parse(options.body));return{ok:true,json:async()=>({ok:true})};}const id=url.includes('/b/')?'b':'a';const data=url==='/papers.json'?papers:url==='/api/reading-state'?{readIds:[]}:url==='/api/article-preferences'?{starredIds:[],badIds:[]}:url==='/api/batch-status'?{}:{id,version:'v1',title:id,html:'<p class="safe">Safe body</p><table><tr><td>Wide table</td></tr></table><p>\\[x=1\\]</p>'};return{ok:true,json:async()=>data};};
 w.eval(appCode);await tick();w.document.querySelector('.paper-open').click();await tick();
 const touch=(target,type,x,y,{count=1}={})=>{const e=new w.Event(type,{bubbles:true,cancelable:true});const t={identifier:1,clientX:x,clientY:y};Object.assign(e,{touches:type==='touchend'||type==='touchcancel'?[]:count===1?[t]:[t,{identifier:2,clientX:x+10,clientY:y}],changedTouches:[t]});target.dispatchEvent(e);return e;};
 for(const target of [w.document.querySelector('td'),w.document.querySelector('.katex-display')]){
  touch(target,'touchstart',300,100);const move=touch(target,'touchmove',100,101);assert.equal(move.defaultPrevented,false);touch(target,'touchend',100,101);assert.equal(w.document.querySelector('.paper-row.selected').dataset.id,'a');
 }
 const safe=w.document.querySelector('.safe'), before=writes.length;
 touch(safe,'touchstart',300,100);assert.equal(touch(safe,'touchmove',100,101).defaultPrevented,true);assert.equal(writes.length,before);touch(safe,'touchcancel',100,101);assert.equal(w.document.querySelector('.paper-row.selected').dataset.id,'a');
 touch(safe,'touchstart',300,100);assert.equal(touch(safe,'touchmove',100,101,{count:2}).defaultPrevented,false);touch(safe,'touchend',100,101);assert.equal(w.document.querySelector('.paper-row.selected').dataset.id,'a');
 touch(safe,'touchstart',300,100);assert.equal(touch(safe,'touchmove',299,200).defaultPrevented,false);touch(safe,'touchcancel',299,200);
 touch(safe,'touchstart',300,100);touch(safe,'touchmove',260,101);assert.equal(w.document.querySelector('.paper-row.selected').dataset.id,'a');touch(safe,'touchend',100,101);await new Promise(r=>setTimeout(r,220));assert.equal(w.document.querySelector('.paper-row.selected').dataset.id,'b');
 const css=await readFile(`${root}reader/reader.css`,'utf8');assert.doesNotMatch(css,/touch-action\s*:\s*(?:none|pan-y)/);w.close();
});

test('list exposes stable IDs and dates for every source using safe text',async()=>{
 const w=windowFor(html);
 const papers=[
  {id:'2610.12345',source:'arXiv',title:'Modern paper',date:'2026-10-19',version:'v2'},
  {id:'hep-th/9901001',source:'arXiv',title:'Legacy paper',date:'1999-01-01',version:'v1'},
  {id:'blog:example/<img src=x onerror=alert(1)>',source:'Blog',title:'Generic article',date:'2026-10-18'}
 ];
 w.fetch=async(url,options)=>({ok:true,json:async()=>options?.method==='POST'?{ok:true}:url==='/papers.json'?papers:url==='/api/reading-state'?{readIds:[]}:url==='/api/article-preferences'?{starredIds:[],badIds:[]}:{}});
 w.eval(appCode);await tick();
 for(const paper of papers){
  const row=[...w.document.querySelectorAll('.paper-row')].find(row=>row.dataset.id===paper.id);
  assert.ok(row);assert.equal(row.querySelector('.paper-id').textContent,paper.id);
  assert.equal(row.querySelector('.paper-version'),null);
  assert.equal(row.querySelector('time').dateTime,paper.date);
  assert.equal(row.querySelector('img,[onerror]'),null);
 }
 const source=[...w.document.querySelectorAll('#feeds button')].find(button=>button.querySelector('.feed-name')?.textContent==='arXiv');source.click();
 assert.equal(w.document.querySelectorAll('.paper-id').length,2);
 assert.deepEqual([...w.document.querySelectorAll('.paper-id')].map(el=>el.textContent).sort(),['2610.12345','hep-th/9901001']);
 w.close();
});

test('generic prototype-like IDs have independent saved state and absent source links stay hidden',async()=>{
 const w=windowFor(html),writes=[];
 const papers=[{id:'__proto__',source:'Blog',title:'Prototype article',version:'v1'},{id:'other',source:'Blog',title:'Other article',version:'v1'}];
 w.fetch=async(url,options)=>{if(options?.method==='POST'){writes.push(JSON.parse(options.body));return {ok:true,json:async()=>({ok:true})};}return {ok:true,json:async()=>url==='/papers.json'?papers:url==='/api/reading-state'?{readIds:['__proto__']}:url==='/api/article-preferences'?{starredIds:['__proto__'],badIds:[]}:url.startsWith('/reader-articles/')?{...papers[0],html:'<p>Generic body</p>'}:{}};};
 w.eval(appCode);await tick();w.document.querySelector('#read-filter').value='all';w.document.querySelector('#read-filter').dispatchEvent(new w.Event('change'));
 const row=[...w.document.querySelectorAll('.paper-row')].find(row=>row.dataset.id==='__proto__');
 assert.equal(row.classList.contains('is-read'),true);
 row.querySelector('.paper-open').click();await tick();
 assert.equal(w.document.querySelector('#star-state').getAttribute('aria-pressed'),'true');
 assert.equal(w.document.querySelector('#original').hidden,true);
 w.document.querySelector('#read-state').click();await tick();
 assert.equal(writes.at(-1).articleId,'__proto__');assert.equal(writes.at(-1).operation,'unread');
 assert.equal(w.Object.prototype.read,undefined);assert.equal(w.Object.prototype.starred,undefined);
 assert.equal([...w.document.querySelectorAll('.paper-row')].find(row=>row.dataset.id==='other').classList.contains('is-read'),false);
 w.close();
});

test('unread default, full-text search, sorting, announcement dates and reset compose without losing saved state',async()=>{
 const w=windowFor(html),writes=[];
 const papers=[{id:'a',source:'arxiv',title:'Zeta',title_ja:'電池材料',summary_ja:'固体の合成',date:'2026-10-19',version:'v2',tags:['synthesis-simulation'],versionHistory:[{id:'a',source:'arxiv',version:'v1',title:'Earlier Zeta',title_ja:'旧版',date:'2026-10-01'}]},{id:'b',source:'arxiv',title:'Alpha',date:'2026-10-18',version:'v1'},{id:'c',source:'Blog',title:'Read paper',date:'2026-10-20',version:'v1'}];
 w.fetch=async(url,options)=>{if(options?.method==='POST'){writes.push(JSON.parse(options.body));return{ok:true,json:async()=>({ok:true})};}return{ok:true,json:async()=>url==='/papers.json'?papers:url==='/api/reading-state'?{readIds:['c']}:url==='/api/article-preferences'?{starredIds:['b'],badIds:[]}:{}};};
 w.eval(appCode);await tick();const doc=w.document;
 assert.equal(doc.querySelector('#read-filter').value,'unread');assert.equal(doc.querySelectorAll('.paper-row').length,2);assert.equal(doc.querySelector('#unread-count').textContent,'未読 2');
 const search=doc.querySelector('#search');search.value='合成';search.dispatchEvent(new w.Event('input'));assert.deepEqual([...doc.querySelectorAll('.paper-id')].map(el=>el.textContent),['a']);assert.equal(doc.querySelector('.interest-tags').textContent,'合成sim');
 search.value='';search.dispatchEvent(new w.Event('input'));const sort=doc.querySelector('#sort');sort.value='title';sort.dispatchEvent(new w.Event('change'));assert.equal(doc.querySelector('.paper-id').textContent,'b');
 const date=doc.querySelector('#batch-date');date.value='2026-10-01';date.dispatchEvent(new w.Event('change'));assert.equal(doc.querySelector('.paper-open h2').textContent,'Earlier Zeta');
 doc.querySelector('#bulk').click();await tick();assert.deepEqual(writes.map(item=>item.articleId),['a']);
 doc.querySelector('#reset').click();assert.equal(search.value,'');assert.equal(sort.value,'date');assert.equal(date.value,'all');assert.equal(doc.querySelector('#read-filter').value,'unread');assert.equal(doc.querySelector('.paper-id').textContent,'b');
 const starFeed=[...doc.querySelectorAll('#feeds button')].find(b=>b.querySelector('.feed-name').textContent==='スター');assert.equal(starFeed.querySelector('.count').textContent,'1');starFeed.click();assert.equal(doc.querySelector('#read-filter').value,'all');assert.equal(doc.querySelector('.paper-id').textContent,'b');w.close();
});

test('article bibliography, source link, topic labels and feedback controls remain attached to the selected version',async()=>{
 const w=windowFor(html),writes=[];const paper={id:'a',source:'arxiv',title:'English title',title_ja:'和題',date:'2026-10-19',version:'v1',authors:['Author'],institution_names:['Institution'],tags:['mlip-extension'],lead_ja:['要約1','要約2','要約3'],source_url:'https://arxiv.org/abs/2610.12345v1'};
 w.fetch=async(url,options)=>{if(options?.method==='POST'){writes.push(JSON.parse(options.body));return{ok:true,json:async()=>({ok:true})};}return{ok:true,json:async()=>url==='/papers.json'?[paper]:url==='/api/reading-state'?{readIds:[]}:url==='/api/article-preferences'?{starredIds:[],badIds:[]}:url.startsWith('/reader-articles/')?{...paper,html:'<h2>結果</h2><p>本文</p><figure><figcaption>図1</figcaption></figure>'}:{}};};
 w.eval(appCode);await tick();const doc=w.document,row=doc.querySelector('.paper-row');assert.deepEqual([...row.querySelectorAll('.row-controls button')].map(b=>b.dataset.field),['starred','bad','read']);assert.ok(row.querySelector('[data-field=bad] svg'));assert.ok(row.querySelector('[data-field=read] .read-dot'));
 row.querySelector('.paper-open').click();await tick();const article=doc.querySelector('#article');assert.equal(article.querySelector('h1 a').href,paper.source_url);assert.equal(article.querySelector('.japanese-title').textContent,'和題');assert.equal(article.querySelector('.authors').textContent,'Author');assert.equal(article.querySelector('.institutions').textContent,'Institution');assert.equal(article.querySelector('.interest-tags').textContent,'MLIP拡張');assert.equal(article.querySelectorAll('.lead p').length,3);
 article.querySelector('.inline-actions [data-field=bad]').click();await tick();assert.equal(writes.at(-1).operation,'bad');assert.equal(doc.querySelector('#bad-state').getAttribute('aria-pressed'),'true');assert.equal([...doc.querySelectorAll('#feeds button')].find(b=>b.querySelector('.feed-name').textContent==='bad').querySelector('.count').textContent,'1');w.close();
});

test('row swipe commits only after release, cancellation and native edge leave reading state unchanged',async()=>{
 const w=windowFor(html),writes=[];w.innerWidth=390;
 w.fetch=async(url,options)=>{if(options?.method==='POST'){writes.push(JSON.parse(options.body));return{ok:true,json:async()=>({ok:true})};}return{ok:true,json:async()=>url==='/papers.json'?[{id:'a',source:'arxiv',title:'A',version:'v1',date:'2026-10-19'}]:url==='/api/reading-state'?{readIds:[]}:url==='/api/article-preferences'?{starredIds:[],badIds:[]}:{}};};
 w.eval(appCode);await tick();const doc=w.document;
 const touch=(target,type,x,y=100)=>{const e=new w.Event(type,{bubbles:true,cancelable:true}),t={identifier:1,clientX:x,clientY:y};Object.assign(e,{touches:['touchend','touchcancel'].includes(type)?[]:[t],changedTouches:[t]});target.dispatchEvent(e);return e;};
 let row=doc.querySelector('.paper-open');touch(row,'touchstart',100);touch(row,'touchmove',210);assert.equal(writes.length,0);touch(row,'touchcancel',210);assert.equal(writes.length,0);
 touch(row,'touchstart',10);assert.equal(touch(row,'touchmove',200).defaultPrevented,false);touch(row,'touchend',200);assert.equal(writes.length,0);
 touch(row,'touchstart',100);touch(row,'touchmove',210);touch(row,'touchend',210);await tick();assert.equal(writes.length,1);assert.equal(writes[0].operation,'read');assert.equal(doc.querySelector('.paper-open').getAttribute('aria-current'),'false');assert.ok(doc.querySelector('.paper-row.is-read'));w.close();
});

test('mobile native scroll offsets are restored after sidebar and list navigation',async()=>{
 const w=windowFor(html);w.innerWidth=390;
 w.fetch=async(url,options)=>({ok:true,json:async()=>url==='/papers.json'?[{id:'a',title:'A',version:'v1'}]:url==='/api/reading-state'?{readIds:[]}:url==='/api/article-preferences'?{starredIds:[],badIds:[]}:url.startsWith('/reader-articles/')?{id:'a',version:'v1',title:'A',html:'<p>Body</p>'}:{}});
 w.eval(appCode);await tick();w.scrollY=320;w.document.querySelector('.paper-open').click();await tick();assert.equal(w.scrollY,0);w.scrollY=480;w.document.querySelector('#menu').click();assert.equal(w.scrollY,0);w.document.querySelector('#close-feeds').click();assert.equal(w.scrollY,480);w.document.querySelector('#back').click();assert.equal(w.scrollY,320);w.close();
});

test('desktop hides mobile navigation and constrains each scrolling pane',async()=>{
 const w=windowFor(html),style=w.document.createElement('style');style.textContent=await readFile(`${root}reader/reader.css`,'utf8');w.document.head.append(style);
 for(const selector of ['#menu','#close-feeds','.article-toolbar','#article-actions'])assert.equal(w.getComputedStyle(w.document.querySelector(selector)).display,'none',selector);
 for(const selector of ['#list','#article-scroll'])assert.equal(w.getComputedStyle(w.document.querySelector(selector)).overflow,'auto',selector);
 assert.equal(w.getComputedStyle(w.document.querySelector('#article-pane')).minHeight,'0');assert.equal(w.getComputedStyle(w.document.body).overflow,'hidden');w.close();
});
