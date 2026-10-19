import {validArticleId,htmlPaths,bodyPaths} from './content-identity.js';
import {validateImageManifest} from './image-contract.js';
export {validateImageManifest} from './image-contract.js';
import {deploymentStatusRequest, publishedBatch} from './release-status.js';
import { sitesIdentity, ownerAuthorization } from './auth.js';
import { privateSitesBoundary, storageRequest } from './storage.js';

const MAX_BODY = 4096;
const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers:{'Content-Type':'application/json; charset=utf-8'}});
function protect(response) {
  const headers = new Headers(response.headers);
  headers.set('Cache-Control', 'private, no-store');
  headers.set('Vary', 'Cookie, Authorization, oai-authenticated-user-id');
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.delete('Set-Cookie');
  return new Response(response.body, {status:response.status, headers});
}
export function webpDimensions(bytes) {
  const b = new Uint8Array(bytes), ascii = (a,n) => String.fromCharCode(...b.slice(a,a+n));
  if (b.length < 25 || ascii(0,4) !== 'RIFF' || ascii(8,4) !== 'WEBP') throw new Error('Invalid WebP');
  const view = new DataView(b.buffer,b.byteOffset,b.byteLength);
  if (view.getUint32(4,true) + 8 !== b.length) throw new Error('Truncated WebP');
  let dimensions;
  let lossless = false;
  for (let offset = 12; offset + 8 <= b.length;) {
    const kind = ascii(offset,4), size = view.getUint32(offset+4,true), start = offset+8;
    if (start+size > b.length) throw new Error('Invalid WebP chunk');
    if (kind === 'VP8 ') throw new Error('Lossy WebP is forbidden');
    if (kind === 'ANIM' || kind === 'ANMF') throw new Error('Animated WebP is forbidden');
    if (kind === 'VP8L') {
      if (size < 5 || b[start] !== 0x2f) throw new Error('Invalid lossless WebP');
      const bits = view.getUint32(start+1,true);
      dimensions = {width:(bits & 0x3fff)+1, height:((bits >>> 14)&0x3fff)+1};
      lossless = true;
    }
    offset = start + size + (size % 2);
  }
  if (!lossless || !dimensions) throw new Error('Missing lossless WebP');
  return dimensions;
}
export async function verifyImageBytes(bytes, image, mime) {
  if (mime !== image.mime_type || bytes.byteLength !== image.byte_size) throw new Error('Image metadata mismatch');
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)), b => b.toString(16).padStart(2,'0')).join('');
  if (digest !== image.sha256) throw new Error('Image digest mismatch');
  const dimensions = webpDimensions(bytes);
  if (dimensions.width !== image.width || dimensions.height !== image.height) throw new Error('Image dimension mismatch');
}
async function readJson(request) {
  if (request.headers.get('Origin') !== new URL(request.url).origin) return {error:json({error:'Same-origin request required'},403)};
  if (!/^application\/json(?:\s*;.*)?$/i.test(request.headers.get('Content-Type') || '')) return {error:json({error:'JSON required'},415)};
  if (Number(request.headers.get('Content-Length')) > MAX_BODY) return {error:json({error:'Request too large'},413)};
  const reader = request.body?.getReader(); let length=0, parts=[];
  if (reader) while (true) {
    const {done,value} = await reader.read(); if (done) break;
    length += value.byteLength;
    if (length > MAX_BODY) { await reader.cancel(); return {error:json({error:'Request too large'},413)}; }
    parts.push(value);
  }
  const all = new Uint8Array(length); let offset=0;
  for (const part of parts) { all.set(part,offset); offset+=part.byteLength; }
  try { const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(all)); if (!value || Array.isArray(value) || typeof value !== 'object') throw Error(); return {value}; }
  catch { return {error:json({error:'Invalid JSON'},400)}; }
}
async function contentFromAssets(request, env) {
  if (env.CONTENT) return env.CONTENT;
  if (!env.ASSETS) throw new Error('Missing asset binding');
  const origin = new URL(request.url).origin;
  const responses = await Promise.all(['/papers.json','/images-manifest.json'].map(path => env.ASSETS.fetch(new Request(origin+path))));
  if (responses.some(r=>!r.ok)) throw new Error('Missing content');
  return {papers:await responses[0].json(),manifest:await responses[1].json()};
}
export function createWorker(content, {authenticate = sitesIdentity} = {}) {
  return {async fetch(request,env) {
    try {
      const pathBeforeAuth = new URL(request.url).pathname;
      if (pathBeforeAuth === '/api/deployment-status') return protect(await deploymentStatusRequest(request,env));
      if (pathBeforeAuth === '/api/storage') return protect(await storageRequest(request,env));
      if (pathBeforeAuth === '/api/setup/identity') {
        if (!privateSitesBoundary(env) || env.OWNER_USER_ID || env.OWNER_SETUP_ENABLED !== 'true') return protect(json({error:'Setup unavailable'},503));
        if (request.method !== 'GET') return protect(json({error:'Method not allowed'},405));
        const verified = await authenticate(request,env);
        return protect(verified.error ? json({error:verified.error},verified.status) : json({user_id:verified.userId}));
      }
      const identity = ownerAuthorization(await authenticate(request, env), env);
      if (identity.error) return protect(json({error:identity.error},identity.status));
      const user = identity.userId;
      const url = new URL(request.url), path = url.pathname;
      if (request.method !== 'GET' && request.method !== 'POST' && request.method !== 'HEAD') return protect(json({error:'Method not allowed'},405));
      const data = content || await contentFromAssets(request,env);
      if (!Array.isArray(data.papers) || data.papers.some(p => !validArticleId(p.id))) throw new Error('Invalid papers');
      const articleIds = new Set(data.papers.map(p=>p.id));
      if (articleIds.size !== data.papers.length) throw new Error('Duplicate article keys');
      const images = validateImageManifest(data.manifest,articleIds);
      const assetPaths = new Map();
      for (const article of data.papers) {
        const versions = [article, ...(Array.isArray(article.versionHistory) ? article.versionHistory : [])];
        for (const version of versions) {
          const body = bodyPaths(article.id, version.version || 'current');
          assetPaths.set(body.publicPath, body.assetPath);
          if (version.html_path) { const html = htmlPaths(version.html_path); assetPaths.set(html.publicPath, html.assetPath); }
        }
      }
      const knownPath = assetPaths.has(path);
      if (/%(?:2e|5c|25)|\\/i.test(path) || (!knownPath && (path.includes('..') || /%2f/i.test(path)))) return protect(json({error:'Invalid path'},400));
      let response;
      if (path === '/batch-status.json' && ['GET','HEAD'].includes(request.method)) {
        response = await publishedBatch(request,env);
      } else if (path === '/api/reading-state' || path === '/api/article-preferences') {
        if (!env.DB) throw new Error('Missing DB');
        const reading = path === '/api/reading-state';
        if (request.method === 'GET') {
          const result = await env.DB.prepare(reading ? 'SELECT article_id FROM reading_state WHERE user_id = ?' : 'SELECT article_id, starred, bad FROM article_preferences WHERE user_id = ?').bind(user).all();
          if (result.success === false || !Array.isArray(result.results)) throw new Error('DB read failed');
          response = reading ? json({readIds:result.results.map(r=>r.article_id)}) : json({starredIds:result.results.filter(r=>r.starred===1).map(r=>r.article_id),badIds:result.results.filter(r=>r.bad===1).map(r=>r.article_id)});
        } else if (request.method === 'POST') {
          const parsed = await readJson(request); if(parsed.error) return protect(parsed.error);
          const value = parsed.value;
          const operations = reading ? ['read','unread'] : ['star','unstar','bad','unbad'];
          if (Object.keys(value).some(k => !['articleId','operation'].includes(k)) || !articleIds.has(value.articleId) || !operations.includes(value.operation)) return protect(json({error:'Invalid operation or article'},400));
          const now = new Date().toISOString(); let statement;
          if (value.operation === 'read') statement = env.DB.prepare('INSERT INTO reading_state (user_id, article_id, read_at) VALUES (?, ?, ?) ON CONFLICT(user_id, article_id) DO UPDATE SET read_at = excluded.read_at').bind(user,value.articleId,now);
          else if (value.operation === 'unread') statement = env.DB.prepare('DELETE FROM reading_state WHERE user_id = ? AND article_id = ?').bind(user,value.articleId);
          else if (['star','unstar'].includes(value.operation)) statement = env.DB.prepare('INSERT INTO article_preferences (user_id, article_id, starred, starred_updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, article_id) DO UPDATE SET starred = excluded.starred, starred_updated_at = excluded.starred_updated_at').bind(user,value.articleId,value.operation === 'star' ? 1 : 0,now);
          else statement = env.DB.prepare('INSERT INTO article_preferences (user_id, article_id, bad, bad_updated_at, bad_reason, bad_source, bad_source_message_id) VALUES (?, ?, ?, ?, NULL, ?, NULL) ON CONFLICT(user_id, article_id) DO UPDATE SET bad = excluded.bad, bad_updated_at = excluded.bad_updated_at, bad_reason = NULL, bad_source = excluded.bad_source, bad_source_message_id = NULL').bind(user,value.articleId,value.operation === 'bad' ? 1 : 0,now,`reader:${value.operation}`);
          const result = await statement.run(); if (result.success === false) throw new Error('DB write failed');
          response = json({ok:true});
        } else response = json({error:'Method not allowed'},405);
      } else if (path.startsWith('/api/images/')) {
        if (!['GET','HEAD'].includes(request.method)) return protect(json({error:'Method not allowed'},405));
        const entry = images.get(path);
        if (!entry || url.search) return protect(json({error:'Image not registered'},404));
        if (!env.IMAGES) throw new Error('Missing private R2 binding');
        const object = await env.IMAGES.get(entry.object_key);
        if (!object) throw new Error('Registered image unavailable');
        const bytes = await object.arrayBuffer();
        await verifyImageBytes(bytes,entry,object.httpMetadata?.contentType);
        response = new Response(request.method === 'HEAD' ? null : bytes,{headers:{'Content-Type':'image/webp','Content-Length':String(bytes.byteLength)}});
      } else if (path.startsWith('/api/')) response = json({error:'Not found'},404);
      else {
        if (!['GET','HEAD'].includes(request.method)) return protect(json({error:'Method not allowed'},405));
        if (/^\/(?:data\/runs|pipeline|worker|db|drizzle|\.git|\.env)(?:\/|$)/.test(path)) return protect(json({error:'Not found'},404));
        if (!env.ASSETS) throw new Error('Missing assets');
        // Asset filenames retain the literal encoded ID (e.g. hep-th%2F...).
        // The binding decodes URL escapes once, so protect '%' internally.
        // External double encoding is still rejected above; only exact known
        // article paths are mapped, after owner and traversal validation.
        let assetRequest = request;
        if (assetPaths.has(path) && assetPaths.get(path) !== path) {
          const assetURL = new URL(request.url);
          assetURL.pathname = assetPaths.get(path);
          assetRequest = new Request(assetURL,request);
        }
        response = await env.ASSETS.fetch(assetRequest);
      }
      return protect(response);
    } catch { return protect(json({error:'Service unavailable'},503)); }
  }};
}
export default createWorker();
