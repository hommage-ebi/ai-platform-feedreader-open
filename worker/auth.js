/** Sites-specific identity adapter. Hosting ingress authenticates the visitor,
 * strips client-supplied identity, and injects this header. This is not a JWT
 * verifier and must never be enabled behind an untrusted public ingress. */
export function sitesIdentity(request, env) {
  if (env.TRUSTED_AUTH_HEADER !== 'hosting') {
    return {status:503,error:'Authentication unavailable'};
  }
  const userId = request.headers.get('oai-authenticated-user-id');
  return userId ? {userId} : {status:401,error:'Authentication required'};
}

/** Sites enforces the owner-only audience before forwarding requests. Use its
 * verified Site identity directly for user-owned records. Other deployments
 * require an explicit owner ID; an optional pin still takes precedence. */
export function ownerAuthorization(identity, env) {
  const owner = typeof env.OWNER_USER_ID === 'string' && env.OWNER_USER_ID.trim() ? env.OWNER_USER_ID : null;
  const ownerPrivate = env.TRUSTED_AUTH_HEADER === 'hosting' && env.SITES_OWNER_PRIVATE === 'confirmed';
  if (!owner && !ownerPrivate) return {status:503,error:'Authentication unavailable'};
  if (identity?.error) return identity;
  if (typeof identity?.userId !== 'string' || !identity.userId.trim()) {
    return {status:401,error:'Authentication required'};
  }
  if (owner && identity.userId !== owner) return {status:403,error:'Forbidden'};
  return {userId:identity.userId};
}
