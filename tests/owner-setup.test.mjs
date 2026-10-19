import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {createWorker} from '../worker/index.js';

function fixture() {
  const sqlite = new DatabaseSync(':memory:');
  for (const name of ['0000_reader_state.sql','0001_owner_setup.sql']) {
    sqlite.exec(readFileSync(new URL('../drizzle/'+name,import.meta.url),'utf8'));
  }
  const DB = {prepare(sql) {
    const statement = sqlite.prepare(sql);
    const bound = values => ({
      async first() {return statement.get(...values) || null;},
      async all() {return {success:true,results:statement.all(...values)};},
      async run() {statement.run(...values);return {success:true};},
    });
    return {...bound([]),bind(...values) {return bound(values);}};
  }};
  const env = {DB,TRUSTED_AUTH_HEADER:'hosting',SITES_OWNER_PRIVATE:'confirmed',ASSETS:{fetch:async()=>new Response('Reader assets')}};
  const worker = createWorker({papers:[],manifest:{schema_version:1,images:[]}});
  const request = (path='/',{user='owner-site-id',method='GET',headers={}}={}) => new Request('https://reader.example'+path,{method,headers:{...(user?{'oai-authenticated-user-id':user}:{}),...headers}});
  return {sqlite,env,worker,request};
}

test('Sites login opens Reader directly without writing a separate owner registration',async()=>{
 const f=fixture();try {
  const page=await f.worker.fetch(f.request(),f.env);
  assert.equal(page.status,200);assert.equal(await page.text(),'Reader assets');
  assert.equal(page.headers.get('Cache-Control'),'private, no-store');
  assert.equal(f.sqlite.prepare('SELECT count(*) AS n FROM site_owner_setup').get().n,0);
  assert.deepEqual(await (await f.worker.fetch(f.request('/api/reading-state'),f.env)).json(),{readIds:[]});
  assert.equal(f.env.OWNER_USER_ID,undefined);
 }finally{f.sqlite.close();}
});

test('existing reading history, preferences and historical setup records remain unchanged',async()=>{
 const f=fixture();try {
  f.sqlite.prepare('INSERT INTO site_owner_setup VALUES (1, ?, ?)').run('owner-site-id','original-setup');
  f.sqlite.prepare('INSERT INTO reading_state VALUES (?, ?, ?)').run('owner-site-id','kept-article','original-read');
  f.sqlite.prepare('INSERT INTO article_preferences (user_id,article_id,starred) VALUES (?, ?, 1)').run('owner-site-id','kept-article');
  assert.equal((await f.worker.fetch(f.request(),f.env)).status,200);
  assert.deepEqual(await (await f.worker.fetch(f.request('/api/reading-state'),f.env)).json(),{readIds:['kept-article']});
  assert.deepEqual(await (await f.worker.fetch(f.request('/api/article-preferences'),f.env)).json(),{starredIds:['kept-article'],badIds:[]});
  assert.equal(f.sqlite.prepare('SELECT created_at FROM site_owner_setup').get().created_at,'original-setup');
  assert.equal(f.sqlite.prepare('SELECT read_at FROM reading_state').get().read_at,'original-read');
  // Platform-authenticated identities are also isolated by the record key.
  assert.deepEqual(await (await f.worker.fetch(f.request('/api/reading-state',{user:'another-site-identity'}),f.env)).json(),{readIds:[]});
 }finally{f.sqlite.close();}
});

test('service access has no user identity and cannot access user-owned records; setup endpoints are removed',async()=>{
 const f=fixture();try {
  for(const path of ['/','/api/reading-state','/api/article-preferences']) {
   for(const headers of [{},{'OAI-Sites-Authorization':'Bearer fixture-service'}]) {
    assert.equal((await f.worker.fetch(f.request(path,{user:null,headers}),f.env)).status,401);
   }
  }
  for(const path of ['/api/setup/activate','/api/setup/identity']) {
   for(const method of ['GET','POST']) assert.equal((await f.worker.fetch(f.request(path,{method}),f.env)).status,404);
  }
  assert.equal(f.sqlite.prepare('SELECT count(*) AS n FROM site_owner_setup').get().n,0);
 }finally{f.sqlite.close();}
});

test('direct Sites identity requires the confirmed private ingress; explicit owner pins still apply',async()=>{
 for(const change of [{SITES_OWNER_PRIVATE:undefined},{TRUSTED_AUTH_HEADER:undefined}]) {
  const f=fixture();try {
   Object.assign(f.env,change);
   assert.equal((await f.worker.fetch(f.request(),f.env)).status,503);
   assert.equal(f.sqlite.prepare('SELECT count(*) AS n FROM site_owner_setup').get().n,0);
  }finally{f.sqlite.close();}
 }
 const f=fixture();try {
  f.env.OWNER_USER_ID='configured-owner';
  assert.equal((await f.worker.fetch(f.request(),f.env)).status,403);
  assert.equal((await f.worker.fetch(f.request('/',{user:'configured-owner'}),f.env)).status,200);
 }finally{f.sqlite.close();}
});
