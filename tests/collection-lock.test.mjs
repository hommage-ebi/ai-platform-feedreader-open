import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm,access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {withCollectionLock} from '../scripts/collection-lock.mjs';
const project=new URL('../',import.meta.url).pathname;
const helper=new URL('../scripts/collection-lock.mjs',import.meta.url).href;
async function fixture(fn){const root=await mkdtemp(join(tmpdir(),'collection-lock-'));try{const articles=join(root,'data/articles');await mkdir(articles,{recursive:true});await fn(root,articles);}finally{await rm(root,{recursive:true,force:true});}}
function python(root,articles,body){return spawnSync(join(project,'pipeline/.venv/bin/python'),['-c',`from pathlib import Path\nfrom pipeline.shared.collection import collection_lock\nroot=Path(${JSON.stringify(root)})\narticles=Path(${JSON.stringify(articles)})\n${body}`],{encoding:'utf8',env:{...process.env,PROJECT_ROOT:root,PYTHONPATH:project}});}
test('JS and Python collection writers mutually exclude one another without deleting the owner lock',()=>fixture(async(root,articles)=>{
 await withCollectionLock(articles,async()=>{
  await assert.rejects(withCollectionLock(articles,async()=>assert.fail('entered')),/locked/);
  const result=python(root,articles,'with collection_lock(articles,root):\n print("entered")');assert.notEqual(result.status,0);assert.match(result.stderr,/collection is locked/);
  assert.equal(JSON.parse(await readFile(join(articles,'.collection.lock/owner.json'))).pid,process.pid);
 });
 const script=`import {withCollectionLock} from ${JSON.stringify(helper)}; await withCollectionLock(${JSON.stringify(articles)},async()=>{});`;
 const result=python(root,articles,`import subprocess\nwith collection_lock(articles,root):\n r=subprocess.run(['node','--input-type=module','-e',${JSON.stringify(script)}],capture_output=True,text=True)\n assert r.returncode != 0 and 'collection is locked' in r.stderr\n assert (articles/'.collection.lock/owner.json').exists()\nprint('verified')`);
 assert.equal(result.status,0,result.stderr);assert.match(result.stdout,/verified/);await assert.rejects(access(join(articles,'.collection.lock')));
}));
test('lock releases after failure, while an unresolved publication prevents Python mutation',()=>fixture(async(root,articles)=>{
 await assert.rejects(withCollectionLock(articles,async()=>{throw new Error('operation failed');}),/operation failed/);
 await withCollectionLock(articles,async()=>{});
 await mkdir(join(root,'.sites-runtime/publish.lock'),{recursive:true});
 const result=python(root,articles,'with collection_lock(articles,root):\n print("entered")');assert.notEqual(result.status,0);assert.match(result.stderr,/Publication is pending/);assert.doesNotMatch(result.stdout,/entered/);await assert.rejects(access(join(articles,'.collection.lock')));
}));

test('cleanup never removes a lock whose ownership token was replaced',()=>fixture(async(root,articles)=>{
 const marker=join(articles,'.collection.lock/owner.json');
 await withCollectionLock(articles,async()=>{await writeFile(marker,JSON.stringify({token:'replacement',pid:99999}));});
 assert.equal(JSON.parse(await readFile(marker)).token,'replacement');
 await rm(join(articles,'.collection.lock'),{recursive:true});
 const result=python(root,articles,"from pipeline.shared.runtime import write\nwith collection_lock(articles,root):\n write(articles/'.collection.lock/owner.json',{'token':'replacement','pid':99999})\nassert (articles/'.collection.lock/owner.json').exists()");
 assert.equal(result.status,0,result.stderr);assert.equal(JSON.parse(await readFile(marker)).token,'replacement');
}));
