import test from 'node:test';
import assert from 'node:assert/strict';
import { collection, shortcut, gestureResult, createStateStore, versionData, formatUpdated } from '../reader/reader-core.js';
const papers = [{id:'a',source:'Science',date:'2026-01-01'}, {id:'b',source:'arXiv',date:'2026-02-01'}, {id:'c',source:'Science',date:'2026-03-01'}];
test('newest first, generic sources, and only all/unread filter over special collections', () => {
 const states = {a:{read:true,starred:true},b:{starred:true,bad:true},c:{bad:true}};
 assert.deepEqual(collection(papers,states).map(p=>p.id), ['c','b','a']);
 assert.deepEqual(collection(papers,states,'source:Science','unread').map(p=>p.id), ['c']);
 assert.deepEqual(collection(papers,states,'starred','unread').map(p=>p.id), ['b']);
 assert.deepEqual(collection(papers,states,'bad').map(p=>p.id), ['c','b']);
});
test('shortcut map, composing/input guard, browser find remains untouched', () => {
 for(const [key,expected] of Object.entries({j:'next',k:'previous',b:'original',s:'starred',g:'bad'})) assert.equal(shortcut({key}),expected);
 assert.equal(shortcut({key:'J',shiftKey:true}),'nextFeed'); assert.equal(shortcut({key:'K',shiftKey:true}),'previousFeed');
 for(const e of [{key:'f',ctrlKey:true},{key:'f',metaKey:true},{key:'j',isComposing:true},{key:'j',target:{closest:()=>true}}]) assert.equal(shortcut(e),null);
});
test('cancelled, vertical, blocked and incomplete swipes never commit', () => {
 const base={startX:200,startY:10,x:70,y:15,width:400};
 assert.equal(gestureResult(base),'next'); assert.equal(gestureResult({...base,cancelled:true}),null); assert.equal(gestureResult({...base,blocked:true}),null); assert.equal(gestureResult({...base,y:200}),null); assert.equal(gestureResult({...base,x:180}),null); assert.equal(gestureResult({...base,startX:5,x:150}),'sidebar');
});
test('old versions never inherit new leads, authors or figures', () => {
 const paper={version:'v2',lead_ja:['new'],authors:['new author'],stored_images:['new'],versionHistory:[{version:'v1',lead_ja:['old'],authors:['old author'],stored_images:[]}]};
 assert.deepEqual(versionData(paper,'v1'),paper.versionHistory[0]); assert.equal(versionData(paper,'v0').lead_ja,undefined);
});
test('optimistic changes rollback on error with retry', async () => {
 let fail=true, failure; const store=createStateStore({},async()=>{if(fail)throw Error('offline');},()=>{},f=>failure=f);
 const task=store.set('a','read',true); assert.equal(store.states.a.read,true); assert.equal(await task,false); assert.equal(store.states.a.read,false); fail=false; await failure.retry(); assert.equal(store.states.a.read,true);
});
test('same field serializes requests and stale failure cannot rollback newest intent', async () => {
 const releases=[], calls=[]; const store=createStateStore({},(id,field,value)=>new Promise((resolve,reject)=>{calls.push(value);releases.push({resolve,reject});}));
 const a=store.set('a','starred',true); await Promise.resolve(); await Promise.resolve();
 const b=store.set('a','starred',false); const c=store.set('a','starred',true);
 releases[0].reject(Error('first failed')); await a; await Promise.resolve(); assert.equal(store.states.a.starred,true);
 releases[1].resolve(); await b; await Promise.resolve(); releases[2].resolve(); await c;
 assert.deepEqual(calls,[true,false,true]); assert.equal(store.states.a.starred,true);
});
test('independent fields persist independently and bulk targets immutable visible collection',async()=>{
 const calls=[], store=createStateStore({},async(id,field,value)=>{calls.push({id,field,value});});
 const visible=collection(papers,store.states,'source:Science','unread'); await store.set('a','read',true);
 assert.deepEqual(visible.map(p=>p.id),['c','a']); await store.bulkRead(visible.map(p=>p.id)); await store.set('b','bad',true); await store.set('b','starred',true);
 assert.equal(store.states.b.read,undefined); assert.equal(store.states.b.bad,true); assert.equal(store.states.b.starred,true); assert.ok(calls.some(c=>c.id==='c'&&c.field==='read'));
});
test('updates use JST',()=>assert.match(formatUpdated('2026-01-01T00:00:00Z'),/09:00 JST$/));

test('generic IDs cannot alias object prototypes or contaminate other article state',async()=>{
 const initial=JSON.parse('{"__proto__":{"read":false},"constructor":{"starred":false}}');
 const store=createStateStore(initial,async()=>{});
 assert.equal(Object.getPrototypeOf(store.states),null);
 await store.set('__proto__','read',true);await store.set('constructor','starred',true);await store.set('toString','bad',true);
 assert.equal(store.states.__proto__.read,true);assert.equal(store.states.constructor.starred,true);assert.equal(store.states.toString.bad,true);assert.equal(JSON.parse(JSON.stringify(store.states)).__proto__.read,true);
 assert.equal(Object.prototype.read,undefined);assert.equal(Object.prototype.starred,undefined);
 assert.deepEqual(collection([{id:'__proto__'},{id:'other'}],store.states,'all','unread').map(p=>p.id),['other']);
 const blank=createStateStore({},async()=>{});await blank.set('__proto__','bad',true);
 assert.equal(blank.states.__proto__.bad,true);assert.equal(Object.prototype.bad,undefined);
});
