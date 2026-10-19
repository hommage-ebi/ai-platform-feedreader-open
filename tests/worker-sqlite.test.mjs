import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {createWorker} from '../worker/index.js';

// Execute the unchanged production SQL through real SQLite. This adapter only
// maps SQLite results to D1's interface; it does not simulate query semantics.
function sqliteD1(sqlite) {
  return {prepare(sql) {
    const statement=sqlite.prepare(sql);
    return {bind(...parameters) {return {
      async all() {return {success:true,results:statement.all(...parameters)};},
      async run() {return {success:true,meta:statement.run(...parameters)};},
    };}};
  }};
}
const migration=readFileSync(new URL('../drizzle/0000_reader_state.sql',import.meta.url),'utf8');
const worker=createWorker({papers:[{id:'stable-article'}],manifest:{schema_version:1,images:[]}});
function setup({migrate=true}={}) {
  const sqlite=new DatabaseSync(':memory:');
  if(migrate) sqlite.exec(migration);
  return {sqlite,env:{OWNER_USER_ID:'owner',TRUSTED_AUTH_HEADER:'hosting',DB:sqliteD1(sqlite)}};
}
function request(path,operation) {
  return new Request(`https://reader.example/api/${path}`,{
    method:operation ? 'POST' : 'GET',
    headers:{'oai-authenticated-user-id':'owner',...(operation ? {Origin:'https://reader.example','Content-Type':'application/json'} : {})},
    ...(operation ? {body:JSON.stringify({articleId:'stable-article',operation})} : {}),
  });
}
async function mutate(env,endpoint,operation) {
  const response=await worker.fetch(request(endpoint,operation),env);
  assert.equal(response.status,200,await response.clone().text());
  assert.deepEqual(await response.json(),{ok:true});
}

test('actual SQLite: read UPSERT changes timestamp without duplicating and unread deletes only owner row',async()=>{
  const {sqlite,env}=setup();
  try {
    sqlite.prepare('INSERT INTO reading_state VALUES (?, ?, ?)').run('another-user','stable-article','2000-01-01T00:00:00.000Z');
    await mutate(env,'reading-state','read');
    const row=sqlite.prepare('SELECT * FROM reading_state WHERE user_id=?').get('owner');
    assert.equal(row.article_id,'stable-article');
    assert.equal(new Date(row.read_at).toISOString(),row.read_at);
    sqlite.prepare('UPDATE reading_state SET read_at=? WHERE user_id=?').run('2000-01-01T00:00:00.000Z','owner');
    await mutate(env,'reading-state','read');
    assert.notEqual(sqlite.prepare('SELECT read_at FROM reading_state WHERE user_id=?').get('owner').read_at,'2000-01-01T00:00:00.000Z');
    assert.equal(sqlite.prepare('SELECT count(*) n FROM reading_state').get().n,2);
    assert.deepEqual(await (await worker.fetch(request('reading-state'),env)).json(),{readIds:['stable-article']});
    await mutate(env,'reading-state','unread');
    assert.deepEqual(await (await worker.fetch(request('reading-state'),env)).json(),{readIds:[]});
    assert.equal(sqlite.prepare('SELECT count(*) n FROM reading_state WHERE user_id=?').get('another-user').n,1);
  } finally {sqlite.close();}
});

