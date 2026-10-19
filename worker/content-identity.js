/** Public identity/path contract shared by the build and serving boundary. */
export function validArticleId(id) {
 try { encodeURIComponent(id); } catch { return false; }
 return typeof id==='string' && id.length>0 && !/[\u0000-\u001f\u007f\\%]/.test(id) && !id.split('/').some(part=>!part || part==='.' || part==='..');
}
export function htmlPaths(path) {
 if(typeof path!=='string'||!path.startsWith('papers/')||!path.endsWith('.html')||/[\u0000-\u001f\u007f\\?#]/.test(path)||path.split('/').some(part=>!part||part==='.'||part==='..')||/%(?!2f)/i.test(path))throw new Error('Invalid standalone HTML path');
 // External paths retain the legacy %2F convention. The asset binding decodes
 // once, so its request must separately encode each literal filename segment.
 return {publicPath:new URL('/'+path,'https://reader.invalid').pathname,assetPath:'/'+path.split('/').map(encodeURIComponent).join('/')};
}
export function bodyPaths(id,version='current') {
 if(!validArticleId(id))throw new Error('Invalid article id');
 const publicPath=`/reader-articles/${encodeURIComponent(id)}/${encodeURIComponent(version || 'current')}.json`;
 return {publicPath,assetPath:publicPath.split('/').map(encodeURIComponent).join('/')};
}
