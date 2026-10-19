import {safeObjectKey} from './image-contract.js';
import {validArticleId} from './content-identity.js';
// Only enable on a confirmed owner-private Site behind Sites dispatch. Dispatch
// consumes OAI-Sites-Authorization; it does not give service calls a user ID.
const LIMIT = 32 * 1024 * 1024;
const json = (error, status) => new Response(JSON.stringify({error}), {status, headers:{'Content-Type':'application/json'}});
export function privateSitesBoundary(env) {
  return env.TRUSTED_AUTH_HEADER === 'hosting' && env.SITES_OWNER_PRIVATE === 'confirmed';
}
export async function storageRequest(request, env) {
  if (!privateSitesBoundary(env) || env.SITES_STORAGE_ENABLED !== 'true') return json('Storage access unavailable',503);
  const visitor=request.headers.get('oai-authenticated-user-id');
  if(visitor && (!env.OWNER_USER_ID || visitor!==env.OWNER_USER_ID)) return json('Forbidden',403);
  const url = new URL(request.url), key = url.searchParams.get('key');
  if ([...url.searchParams.keys()].some(k=>k!=='key') || url.searchParams.getAll('key').length !== 1 || !/^papers\/[A-Za-z0-9_%~.-]+\/[A-Za-z0-9][A-Za-z0-9._-]{0,63}\/([a-f0-9]{64})\.(webp|svg)$/.test(key||'') || !safeObjectKey(key)) return json('Invalid object key',400);
  // Decode only the article namespace, never the version or object basename.
  // Encoded slashes belong to a source ID; double encodings and traversal do not.
  try { const segment=key.split('/')[1],id=decodeURIComponent(segment); const canonical=encodeURIComponent(id).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase()); if(!validArticleId(id)||segment!==canonical) return json('Invalid object key',400); } catch { return json('Invalid object key',400); }
  const match = key.match(/\/([a-f0-9]{64})\.(webp|svg)$/), mime = match[2] === 'webp' ? 'image/webp' : 'image/svg+xml';
  if (!env.IMAGES) return json('Storage unavailable',503);
  if (!['GET','PUT'].includes(request.method)) return json('Method not allowed',405);
  const origin = request.headers.get('Origin');
  if (origin && origin !== url.origin) return json('Same-origin request required',403);
  if (request.method === 'PUT') {
    // Custom header blocks cross-site simple requests; never emit CORS grants.
    if (request.headers.get('X-Reader-Upload') !== '1') return json('Upload header required',403);
    if (request.headers.get('Content-Type') !== mime) return json('Invalid media type',415);
    if (Number(request.headers.get('Content-Length')) > LIMIT) return json('Object too large',413);
    const reader=request.body?.getReader(), parts=[];let size=0;
    if(reader) while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>LIMIT){await reader.cancel();return json('Object too large',413);}parts.push(value);}
    const bytes=new Uint8Array(size);let offset=0;for(const p of parts){bytes.set(p,offset);offset+=p.byteLength;}
    if (!size || await hash(bytes) !== match[1]) return json('Object hash mismatch',400);
    await env.IMAGES.put(key,bytes,{httpMetadata:{contentType:mime}});
  }
  const object=await env.IMAGES.get(key);
  if (!object) return json('Object not found',404);
  if (Number.isFinite(object.size) && object.size > LIMIT) return json('Object too large',413);
  const bytes=await object.arrayBuffer();
  if(bytes.byteLength > LIMIT || object.httpMetadata?.contentType !== mime || await hash(bytes) !== match[1]) return json('Object integrity failure',503);
  return new Response(bytes,{headers:{'Content-Type':mime,'Content-Length':String(bytes.byteLength),'Content-Disposition':'attachment'}});
}
async function hash(bytes) {return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');}
