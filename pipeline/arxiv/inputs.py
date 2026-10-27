"""Compact durable candidates; title/abstract live only in disposable model inputs."""
from pathlib import Path
from pipeline.shared.runtime import digest, lock, read, write
from .common import identity, validate_candidates

FIELDS=('id','version','title','abstract')

def input_hash(paper):
    if 'title' in paper or 'abstract' in paper:
        if any(not isinstance(paper.get(k),str) or not paper[k].strip() for k in FIELDS):raise ValueError('Missing title/abstract')
        value=digest({k:paper[k] for k in FIELDS})
        if paper.get('input_sha256',value)!=value:raise ValueError('Candidate input hash mismatch')
        return value
    value=paper.get('input_sha256')
    import re
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Missing title/abstract or input hash')
    return value

def compact(paper):
    return {**{k:v for k,v in paper.items() if k not in ('title','abstract')},'input_sha256':input_hash(paper)}

def pool_path(run):return Path(run)/'.cache/classification-inputs.json'

def remember(run,papers):
    """Keep one transient source pool; never embed text in durable candidate rows."""
    with lock(Path(run)/'.inputs.lock'):
        pool=read(pool_path(run),{})
        for p in papers:
            if 'title' in p and 'abstract' in p:pool[input_hash(p)]={k:p[k] for k in FIELDS}
        write(pool_path(run),pool)

def model_input(run,paper):
    if 'title' in paper and 'abstract' in paper:return {k:paper[k] for k in FIELDS}
    value=read(pool_path(run),{}).get(input_hash(paper))
    if value is None:raise ValueError('Temporary classification input unavailable; restore original title/abstract for this ID/version; do not substitute current RSS')
    if identity(value)!=identity(paper) or input_hash(value)!=input_hash(paper):raise ValueError('Temporary classification input hash mismatch')
    return value

def compact_run(run):
    """Migrate older verbose candidate/selected records without weakening coverage."""
    run=Path(run);path=run/'run/candidates.json'
    with lock(run/'.candidates.lock'):
        rows=validate_candidates(read(path,[]));remember(run,rows);lean=[compact(p) for p in rows]
        if rows!=lean:
            capture=read(run/'run/collection-capture.json',{})
            with lock(run/'.ledger.lock'):
                ledger=read(run/'ledger.json',{});details=ledger.get('stages',{}).get('collect',{}).get('details',{})
                if capture.get('candidates_sha256')==digest(rows) and details.get('capture_sha256')==digest(capture):
                    capture['candidates_sha256']=digest(lean);write(run/'run/collection-capture.json',capture)
                    details['capture_sha256']=digest(capture);write(run/'ledger.json',ledger)
            write(path,lean)
        selected=read(run/'run/selected.json')
        if selected is not None:write(run/'run/selected.json',[compact(p) for p in selected])
    return lean

def cleanup_inputs(run):
    import shutil
    run=Path(run)
    if any(v.get('status')=='running' for v in read(run/'ledger.json',{}).get('model_claims',{}).values()):raise ValueError('Cannot clean inputs while a model task is running')
    rows=compact_run(run)
    from .policy import bind_papers
    bind_papers(run,rows)
    from .split_merge import classification_proof,prompt_hash
    classified={identity(p):p for p in read(run/'run/classifications.json',[])};proofs=read(run/'run/classification-proofs.json',{})
    pending=set()
    for p in rows:
        try:done=proofs.get('/'.join(identity(p)))==classification_proof(p,classified[identity(p)],prompt_hash(run,p),run)
        except (ValueError,KeyError,TypeError):done=False
        if not done:pending.add(input_hash(p))
    with lock(run/'.inputs.lock'):
        pool=read(pool_path(run),{});retained={k:v for k,v in pool.items() if k in pending}
        if retained:write(pool_path(run),retained)
        else:pool_path(run).unlink(missing_ok=True)
    # Merged classifications/proofs are the durable evidence; group files are disposable.
    for directory in (run/'run/groups',run/'.cache/groups'):shutil.rmtree(directory,ignore_errors=True)
    write(run/'run/groups.json',[])
    for name in ('classifier_tasks.json','summary_tasks.json'):(run/'run'/name).unlink(missing_ok=True)
