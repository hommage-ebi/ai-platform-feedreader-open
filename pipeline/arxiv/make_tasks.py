"""External native-model handoff. Task creation never means model execution.
claim/finish wrap every model launch across all runs. Claims deliberately never
expire automatically: a lost executor must be explicitly released after checking
that its model stopped, preventing duplicate execution after a restart.
"""
import re
import shutil, uuid
from pathlib import Path
from pipeline.shared.runtime import digest, lock, now, project_root, read, record, write
from .common import identity, paper_path, validate_candidates
from .policy import run_policy,relevance_template,expand_prompt,summary_template,agent_settings
from pipeline.shared.agents import load_agents

def summary_fingerprint(directory):
    p=Path(directory);source=read(p/'source.json')
    metadata_hash=digest((p/'metadata.json').read_bytes());request=read(p/'model-request.json',{});receipt=read(p/'model-output-receipt.json',{})
    if receipt.get('outputs',{}).get('metadata.json')==metadata_hash and request.get('metadata_input_hash'):metadata_hash=request['metadata_input_hash']
    return {'id':source['id'],'version':source['version'],'source_sha256':source['sha256'],'metadata_sha256':metadata_hash}

def attest_summary(task):
    model=task['model'];effort=task['reasoning_effort']
    if task['kind']=='classify':
        output=Path(task['output_files'][0])
        write(output.with_suffix('.receipt.json'),{'cache_key':task['cache_key'],'output_sha256':digest(output.read_bytes()),'model':model,'reasoning_effort':effort,'verified_at':now()})
        return
    p=Path(task['output_files'][0]).parent
    write(p/'model-output-receipt.json',{'cache_key':task['cache_key'],'outputs':{Path(f).name:digest(Path(f).read_bytes()) for f in task['output_files']},'verified_at':now()})

def state_dir(root):return Path(root)/'.cache/arxiv/model-tasks'
def durable_claims_path(root):return Path(root)/'data/model-task-claims.json'

def relative_run(root,value):
    root=Path(root).resolve();path=Path(value)
    if path.is_absolute():
        try:path=path.resolve().relative_to(root)
        except ValueError:
            # Migrate old absolute registries after moving/cloning a project.
            parts=path.parts[-4:]
            if len(parts)!=4 or parts[:2]!=('data','runs'):raise ValueError('Unsafe claim run path')
            path=Path(*parts)
    if len(path.parts)!=4 or path.parts[:2]!=('data','runs') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',path.parts[2]) or not re.fullmatch(r'[A-Za-z0-9_-]+',path.parts[3]):raise ValueError('Unsafe claim run path')
    if not (root/path).resolve().is_relative_to(root):raise ValueError('Claim run path escapes project')
    return path.as_posix()

def descriptor_hash(root,task):
    prefix=str(Path(root).resolve())
    def portable(value):
        if isinstance(value,str):return value.replace(prefix+'/', '<PROJECT>/')
        if isinstance(value,list):return [portable(v) for v in value]
        if isinstance(value,dict):return {portable(k):portable(v) for k,v in value.items()}
        return value
    return digest(portable(task))

def save_claims(root,base,claims):
    # Reservations outlive disposable model-result caches.
    claims={key:{**value,'run_dir':relative_run(root,value['run_dir'])} for key,value in claims.items()}
    write(durable_claims_path(root),claims);write(base/'claims.json',claims)

