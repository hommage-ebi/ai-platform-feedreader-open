import {mkdir, rm, writeFile, readFile, access} from 'node:fs/promises';
import {resolve} from 'node:path';
import {randomUUID} from 'node:crypto';

/** Cross-language lock shared with reader_adapter.py. Never steal a stale lock:
 * an operator must establish that its process stopped before removing it. */
export async function withCollectionLock(articles, callback) {
  const root = resolve(articles), lock = resolve(root, '.collection.lock');
  await mkdir(root, {recursive:true});
  try { await mkdir(lock); }
  catch (error) { if (error.code === 'EEXIST') throw new Error('Article collection is locked; wait for its owner or verify crash recovery'); throw error; }
  const token = randomUUID();
  try {
    await writeFile(resolve(lock, 'owner.json'), JSON.stringify({token,pid:process.pid,started_at:new Date().toISOString(),operation:'javascript'})+'\n');
    try{await access(resolve(root,'.collection-transaction.json'));throw new Error('Interrupted collection transaction; recover with reader_adapter before building or publishing');}catch(error){if(error.code!=='ENOENT')throw error;}
    return await callback();
  } finally {
    // A manually replaced lock belongs to its new owner, never to this call.
    let owner; try { owner = JSON.parse(await readFile(resolve(lock, 'owner.json'),'utf8')); } catch {}
    if (owner?.token === token) await rm(lock, {recursive:true,force:true});
  }
}