test('actual SQLite: independent flag UPSERTs preserve opposite flag timestamps and clear bad provenance',async()=>{
  const {sqlite,env}=setup();
  const get=()=>sqlite.prepare('SELECT * FROM article_preferences WHERE user_id=? AND article_id=?').get('owner','stable-article');
  try {
    sqlite.prepare('INSERT INTO article_preferences(user_id,article_id,starred,bad) VALUES(?,?,1,1)').run('another-user','other-article');
    await mutate(env,'article-preferences','star');
    const first=get(); assert.equal(first.starred,1); assert.equal(first.bad,0); assert.equal(first.bad_updated_at,null);
    await mutate(env,'article-preferences','bad');
    const second=get(); assert.equal(second.starred,1); assert.equal(second.bad,1); assert.equal(second.starred_updated_at,first.starred_updated_at);
    assert.equal(second.bad_source,'reader:bad');
    sqlite.prepare('UPDATE article_preferences SET bad_reason=?,bad_source=?,bad_source_message_id=? WHERE user_id=?').run('prior rationale','prior-source','message-id','owner');
    await mutate(env,'article-preferences','unstar');
    const third=get(); assert.equal(third.starred,0); assert.equal(third.bad,1); assert.equal(third.bad_updated_at,second.bad_updated_at);
    assert.equal(third.bad_reason,'prior rationale'); assert.equal(third.bad_source_message_id,'message-id');
    await mutate(env,'article-preferences','unbad');
    const fourth=get(); assert.equal(fourth.starred,0); assert.equal(fourth.bad,0); assert.equal(fourth.starred_updated_at,third.starred_updated_at);
    assert.equal(fourth.bad_reason,null); assert.equal(fourth.bad_source_message_id,null); assert.equal(fourth.bad_source,'reader:unbad');
    assert.equal(new Date(fourth.bad_updated_at).toISOString(),fourth.bad_updated_at);
    assert.equal(sqlite.prepare('SELECT count(*) n FROM article_preferences WHERE user_id=?').get('owner').n,1);
    assert.deepEqual(await (await worker.fetch(request('article-preferences'),env)).json(),{starredIds:[],badIds:[]});
    const other=sqlite.prepare('SELECT starred,bad FROM article_preferences WHERE user_id=?').get('another-user');
    assert.equal(other.starred,1); assert.equal(other.bad,1);
    await mutate(env,'article-preferences','star');
    await mutate(env,'article-preferences','bad');
    assert.deepEqual(await (await worker.fetch(request('article-preferences'),env)).json(),{starredIds:['stable-article'],badIds:['stable-article']});
  } finally {sqlite.close();}
});

test('actual SQLite: missing schema and storage constraint failures become 503 without partial success',async()=>{
  const {sqlite,env}=setup({migrate:false});
  try {
    for(const [endpoint,operation] of [['reading-state',undefined],['reading-state','read'],['article-preferences',undefined],['article-preferences','star']]) {
      const response=await worker.fetch(request(endpoint,operation),env);
      assert.equal(response.status,503); assert.deepEqual(await response.json(),{error:'Service unavailable'});
    }
    sqlite.exec(migration);
    sqlite.exec("CREATE TRIGGER reject_reads BEFORE INSERT ON reading_state BEGIN SELECT RAISE(ABORT, 'storage denied'); END;");
    assert.equal((await worker.fetch(request('reading-state','read'),env)).status,503);
    assert.equal(sqlite.prepare('SELECT count(*) n FROM reading_state').get().n,0);
  } finally {sqlite.close();}
});

test('actual SQLite: prototype-like generic IDs round-trip without crossing owner boundary',async()=>{
 const {sqlite,env}=setup();const ids=['__proto__','constructor','toString'];
 const generic=createWorker({papers:ids.map(id=>({id})),manifest:{schema_version:1,images:[]}});
 try{
  for(const id of ids){const body={articleId:id,operation:'read'};const req=()=>new Request('https://reader.example/api/reading-state',{method:'POST',headers:{Origin:'https://reader.example','Content-Type':'application/json','oai-authenticated-user-id':'owner'},body:JSON.stringify(body)});assert.equal((await generic.fetch(req(),env)).status,200);}
  const response=await generic.fetch(request('reading-state'),env);assert.deepEqual((await response.json()).readIds.sort(),ids.sort());
  const outsider=new Request('https://reader.example/api/reading-state',{headers:{'oai-authenticated-user-id':'other'}});assert.equal((await generic.fetch(outsider,env)).status,403);
  assert.equal(sqlite.prepare('SELECT count(*) n FROM reading_state WHERE user_id=?').get('owner').n,3);
  assert.equal(Object.prototype.read,undefined);
 }finally{sqlite.close();}
});