def active_claims(root,base):
    durable=durable_claims_path(root)
    registry=durable if durable.exists() else base/'claims.json'
    has_registry=registry.exists();claims=read(registry,{})
    if not isinstance(claims,dict):raise ValueError('Model claim registry is corrupt')
    def valid(value):
        return isinstance(value,dict) and isinstance(value.get('target_keys'),list) and all(isinstance(k,str) and k for k in value['target_keys']) and isinstance(value.get('token'),str) and bool(value['token']) and isinstance(value.get('run_dir'),str)
    if any(not valid(v) for v in claims.values()):raise ValueError('Model claim registry entry is corrupt')
    claims={key:{**value,'run_dir':relative_run(root,value['run_dir'])} for key,value in claims.items()}
    blocked=[]
    for file in Path(root).glob('data/runs/*/*/ledger.json'):
        try:
            ledger=read(file,{})
            if not isinstance(ledger,dict) or not isinstance(ledger.get('model_claims',{}),dict):raise ValueError('Invalid run ledger claim schema')
            for key,value in ledger.get('model_claims',{}).items():
                if not isinstance(value,dict):raise ValueError('Invalid run claim entry')
                if value.get('status')!='running':continue
                candidate={**value,'run_dir':relative_run(root,str(file.parent))}
                if not valid(candidate):raise ValueError('Invalid running claim entry')
                previous=claims.get(key)
                if previous and (previous['token']!=candidate['token'] or previous['run_dir']!=candidate['run_dir']):
                    # Preserve both reservations and count both toward capacity.
                    claims[key]={**previous,'conflict':True}
                    claims[key+'.conflict.'+digest(candidate['token'])]={**candidate,'conflict':True}
                    blocked.append({'run_dir':str(file.parent),'error':'Conflicting claim mirror','status':'blocked','cache_key':key})
                elif not previous:claims[key]=candidate
        except (OSError,ValueError,TypeError) as exc:
            blocked.append({'run_dir':str(file.parent),'error':str(exc),'status':'blocked'})
    write(base/'blocked-runs.json',{'updated_at':now(),'runs':blocked})
    if blocked and not has_registry:
        raise RuntimeError('Cannot establish running-model capacity: claim registry missing and a run ledger is corrupt; recover reservations before launching models')
    return claims

def persist_claim(run,key,value):
    with lock(Path(run)/'.ledger.lock'):
        ledger=read(Path(run)/'ledger.json',{});ledger.setdefault('model_claims',{})[key]=value;write(Path(run)/'ledger.json',ledger)

def claim(root,task,owner):
    validate_task_inputs(root,task)
    base=state_dir(root)
    # Fail closed for the requested run before consulting unrelated history.
    ledger=read(Path(task['run_dir'])/'ledger.json')
    if not isinstance(ledger,dict):raise ValueError('Task run ledger is missing or corrupt')
    with lock(base/'registry.lock'):
        claims=active_claims(root,base)
        key=task['cache_key']
        if key in claims:raise RuntimeError('Identical model task is already running')
        targets=set(task.get('target_keys',[]))
        if any(targets.intersection(value.get('target_keys',[])) for value in claims.values()):raise RuntimeError('Paper input/prompt is already running in another model task')
        if len(claims)>=load_agents(root)['max_parallel']:raise RuntimeError('Global model concurrency limit reached')
        token=uuid.uuid4().hex;claims[key]={'token':token,'owner':owner,'claimed_at':now(),'task_name':task['task_name'],'status':'running','run_dir':relative_run(root,task['run_dir']),'target_keys':task['target_keys'],'descriptor_sha256':descriptor_hash(root,task)}
        # Reserve durably first: interruption may leave a conservative blocked
        # reservation, never an uncounted running model.
        save_claims(root,base,claims)
        persist_claim(task['run_dir'],key,{k:v for k,v in claims[key].items() if k!='run_dir'})
        return token

def release(root,key,token):
    base=state_dir(root)
    with lock(base/'registry.lock'):
        claims=active_claims(root,base)
        if claims.get(key,{}).get('conflict'):raise ValueError('Conflicting claim requires reconciliation')
        if claims.get(key,{}).get('token')!=token:raise ValueError('Claim token mismatch')
        persist_claim(Path(root)/relative_run(root,claims[key]['run_dir']),key,{**{k:v for k,v in claims[key].items() if k!='run_dir'},'status':'released','released_at':now()})
        del claims[key];save_claims(root,base,claims)

