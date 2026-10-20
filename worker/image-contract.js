export function safeObjectKey(key) {
  if(typeof key !== 'string' || !/^[A-Za-z0-9_%./~-]+$/.test(key) || /%(?:2e|5c)/i.test(key))return false;
  try {
    return key.split('/').every(segment => {
      const decoded=decodeURIComponent(segment);
      return !/[\u0000-\u001f\u007f\\%]/.test(decoded) && decoded.split('/').every(part => part && part!=='.' && part!=='..');
    });
  } catch { return false; }
}
export function isArxivImageURL(value, articleId, version) {
  if(typeof value!=='string'||/[\u0000-\u0020\u007f\\%?#]/.test(value))return false;
  const match=/^https:\/\/arxiv\.org\/html\/((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?\/\d{7}))(v[1-9]\d*)\/(.+)$/.exec(value);
  if(!match||match[3].split('/').some(p=>!p||p==='.'||p==='..'))return false;
  return (articleId===undefined||match[1]===articleId)&&(version===undefined||match[2]===version);
}
export function validateLinkedImages(article, version) {
  const images=version.linked_images||[];
  if(!Array.isArray(images))throw new Error('Invalid linked_images');
  const refs=new Set(),urls=new Set();
  for(const image of images) {
    if(String(article.source).toLowerCase()!=='arxiv'||!isArxivImageURL(image.url,article.id,version.version)||!/^selected:[A-Za-z0-9_-]{1,80}$/.test(image.source_ref||'')||image.selection?.kind!=='html'||!image.selection.element_id||!Number.isSafeInteger(image.selection.asset_index)||image.selection.asset_index<0||!/^[a-f0-9]{64}$/.test(image.source_document_sha256||''))throw new Error('Invalid direct arXiv image');
    if(refs.has(image.source_ref)||urls.has(image.url))throw new Error('Duplicate direct image');
    refs.add(image.source_ref);urls.add(image.url);
  }
  if((version.stored_images||[]).some(image=>refs.has(image.source_ref)))throw new Error('Conflicting stored/direct image reference');
}
export function validateArticleImagePaths(paths, {stored_images=[],linked_images=[]}={}) {
  const stored=stored_images.map(image=>image.serving_path),linked=linked_images.map(image=>image.url);
  const allowed=new Set([...stored,...linked]);
  if(paths.length!==stored.length+linked.length||paths.some(path=>!allowed.has(path))||JSON.stringify(paths.filter(path=>stored.includes(path)))!==JSON.stringify(stored)||JSON.stringify(paths.filter(path=>linked.includes(path)))!==JSON.stringify(linked))throw new Error('Article images mismatch registered references');
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
