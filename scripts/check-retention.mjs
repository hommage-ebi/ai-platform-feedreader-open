import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
export function allowedTrackedPath(path) {
 if(/(^|\/)(node_modules|\.venv|\.cache|dist|\.sites-runtime)(\/|$)/.test(path)||/(^|\/)\.env($|\.)/.test(path)&&!path.endsWith('.env.example'))return false;
 if(/(^|\/)(?:\.collection\.lock|\.merge\.lock)(?:\/|$)/.test(path))return false;
 if(path==='data/articles/.collection-transaction.json'||/^data\/runs\/\.paper-policy\.lock(?:\/|$)/.test(path))return false;
 if(!path.startsWith('data/runs/'))return true;
 if(/\/(site|figures|tasks)\//.test(path))return false;
 if(/\/(collection-capture|processing-policy|paper-policies)\.json$/.test(path))return /^data\/runs\/[^/]+\/[^/]+\/run\/(collection-capture|processing-policy|paper-policies)\.json$/.test(path);
 const name=path.split('/').at(-1);
 if(/\/groups\//.test(path))return false;
 if(/\/(policies|task-receipts)\/[^/]+\.json$/.test(path))return true;
 return /^(preflight|ledger|release|candidates|classification|source|source-history|metadata|result|selected|groups|classifications|classification-proofs|model-request|model-output-receipt)\.json$/.test(name)||/^classification-[\w-]+\.json$/.test(name)||name==='summary.md'||name==='failures.jsonl';
}
// Read immutable index object IDs, never the potentially different working tree.
// Suppress Git stderr and replace errors: output must never include blob contents.
function git(cwd, args) {
 try {
  return execFileSync('git', args, {cwd, encoding:'utf8', maxBuffer:64*1024*1024, stdio:['ignore','pipe','pipe']});
 } catch {
  throw new Error('Unable to inspect Git index; retention check failed closed');
 }
}
function hasCredential(text, path) {
 if(/-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----/.test(text)||/\b(?:sk-proj-|ghp_|AKIA)[A-Za-z0-9]{16,}/.test(text))return true;
 if(!/(^|\/)\.env(?:\.|$)/.test(path))return false;
 // Template files may contain only empty values or explicit placeholder literals
 // for credential-named variables. This also catches opaque service tokens.
 for(const line of text.split(/\r?\n/)) {
  const assignment=line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
  if(!assignment||!/(?:^|_)(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY(?:_ID)?|PRIVATE_KEY)(?:_|$)/i.test(assignment[1]))continue;
  let value=assignment[2];
  if(value.startsWith('"')||value.startsWith("'")) {
   const end=value.indexOf(value[0],1);
   if(end<0)return true;
   value=value.slice(1,end);
  } else value=value.replace(/\s+#.*$/,'').trim();
  if(!/^(?:|example|placeholder|changeme|change_me|replace_me|your_api_key|your_token|your_secret|your_password|<[^<>\r\n]+>)$/i.test(value))return true;
 }
 return false;
}
export function checkRetention({cwd=process.cwd()}={}) {
 const root=git(cwd,['rev-parse','--show-toplevel']).trimEnd();
 const records=git(root,['ls-files','--stage','-z']).split('\0').filter(Boolean);
 for(const record of records) {
  const match=record.match(/^(\d+) ([a-f0-9]+) ([0-3])\t([\s\S]*)$/);
  if(!match)throw new Error('Invalid Git index record');
  const [,mode,oid,stage,path]=match;
  const label=JSON.stringify(path);
  if(stage!=='0')throw new Error(`Unresolved Git index entry: ${label}`);
  if(!allowedTrackedPath(path))throw new Error(`Forbidden tracked artifact: ${label}`);
  if(!['100644','100755','120000'].includes(mode))throw new Error(`Unsupported Git index entry: ${label}`);
  const text=git(root,['cat-file','blob',oid]);
  if(hasCredential(text,path))throw new Error(`Possible credential: ${label}`);
  if(/^data\/runs\/[^/]+\/[^/]+\/run\/(?:candidates|selected|paper-policies)\.json$/.test(path)) {
   let rows;try {rows=JSON.parse(text);}catch {throw new Error(`Invalid run record: ${label}`);}
   if(path.endsWith('/paper-policies.json')) {
    if(Object.values(rows).some(row=>'policy' in row))throw new Error(`Embedded per-paper policy: ${label}`);
   } else if(!Array.isArray(rows)||rows.some(row=>'title' in row||'abstract' in row))throw new Error(`Verbose candidate record: ${label}`);
  }
  if(path.startsWith('data/runs/')&&/data:(?:application\/pdf|image\/)\S*;base64,/.test(text))throw new Error(`Embedded raw material: ${label}`);
 }
 return records.length;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
 try {
  const count=checkRetention();
  console.log(`Retention check passed: ${count} Git index files (untracked files and working-tree changes are not checked)`);
 } catch(error) {
  console.error(error.message);
  process.exitCode=1;
 }
}