def validate_task_inputs(root,task,finishing=False):
    """Reconstruct the complete executable descriptor; no caller field grants trust."""
    root=Path(root).resolve()
    if not isinstance(task,dict) or task.get('kind') not in ('classify','summary'):raise ValueError('Invalid model task kind')
    kind=task['kind'];run=(root/relative_run(root,task.get('run_dir',''))).resolve()
    if task.get('run_dir')!=str(run):raise ValueError('Task paths changed; regenerate task in this checkout')
    run_policy(run)
    if kind=='classify':
        files=task.get('input_files')
        if not isinstance(files,list) or len(files)!=1:raise ValueError('Invalid classification input paths')
        item=Path(files[0]).resolve()
        if item.parent!=run/'run/groups':raise ValueError('Classification input is not in canonical group directory')
        rows=validate_candidates(read(item));settings=agent_settings(run,rows[0],kind);model=settings['model'];effort=settings['reasoning_effort'];template=relevance_template(run,rows);fingerprint=[{k:p[k] for k in ('id','version','title','abstract')} for p in rows]
        key=digest({'kind':kind,'input':fingerprint,'prompt':template,'model':model,'reasoning_effort':effort})
        if key!=task.get('cache_key'):raise ValueError('Model task inputs or prompt changed; regenerate task')
        canonical=run/'run/groups'/(digest({'papers':rows,'prompt_hash':digest(template.encode())})+'.json')
        if item!=canonical or str(item.relative_to(run)) not in read(run/'run/groups.json',[]):raise ValueError('Classification group path changed')
        candidates={identity(p):p for p in validate_candidates(read(run/'run/candidates.json',[]))}
        if any(identity(p) not in candidates or any(p[k]!=candidates[identity(p)][k] for k in ('title','abstract')) for p in rows):raise ValueError('Classification group no longer matches current candidates')
        inputs=[item];outputs=[item.with_name(item.stem+'.result.json')]
        expanded=expand_prompt(run,rows,item,outputs[0])
    else:
        original=task.get('input_fingerprint',{});identity(original);settings=agent_settings(run,original,kind);model=settings['model'];effort=settings['reasoning_effort'];template=summary_template(run,original)
        from .split_merge import validate_current_selection
        validate_current_selection(run,original)
        item=paper_path(run,original).resolve()
        if not item.is_relative_to(run):raise ValueError('Summary path escapes run')
        if identity(original) not in {identity(p) for p in read(run/'run/selected.json',[])}:raise ValueError('Summary paper is no longer selected')
        source=read(item/'source.json',{})
        if source.get('verified') is not True or identity(source)!=identity(original) or source.get('kind') not in ('html','pdf'):raise ValueError('Summary source changed')
        local=(item/source['local_file']).resolve()
        if not local.is_relative_to(item):raise ValueError('Summary input escapes paper directory')
        inputs=[item/'source.json',item/'metadata.json',local]
        if source['kind']=='html':inputs.append(item/'fulltext.html.txt')
        outputs=[item/'metadata.json',item/'result.json',item/'summary.md'];expanded=template.replace('{paper_dir}',str(item))
        fingerprint=summary_fingerprint(item);request=read(item/'model-request.json',{})
        if finishing:fingerprint['metadata_sha256']=original.get('metadata_sha256')
        key=digest({'kind':kind,'input':fingerprint,'prompt':template,'model':model,'reasoning_effort':effort})
        if request!={'cache_key':key,'input_hash':digest(fingerprint),'prompt_hash':digest(template.encode()),'metadata_input_hash':fingerprint['metadata_sha256']}:raise ValueError('Summary task input request changed; regenerate task')
        if digest(local.read_bytes())!=source['sha256'] or source['kind']=='html' and digest((item/'fulltext.html.txt').read_bytes())!=source.get('fulltext_sha256'):raise ValueError('Prepared summary input changed')
    target_keys=[digest({'kind':kind,'paper':p,'prompt':template,'model':model,'effort':effort}) for p in fingerprint] if kind=='classify' else [key]
    expected={'target_keys':target_keys,'task_name':kind+'_'+key[:16],'kind':kind,'cache_key':key,'model':model,'reasoning_effort':effort,'independent_context':True,'run_dir':str(run),'prompt':expanded,'input_fingerprint':fingerprint,'input_digests':{str(p.resolve()):digest(p.read_bytes()) for p in inputs if not (kind=='summary' and p.name=='metadata.json')},'input_files':[str(p.resolve()) for p in inputs],'output_files':[str(p.resolve()) for p in outputs]}
    if task!=expected:raise ValueError('Model task descriptor or inputs or prompt changed; regenerate task')

