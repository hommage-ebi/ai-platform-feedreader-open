"""Group arXiv classification work and merge results with current policy proofs."""
from pathlib import Path
from pipeline.shared.runtime import digest, project_root, read, record, write
from .common import identity, published_versions, validate_candidates
from .inputs import input_hash,compact,compact_run,model_input
from .policy import run_policy,paper_policy,bind_papers,relevance_template,validate_bindings,agent_settings

def validate_classification(rows,inputs,run=None):
    validate_candidates(inputs)
    if not isinstance(rows,list) or len(rows)!=len(inputs):raise ValueError('Classification count mismatch')
    for r,p in zip(rows,inputs):
        allowed={item['id'] for item in paper_policy(run,p)['interests']['interests']}
        if set(r)!={'id','version','decision','topics','reason'} or identity(r)!=identity(p):raise ValueError('Classification identity/keys/order mismatch')
        decision=r['decision']; topics=r['topics']
        if decision not in ['include','exclude','uncertain']:raise ValueError('Invalid decision')
        if not isinstance(topics,list) or any(not isinstance(t,str) or t not in allowed for t in topics) or len(set(topics))!=len(topics):raise ValueError('Invalid topics')
        if bool(topics)!=(decision!='exclude') or not isinstance(r['reason'],str) or not r['reason'].strip():raise ValueError('Invalid classification evidence')
    return rows

def prompt_hash(run,paper=None):
    return digest(relevance_template(run,[paper] if paper else []).encode())


def classification_proof(p,result,ph,run):
    settings=agent_settings(run,p,'classify')
    return {'input_hash':input_hash(p),'prompt_hash':ph,'result_hash':digest(result),'model':settings['model'],'reasoning_effort':settings['reasoning_effort']}

def validate_current_selection(run,paper):
    """Summary reuse/execution requires current interest eligibility, not stale selected JSON."""
    run=Path(run);ph=prompt_hash(run,paper);key=identity(paper)
    if key in published_versions(run):return
    candidates={identity(p):p for p in validate_candidates(read(run/'run/candidates.json'))}
    results={identity(r):r for r in read(run/'run/classifications.json',[])}
    candidate=candidates.get(key);result=results.get(key)
    if candidate is None or result is None:raise ValueError('Current classification is missing; classify before summary work')
    validate_classification([result],[candidate],run)
    if read(run/'run/classification-proofs.json',{}).get('/'.join(key))!=classification_proof(candidate,result,ph,run):raise ValueError('Current interest/input classification proof changed; reclassify before summary work')
    selected={identity(p):p for p in read(run/'run/selected.json',[])}
    if result['decision']=='exclude' or selected.get(key)!={**candidate,**result}:raise ValueError('Current selection is missing or stale')

