import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
/** Persist only a project ID returned by Sites registration; this does not
 * create a Site, issue credentials, deploy, or modify hosted configuration. */
export async function initSite({root=process.cwd(),projectId}) {
  if(typeof projectId!=='string'||!projectId.trim()||/\s/.test(projectId)||projectId.includes('CONFIGURE_')) throw Error('Use the exact project_id returned by create_site');
  const file=resolve(root,'.openai/hosting.json');let manifest={};
  try{manifest=JSON.parse(await readFile(file,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
  if(manifest.project_id && !manifest.project_id.startsWith('CONFIGURE_') && manifest.project_id!==projectId) throw Error('Existing Site identity differs; refusing to retarget this checkout');
  manifest={...manifest,project_id:projectId,d1:'DB',r2:'IMAGES'};
  await mkdir(resolve(root,'.openai'),{recursive:true});await writeFile(file,JSON.stringify(manifest,null,2)+'\n');
  return {project_id:projectId,manifest:'.openai/hosting.json',runtime_variables:{TRUSTED_AUTH_HEADER:'hosting',SITES_OWNER_PRIVATE:'confirmed',OWNER_SETUP_ENABLED:'true',SITES_STORAGE_ENABLED:'false'},next:'Confirm owner-private Sites ingress before applying runtime variables. Then sign in and read /api/setup/identity; explicitly set OWNER_USER_ID and disable OWNER_SETUP_ENABLED.'};
}
if(import.meta.url===pathToFileURL(process.argv[1]||'').href){
  if(process.argv.length!==4||process.argv[2]!=='--project-id')throw Error('Usage: node scripts/init-site.mjs --project-id <returned-id>');
  console.log(JSON.stringify(await initSite({projectId:process.argv[3]}),null,2));
}