def finish(root,task,token):
    model=task['model'];effort=task['reasoning_effort']
    validate_task_inputs(root,task,finishing=True)
    # Only structurally validated output can be cached. Invalid output retains claim
    # until the caller deliberately releases it and records the failed execution.
    if task['kind']=='classify':
        from .split_merge import validate_classification
        validate_classification(read(task['output_files'][0]),read(task['input_files'][0]),Path(task['run_dir']))
    else:
        from .assemble import validate_output
        validate_output(Path(task['output_files'][0]).parent)
    base=state_dir(root)
    with lock(base/'registry.lock'):
        active=active_claims(root,base).get(task['cache_key'],{})
        if active.get('conflict'):raise ValueError('Conflicting claim requires reconciliation')
        if active.get('token')!=token:raise ValueError('Claim token mismatch')
        if active.get('descriptor_sha256')!=descriptor_hash(root,task):raise ValueError('Claimed model task descriptor changed')
        dest=base/'results'/task['cache_key'];dest.mkdir(parents=True,exist_ok=True)
        entries=[]
        for path in task['output_files']:
            p=Path(path);shutil.copyfile(p,dest/p.name);entries.append({'name':p.name,'sha256':digest(p.read_bytes())})
        receipt={'cache_key':task['cache_key'],'files':entries,'verified_at':now(),'model':model,'reasoning_effort':effort}
        write(dest/'receipt.json',receipt)
        run=Path(task['run_dir'])
        durable={**receipt,'files':[{**e,'path':str(Path(path).relative_to(Path(root)))} for e,path in zip(entries,task['output_files'])]}
        write(run/'run/task-receipts'/f"{task['cache_key']}.json",durable)
    attest_summary(task)
    release(root,task['cache_key'],token)

def reuse(root,task):
    dest=state_dir(root)/'results'/task['cache_key'];receipt=read(dest/'receipt.json')
    if not receipt:
        for file in Path(root).glob('data/runs/*/*/run/task-receipts/'+task['cache_key']+'.json'):
            candidate=read(file)
            if candidate.get('cache_key')!=task['cache_key']:continue
            files=candidate.get('files',[])
            paths=[(Path(root)/e['path']).resolve() for e in files]
            if any(not p.is_relative_to(Path(root).resolve()) for p in paths):raise ValueError('Unsafe durable receipt path')
            if any(not p.is_file() or digest(p.read_bytes())!=e['sha256'] for p,e in zip(paths,files)):continue
            dest.mkdir(parents=True,exist_ok=True)
            for p,e in zip(paths,files):shutil.copyfile(p,dest/e['name'])
            receipt=candidate;write(dest/'receipt.json',receipt);break
        if not receipt:return False
    for entry in receipt['files']:
        if digest((dest/entry['name']).read_bytes())!=entry['sha256']:raise ValueError('Cached result integrity failure')
    if len(task['output_files'])!=len(receipt['files']):raise ValueError('Cached output count mismatch')
    for entry,target in zip(receipt['files'],task['output_files']):
        p=Path(target);p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest/entry['name'],p)
    attest_summary(task)
    return True