def split(run):
    run=Path(run);run_policy(run);validate_bindings(run);rows=compact_run(run);results=read(run/'run/classifications.json',[])
    proofs=read(run/'run/classification-proofs.json',{});run_policy(run);validate_bindings(run)
    eligible=[]
    for p in rows:
        try:
            bind_papers(run,[p]);eligible.append(p)
            record(run,'policy','complete',{},'/'.join(identity(p)))
        except (ValueError,TypeError,KeyError) as exc:record(run,'policy','blocked',{'error':str(exc)},'/'.join(identity(p)))
    rows=eligible
    indexed={identity(r):r for r in results};frozen=published_versions(run)
    for p in rows:
        ph=prompt_hash(run,p);key='/'.join(identity(p));current=indexed.get(identity(p))
        if identity(p) in frozen or current and proofs.get(key)==classification_proof(p,current,ph,run):continue
        for path in project_root(run).glob('data/runs/*/*/run/classification-proofs.json'):
            if path.parent==run/'run':continue
            try:
                old=next((r for r in read(path.parent/'classifications.json',[]) if identity(r)==identity(p)),None)
                if old and read(path,{}).get(key)==classification_proof(p,old,ph,run):
                    validate_classification([old],[p],run);indexed[identity(p)]=old;proofs[key]=classification_proof(p,old,ph,run)
                    record(run,'classify','reused',{'from':str(path.relative_to(project_root(run))),'input_hash':input_hash(p),'prompt_hash':ph},key);break
            except (ValueError,TypeError,KeyError,OSError) as exc:
                record(run,'classification_reuse','blocked',{'source':str(path.relative_to(project_root(run))),'error':str(exc)},key)
    results=list(indexed.values());write(run/'run/classifications.json',results);write(run/'run/classification-proofs.json',proofs)
    done={identity(r) for r in results}
    rows=[p for p in rows if identity(p) not in frozen and (identity(p) not in done or proofs.get('/'.join(identity(p)))!=classification_proof(p,indexed.get(identity(p)),prompt_hash(run,p),run))]
    partitions={}
    for p in rows:partitions.setdefault(paper_policy(run,p)['sha256'],[]).append(p)
    groups=[]
    for values in partitions.values():
        n=min(run_policy(run)['agents']['max_parallel'],len(values));groups.extend(values[i::n] for i in range(n))
    paths=[]
    for group in groups:
        ph=prompt_hash(run,group[0])
        group=[model_input(run,p) for p in group]
        path=run/'.cache/groups'/f'{digest({"papers":group,"prompt_hash":ph})}.json';write(path,group);write(path.with_name(path.stem+'.request.json'),{'input_hash':digest(group),'prompt_hash':ph});paths.append(str(path.relative_to(run)))
    write(run/'run/groups.json',paths);record(run,'split','complete',{'groups':len(paths),'pending':len(rows)})
    return paths

def merge(run):
    run=Path(run);proofs=read(run/'run/classification-proofs.json',{});candidate_map={identity(p):p for p in validate_candidates(read(run/'run/candidates.json',[]))}
    frozen=published_versions(run)
    existing={}
    for r in read(run/'run/classifications.json',[]):
        try:
            p=candidate_map[identity(r)]
            if identity(r) in frozen or proofs.get('/'.join(identity(r)))==classification_proof(p,r,prompt_hash(run,p),run):existing[identity(r)]=r
        except (ValueError,TypeError,KeyError):continue
    for rel in read(run/'run/groups.json',[]):
        path=run/rel;out=path.with_name(path.stem+'.result.json')
        try:
            inputs=read(path);ph=prompt_hash(run,inputs[0])
            if read(path.with_name(path.stem+'.request.json'))!={'input_hash':digest(inputs),'prompt_hash':ph}:raise ValueError('Stale classification prompt/input')
            if any(identity(p) not in candidate_map or input_hash(p)!=input_hash(candidate_map[identity(p)]) for p in inputs):raise ValueError('Classification group differs from current candidates')
            settings=agent_settings(run,inputs[0],'classify')
            fingerprint=[{k:p[k] for k in ('id','version','title','abstract')} for p in inputs]
            template=relevance_template(run,inputs)
            expected=digest({'kind':'classify','input':fingerprint,'prompt':template,'model':settings['model'],'reasoning_effort':settings['reasoning_effort']})
            receipt=read(out.with_suffix('.receipt.json'),{})
            if receipt.get('cache_key')!=expected or receipt.get('output_sha256')!=digest(out.read_bytes()) or receipt.get('model')!=settings['model'] or receipt.get('reasoning_effort')!=settings['reasoning_effort']:raise ValueError('Classification lacks matching finish receipt; run finish after model execution')
            rows=validate_classification(read(out),inputs,run)
            for r,p in zip(rows,inputs):
                if identity(r) in frozen:continue
                existing[identity(r)]=r;proofs['/'.join(identity(r))]=classification_proof(p,r,ph,run)
            record(run,'classify','complete',{'input_hash':digest(read(path))},path.stem)
        except Exception as e:record(run,'classify','failed',{'error':str(e)},path.stem)
    write(run/'run/classifications.json',list(existing.values()));write(run/'run/classification-proofs.json',proofs)
    candidates=validate_candidates(read(run/'run/candidates.json',[])); selected=[]
    for p in candidates:
        result=existing.get(identity(p))
        if result and result['decision'] in ('include','uncertain'):selected.append({**compact(p),**result})
    write(run/'run/selected.json',selected);return selected
