import {createHash} from 'node:crypto';
import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync, spawnSync} from 'node:child_process';
import {mkdtemp, writeFile, readFile, rm, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {checkRetention, allowedTrackedPath} from '../scripts/check-retention.mjs';

const checker=fileURLToPath(new URL('../scripts/check-retention.mjs',import.meta.url));
// Generated, deliberately synthetic, and never included in assertion output.
const syntheticCredential=['ghp','_','SYNTHETICNOTAREALCREDENTIAL123456789'].join('');
async function fixture(fn) {
 const cwd=await mkdtemp(join(tmpdir(),'reader-retention-'));
 const git=(...args)=>execFileSync('git',args,{cwd,encoding:'utf8',stdio:['ignore','pipe','pipe']});
 const put=async(path,text)=>{await writeFile(join(cwd,path),text);};
 const stage=async(path,text)=>{await put(path,text);git('add','--',path);};
 try {git('init','--quiet');await fn({cwd,git,put,stage});}
 finally {await rm(cwd,{recursive:true,force:true});}
}
test('staged credential is rejected after working tree is cleaned, without disclosing bytes',()=>fixture(async({cwd,stage,put})=>{
 await stage('config.txt',syntheticCredential);await put('config.txt','clean');
 const result=spawnSync(process.execPath,[checker],{cwd,encoding:'utf8'});
 assert.equal(result.status,1);assert.match(result.stderr,/Possible credential: "config.txt"/);
 assert.equal((result.stdout+result.stderr).includes(syntheticCredential),false);
}));
test('env example scans indexed bytes for known and opaque credentials',()=>fixture(async({cwd,stage,put})=>{
 for(const value of [syntheticCredential,'synthetic-opaque-service-credential']) {
  await stage('.env.example',`SITES_SERVICE_TOKEN=${value}\n`);
  await put('.env.example','SITES_SERVICE_TOKEN=\n');
  assert.throws(()=>checkRetention({cwd}),/Possible credential/);
 }
}));
test('blank and explicit example placeholder credentials are allowed',()=>fixture(async({cwd,stage})=>{
 await stage('.env.example',`SITES_SERVICE_TOKEN=\nAPI_KEY="example"\nPASSWORD='changeme'\nAWS_SECRET_ACCESS_KEY=<your-secret>\nACCESS_TOKEN=placeholder # explain\nOWNER_SETUP_ENABLED=false\n`);
 assert.equal(checkRetention({cwd}),1);
}));
test('all indexed extensions and extensionless files are scanned',()=>fixture(async({cwd,stage,git})=>{
 for(const path of ['app.js','config.yaml','README']) {
  await stage(path,syntheticCredential);assert.throws(()=>checkRetention({cwd}),/Possible credential/);
  git('rm','--cached','--',path);
 }
}));
test('unstaged removal still scans the index; staged deletion excludes that entry',()=>fixture(async({cwd,stage,git})=>{
 await stage('config.txt',syntheticCredential);await rm(join(cwd,'config.txt'));
 assert.throws(()=>checkRetention({cwd}),/Possible credential/);
 git('rm','--cached','--','config.txt');assert.equal(checkRetention({cwd}),0);
}));
test('staged rename scans the new index path and blob, including unusual path characters',()=>fixture(async({cwd,stage,git,put})=>{
 await stage('old.txt',syntheticCredential);
 const renamed='renamed\twith\nspaces.env.example';git('mv','--','old.txt',renamed);await put(renamed,'clean');
 assert.throws(()=>checkRetention({cwd}),/Possible credential: "renamed/);
}));
test('untracked and ignored files and unstaged edits are explicitly outside the index check',()=>fixture(async({cwd,stage,put})=>{
 await stage('config.txt','clean');await put('config.txt',syntheticCredential);
 await put('untracked.txt',syntheticCredential);await put('.env',syntheticCredential);
 await put('.gitignore','.env\n');assert.equal(checkRetention({cwd}),1);
}));
test('forbidden paths and embedded run material are checked in staged content',()=>fixture(async({cwd,stage,git})=>{
 await stage('.env','API_KEY=');assert.throws(()=>checkRetention({cwd}),/Forbidden tracked artifact/);
 git('rm','--cached','--','.env');await mkdir(join(cwd,'data/runs/x'),{recursive:true});
 await stage('data/runs/x/summary.md','data:image/png;base64,synthetic');
 assert.throws(()=>checkRetention({cwd}),/Embedded raw material/);
}));
test('invoking from a subdirectory still checks the full index',()=>fixture(async({cwd,stage})=>{
 await stage('config.txt',syntheticCredential);await mkdir(join(cwd,'nested'));
 assert.throws(()=>checkRetention({cwd:join(cwd,'nested')}),/Possible credential/);
}));
test('unmerged entries fail closed without inspecting conflict bytes',()=>fixture(async({cwd,git})=>{
 const oid=git('hash-object','-w','--stdin').trim();
 execFileSync('git',['update-index','--index-info'],{cwd,input:`100644 ${oid} 1\tconflict.txt\n100644 ${oid} 2\tconflict.txt\n`,stdio:['pipe','pipe','pipe']});
 assert.throws(()=>checkRetention({cwd}),/Unresolved Git index entry/);
}));

test('durable collection capture is allowed only at its canonical run location',()=>{
 assert.equal(allowedTrackedPath('data/runs/2026-10-19/run-id/run/collection-capture.json'),true);
 for(const path of ['data/runs/collection-capture.json','data/runs/date/id/collection-capture.json','data/runs/date/id/run/papers/paper/collection-capture.json','data/runs/date/id/tasks/collection-capture.json','data/runs/date/id/site/collection-capture.json'])assert.equal(allowedTrackedPath(path),false,path);
 assert.equal(allowedTrackedPath('data/model-task-claims.json'),true);
});
test('source revision evidence survives ordinary staging',()=>fixture(async({cwd,git,stage})=>{
 const path='data/runs/2026-10-19/run-id/run/papers/2610.12345/v1/source-history.json';
 await stage('.gitignore',await readFile(new URL('../.gitignore',import.meta.url),'utf8'));
 await mkdir(join(cwd,'data/runs/2026-10-19/run-id/run/papers/2610.12345/v1'),{recursive:true});
 await stage(path,JSON.stringify([{kind:'html',sha256:'a'.repeat(64),verified:true}]));
 assert.equal(allowedTrackedPath(path),true);assert.equal(checkRetention({cwd}),2);
 assert.match(git('ls-files','--',path),/source-history\.json/);
}));
test('durable capture and claim registry survive ordinary staging and a fresh clone',()=>fixture(async({cwd,git,stage})=>{
 const capture='data/runs/2026-10-19/run-id/run/collection-capture.json';
 const claims='data/model-task-claims.json';
 await stage('.gitignore',await readFile(new URL('../.gitignore',import.meta.url),'utf8'));
 await mkdir(join(cwd,'data/runs/2026-10-19/run-id/run'),{recursive:true});
 await stage(capture,JSON.stringify({schema_version:1,target_date:'2026-10-19',candidates_sha256:'synthetic',scope:[]}));
 await stage(claims,JSON.stringify({synthetic:{target_keys:['paper@v1'],token:'synthetic-reservation-id',run_dir:'data/runs/2026-10-19/run-id'}}));
 assert.equal(checkRetention({cwd}),3);
 git('-c','user.name=Retention Test','-c','user.email=retention@example.invalid','commit','--quiet','-m','Synthetic durable records');
 const clone=join(cwd,'fresh-clone');git('clone','--quiet','--no-local','--',cwd,clone);
 for(const path of [capture,claims])assert.equal(await readFile(join(clone,path),'utf8'),await readFile(join(cwd,path),'utf8'));
 assert.equal(checkRetention({cwd:clone}),3);
}));

test('durable policy snapshots and mappings are allowed only at canonical run locations',()=>{
 assert.equal(allowedTrackedPath('data/runs/.paper-policy.lock/ledger.json'),false);
 for(const name of ['processing-policy.json','paper-policies.json']) {
  assert.equal(allowedTrackedPath(`data/runs/2026-10-19/run-id/run/${name}`),true);
  for(const prefix of ['data/runs','data/runs/date/id','data/runs/date/id/run/papers/paper','data/runs/date/id/tasks','data/runs/date/id/site'])assert.equal(allowedTrackedPath(`${prefix}/${name}`),false);
 }
});
test('frozen run policy and paper mapping survive config changes, commit and fresh clone',()=>fixture(async({cwd,git,stage})=>{
 await stage('.gitignore',await readFile(new URL('../.gitignore',import.meta.url),'utf8'));
 const run='data/runs/2026-10-19/run-id/run';
 await mkdir(join(cwd,run),{recursive:true});await mkdir(join(cwd,'pipeline/arxiv/config'),{recursive:true});
 const frozen={schema_version:1,
  agents:JSON.parse(await readFile(new URL('../pipeline/config/agents.json',import.meta.url),'utf8')),
  interests:JSON.parse(await readFile(new URL('../pipeline/config/interests.json',import.meta.url),'utf8')),
  sources:JSON.parse(await readFile(new URL('../pipeline/arxiv/config/sources.json',import.meta.url),'utf8')),
  relevance:await readFile(new URL('../pipeline/arxiv/prompts/relevance.txt',import.meta.url),'utf8'),
  summary:await readFile(new URL('../pipeline/arxiv/prompts/summary.txt',import.meta.url),'utf8'),
 };
 const sorted=value=>Array.isArray(value)?value.map(sorted):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,sorted(value[key])])):value;
 frozen.sha256=createHash('sha256').update(JSON.stringify(sorted(frozen))).digest('hex');
 const records={
  [`${run}/processing-policy.json`]:frozen,
  [`${run}/paper-policies.json`]:{'synthetic-paper/v1':{input_sha256:'0'.repeat(64),policy_sha256:frozen.sha256}},
 };
 for(const [path,record] of Object.entries(records))await stage(path,JSON.stringify(record));
 await stage('pipeline/arxiv/config/sources.json',JSON.stringify(frozen.sources));
 git('-c','user.name=Retention Test','-c','user.email=retention@example.invalid','commit','--quiet','-m','Freeze synthetic original policy');
 await stage('pipeline/arxiv/config/sources.json',JSON.stringify({schema_version:1,categories:['math.AG']}));
 git('-c','user.name=Retention Test','-c','user.email=retention@example.invalid','commit','--quiet','-m','Change only future policy source');
 const clone=join(cwd,'fresh-clone');git('clone','--quiet','--no-local','--',cwd,clone);
 for(const [path,record] of Object.entries(records))assert.deepEqual(JSON.parse(await readFile(join(clone,path),'utf8')),record);
 assert.equal(checkRetention({cwd:clone}),4);
 for(const name of ['processing-policy.json','paper-policies.json']) {
  const unexpected=`data/runs/2026-10-19/run-id/${name}`;
  assert.match(git('check-ignore','--',unexpected),new RegExp(name.replace('.','\\.')));
 }
}));
test('staged policy records remain subject to credential scanning after clean working-tree edits',()=>fixture(async({cwd,stage,put,git})=>{
 const run='data/runs/2026-10-19/run-id/run';await mkdir(join(cwd,run),{recursive:true});
 for(const name of ['processing-policy.json','paper-policies.json']) {
  const path=`${run}/${name}`;
  await stage(path,JSON.stringify({synthetic:syntheticCredential}));await put(path,'{}');
  assert.throws(()=>checkRetention({cwd}),/Possible credential/);
  git('add','--',path);git('rm','--cached','--',path);
 }
}));

