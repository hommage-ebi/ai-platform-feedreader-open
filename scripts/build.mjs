import { mkdir, rm, writeFile, rename, access, cp } from 'node:fs/promises';
import { createBuilder } from 'vite';
import { sitesEnvironment } from './sites-env.mjs';
import { stagePublic } from './stage-public.mjs';
import { buildReaderArticles } from './build-reader-articles.mjs';
import { withCollectionLock } from './collection-lock.mjs';
import { writeBuildSnapshot } from './release-snapshot.mjs';
import { verifyContent } from './verify-content.mjs';
const env = await sitesEnvironment();
try { await access(`${env.runtime}/publish.lock`); throw new Error('A publication is pending; reconcile it before building the same target'); } catch(error) { if(error.code !== 'ENOENT') throw error; }
const lock = `${env.runtime}/build.lock`;
try { await mkdir(lock); } catch (error) { if (error.code === 'EEXIST') throw new Error('Another build/release owns the lock; do not overwrite it'); throw error; }
try {
  await writeFile(`${lock}/owner.json`, JSON.stringify({pid:process.pid,started_at:new Date().toISOString()}));
  await withCollectionLock('data/articles', async () => {
  await rm('dist', {recursive:true,force:true});
  await stagePublic();
  await buildReaderArticles();
  const builder = await createBuilder();
  await builder.buildApp();
  // Native Sites packaging loads dist/server/index.js and its Wrangler config.
  // Retain dist/worker for existing integration tools and publish the same build.
  await cp('dist/worker','dist/server',{recursive:true});
  // Keep the origin root dynamic so Sites' static-first asset lookup cannot
  // bypass the initial owner setup screen.
  await rename('dist/client/reader/index.html','dist/client/reader-shell.html');
  await rm('dist/client/reader',{recursive:true,force:true});
  await verifyContent();
  await writeBuildSnapshot();
  });
} finally { await rm(lock, {recursive:true,force:true}); }
