export function safeObjectKey(key) {
  if(typeof key !== 'string' || !/^[A-Za-z0-9_%./~-]+$/.test(key) || /%(?:2e|5c)/i.test(key))return false;
  try {
    return key.split('/').every(segment => {
      const decoded=decodeURIComponent(segment);
      return !/[\u0000-\u001f\u007f\\%]/.test(decoded) && decoded.split('/').every(part => part && part!=='.' && part!=='..');
    });
  } catch { return false; }
}
export function validateImageManifest(manifest, articleIds) {
  if (manifest?.schema_version !== 1 || !Array.isArray(manifest.images)) throw new Error('Invalid image manifest');
  const paths = new Map(), identities = new Set();
  for (const image of manifest.images) {
    if (!articleIds.has(image.article_id) || typeof image.version !== 'string' || !image.version || typeof image.source_ref !== 'string' || !image.source_ref || !safeObjectKey(image.object_key) || image.mime_type !== 'image/webp' || !/^[a-f0-9]{64}$/.test(image.sha256) || image.serving_path !== `/api/images/${image.sha256}` || !['width','height','byte_size'].every(k => Number.isSafeInteger(image[k]) && image[k] > 0) || image.byte_size > 32 * 1024 * 1024) throw new Error('Invalid image entry');
    const identity = JSON.stringify([image.article_id,image.version,image.source_ref]);
    if (identities.has(identity)) throw new Error('Duplicate image identity');
    identities.add(identity);
    const previous = paths.get(image.serving_path);
    if (previous && ['sha256','width','height','byte_size','mime_type'].some(k => previous[k] !== image[k])) throw new Error('Conflicting image path');
    paths.set(image.serving_path, image);
  }
  return paths;
}