test('verbose candidates and embedded paper policies cannot be staged',()=>fixture(async({cwd,stage,git})=>{
 const run='data/runs/2026-10-19/run-id/run';await mkdir(join(cwd,run),{recursive:true});
 for(const name of ['candidates','selected']) {
  const path=`${run}/${name}.json`;
  await stage(path,JSON.stringify([{id:'synthetic',version:'v1',title:'Synthetic title',abstract:'Synthetic abstract'}]));
  assert.throws(()=>checkRetention({cwd}),/Verbose candidate record/);
  await stage(path,JSON.stringify([{id:'synthetic',version:'v1',input_sha256:'0'.repeat(64)}]));
  assert.doesNotThrow(()=>checkRetention({cwd}));git('rm','--cached','--',path);
 }
 const path=`${run}/paper-policies.json`;
 await stage(path,JSON.stringify({synthetic:{input_sha256:'0'.repeat(64),policy:{summary:'Repeated prompt'}}}));
 assert.throws(()=>checkRetention({cwd}),/Embedded per-paper policy/);
}));
test('temporary group inputs are ignored while distinct policy snapshots can be staged',()=>fixture(async({git,stage,cwd})=>{
 await stage('.gitignore',await readFile(new URL('../.gitignore',import.meta.url),'utf8'));
 const run='data/runs/2026-10-19/run-id';
 for(const path of [`${run}/run/groups/verbose.json`,`${run}/.cache/groups/input.json`,`${run}/.cache/classification-inputs.json`]) {
  assert.equal(allowedTrackedPath(path),false);assert.ok(git('check-ignore','--',path).trim());
 }
 const snapshot=`${run}/run/policies/${'0'.repeat(64)}.json`;
 assert.equal(allowedTrackedPath(snapshot),true);await mkdir(join(cwd,run,'run/policies'),{recursive:true});
 await stage(snapshot,'{}');assert.doesNotThrow(()=>checkRetention({cwd}));
}));
