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

/** Provider-independent owner authorization. A different verified identity
 * provider can replace sitesIdentity at factory construction, but never skip
 * this gate. There is deliberately no environment-selected permissive mode. */
export function ownerAuthorization(identity, env) {
  if (typeof env.OWNER_USER_ID !== 'string' || !env.OWNER_USER_ID.trim()) {
    return {status:503,error:'Authentication unavailable'};
  }
  if (identity?.error) return identity;
  if (typeof identity?.userId !== 'string' || !identity.userId) {
    return {status:401,error:'Authentication required'};
  }
  if (identity.userId !== env.OWNER_USER_ID) return {status:403,error:'Forbidden'};
  return {userId:identity.userId};
}