def make_tasks(run,kind):
    run=Path(run).resolve();root=project_root(run);run_policy(run)
    tasks=[];blocked=0
    if kind=='classify':
        items=[run/p for p in read(run/'run/groups.json',[])]
    else:items=[paper_path(run,p) for p in read(run/'run/selected.json',[])]
    for item in items:
        try:
            if kind=='classify':
                data=validate_candidates(read(item));settings=agent_settings(run,data[0],kind);model=settings['model'];effort=settings['reasoning_effort'];template=relevance_template(run,data);output=item.with_name(item.stem+'.result.json'); inputs=[item];outputs=[output];prompt=expand_prompt(run,data,item,output); fingerprint=[{k:p[k] for k in ('id','version','title','abstract')} for p in data]
            else:
                source=read(item/'source.json');settings=agent_settings(run,source,kind);model=settings['model'];effort=settings['reasoning_effort'];template=summary_template(run,source)
                from .split_merge import validate_current_selection
                validate_current_selection(run,source)
                if not source or source.get('verified') is not True:raise ValueError('No verified same-version source')
                expected_files={source['local_file']:source['sha256']}
                if source['kind']=='html':expected_files['fulltext.html.txt']=source.get('fulltext_sha256')
                for name,sha in expected_files.items():
                    path=(item/name).resolve()
                    if not path.is_relative_to(item.resolve()):raise ValueError('Prepared input path escapes paper directory')
                    if path.is_file() and (not sha or digest(path.read_bytes())!=sha):raise ValueError('Prepared source/figure hash changed: '+name)
                inputs=[item/'source.json',item/'metadata.json',item/source['local_file']]
                if source['kind']=='html':inputs.append(item/'fulltext.html.txt')
                outputs=[item/'metadata.json',item/'result.json',item/'summary.md'];prompt=template.replace('{paper_dir}',str(item))
                fingerprint=summary_fingerprint(item)
            for p in outputs:p.parent.mkdir(parents=True,exist_ok=True)
            cache_key=digest({'kind':kind,'input':fingerprint,'prompt':template,'model':model,'reasoning_effort':effort})
            target_keys=[digest({'kind':kind,'paper':p,'prompt':template,'model':model,'effort':effort}) for p in fingerprint] if kind=='classify' else [cache_key]
            task={'target_keys':target_keys,'task_name':kind+'_'+cache_key[:16],'kind':kind,'cache_key':cache_key,'model':model,'reasoning_effort':effort,'independent_context':True,'run_dir':str(run),'prompt':prompt,'input_fingerprint':fingerprint,'input_digests':{str(p.resolve()):digest(p.read_bytes()) for p in inputs if p.is_file() and not (kind=='summary' and p.name=='metadata.json')},'input_files':[str(p.resolve()) for p in inputs],'output_files':[str(p.resolve()) for p in outputs]}
            if kind=='summary':write(item/'model-request.json',{'cache_key':cache_key,'input_hash':digest(fingerprint),'prompt_hash':digest(template.encode()),'metadata_input_hash':fingerprint['metadata_sha256']})
            if reuse(root,task):record(run,kind,'reused',{'cache_key':cache_key},task['task_name'])
            else:
                if any(not p.is_file() for p in inputs):raise ValueError('Ephemeral inputs missing; run prepare again')
                tasks.append(task)
        except Exception as exc:
            blocked+=1;record(run,kind+'_tasks','blocked',{'error':str(exc)},item.name)
    write(run/'run'/('classifier_tasks.json' if kind=='classify' else 'summary_tasks.json'),tasks)
    record(run,kind+'_tasks',('partial' if tasks else 'blocked') if blocked else ('awaiting_external_model' if tasks else 'complete'),{'task_count':len(tasks),'blocked_count':blocked,'model_executed':False})
    return tasks
