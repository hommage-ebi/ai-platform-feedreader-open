"""Durable first-capture policies. Editing defaults only affects new work."""
from pathlib import Path
import json,re,hashlib
from pipeline.shared.runtime import digest, lock, project_root, read, write
from .common import identity, validate_candidates
from pipeline.shared.interests import load_interests,validate_interests,render_relevance
from pipeline.shared.agents import load_agents,validate_agents

def load_sources(root):
    from pipeline.shared.interests import unique_object
    try:value=json.loads((Path(root)/'pipeline/arxiv/config/sources.json').read_text(),object_pairs_hook=unique_object)
    except (OSError,ValueError) as exc:raise ValueError('Cannot load RSS sources configuration: '+str(exc)) from exc
    return validate_sources(value)

def validate_sources(value):
    if not isinstance(value,dict) or set(value)!={'schema_version','categories'} or type(value['schema_version']) is not int or value['schema_version']!=1:raise ValueError('Invalid RSS sources schema')
    rows=value['categories']
    if not isinstance(rows,list) or not rows or any(not isinstance(x,str) or not re.fullmatch(r'[a-z][a-z-]*(?:\.[A-Za-z][A-Za-z0-9-]*)?',x) for x in rows) or len(set(rows))!=len(rows):raise ValueError('RSS categories must be unique nonempty arXiv category identifiers')
    return value

def validate_policy(value):
    if not isinstance(value,dict) or set(value)!={'schema_version','interests','sources','agents','relevance','summary','sha256'} or type(value['schema_version']) is not int or value['schema_version']!=1:raise ValueError('Invalid processing policy snapshot')
    if value['sha256']!=digest({k:v for k,v in value.items() if k!='sha256'}):raise ValueError('Processing policy snapshot hash mismatch')
    validate_interests(value['interests']);validate_sources(value['sources']);validate_agents(value['agents'])
    if not isinstance(value['summary'],str) or not value['summary'].strip() or value['summary'].count('{paper_dir}')!=1:raise ValueError('Invalid summary policy template')
    render_relevance(value['relevance'],value['interests'])
    return value

def run_policy(run):
    run=Path(run);path=run/'run/processing-policy.json'
    if path.exists():return validate_policy(read(path))
    with lock(run/'.policy.lock'):
        if path.exists():return validate_policy(read(path))
        if (run/'run/paper-policies.json').exists() or read(run/'ledger.json',{}).get('stages',{}).get('collect') or read(run/'run/classification-proofs.json',{}) or list((run/'run/papers').glob('*/*/model-output-receipt.json')):raise ValueError('Historical processing policy unavailable; recover original policy before resuming affected work')
        root=project_root(run)
        value={'schema_version':1,'interests':load_interests(root),'sources':load_sources(root),'agents':load_agents(root),'relevance':(root/'pipeline/arxiv/prompts/relevance.txt').read_text(),'summary':(root/'pipeline/arxiv/prompts/summary.txt').read_text()}
        value['sha256']=digest(value);validate_policy(value);write(path,value)
        return value

from .inputs import input_hash as fingerprint

def binding_policy(run,entry):
    # Legacy embedded snapshots remain readable and migrate on the next binding write.
    if 'policy' in entry:return validate_policy(entry['policy'])
    sha=entry['policy_sha256']
    current=validate_policy(read(Path(run)/'run/processing-policy.json'))
    if current['sha256']==sha:return current
    value=validate_policy(read(Path(run)/'run/policies'/f'{sha}.json'))
    if value['sha256']!=sha:raise ValueError('Processing policy reference hash mismatch')
    return value

def policy_reference(run,value):
    value=validate_policy(value);current=run_policy(run)
    if value['sha256']!=current['sha256']:write(Path(run)/'run/policies'/f"{value['sha256']}.json",value)
    return value['sha256']

_binding_cache={}

def _binding_snapshot(run):
    """Include the bytes of every referenced policy, not just file mtimes."""
    base=Path(run)/'run';path=base/'paper-policies.json'
    raw=path.read_bytes() if path.exists() else b'{}'
    bindings=json.loads(raw)
    if not isinstance(bindings,dict):raise ValueError('Invalid paper policy bindings')
    refs=sorted({entry['policy_sha256'] for entry in bindings.values()
                 if isinstance(entry,dict) and isinstance(entry.get('policy_sha256'),str)
                 and re.fullmatch('[0-9a-f]{64}',entry['policy_sha256'])})
    paths=[base/'processing-policy.json',*(base/'policies'/f'{sha}.json' for sha in refs)]
    h=hashlib.sha256(raw)
    for path in paths:
        h.update(str(path).encode());h.update(b'\0')
        h.update(path.read_bytes() if path.exists() else b'<missing>')
    return h.digest(),raw,bindings

def validate_bindings(run):
    cache_key=str(Path(run).resolve())
    signature,raw,bindings=_binding_snapshot(run)
    if _binding_cache.get(cache_key)==signature:
        return bindings  # freshly decoded: callers may mutate it
    if not isinstance(bindings,dict):raise ValueError('Invalid paper policy bindings')
    for key,entry in bindings.items():
        if not isinstance(key,str) or not isinstance(entry,dict) or set(entry) not in ({'input_sha256','policy'},{'input_sha256','policy_sha256'}) or not isinstance(entry['input_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',entry['input_sha256']):raise ValueError('Invalid paper policy binding')
        if 'policy_sha256' in entry and (not isinstance(entry['policy_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',entry['policy_sha256'])):raise ValueError('Invalid processing policy reference')
        binding_policy(run,entry)
    if _binding_snapshot(run)[0]!=signature:raise ValueError('Processing policies changed during validation')
    _binding_cache[cache_key]=signature
    return bindings

def bind_papers(run,papers):
    run=Path(run);root=project_root(run);current=run_policy(run)
    with lock(root/'data/runs/.paper-policy.lock'):
        path=run/'run/paper-policies.json';bindings=validate_bindings(run)
        if not isinstance(bindings,dict):raise ValueError('Invalid paper policy bindings')
        for paper in papers:
            key='/'.join(identity(paper));fp=fingerprint(paper)
            if key in bindings:
                entry=bindings[key];binding_policy(run,entry)
                if entry['input_sha256']!=fp:raise ValueError('Changed input for identical version')
                continue
            chosen=None
            histories=sorted({p.parent for pattern in ('data/runs/*/*/run/candidates.json','data/runs/*/*/run/paper-policies.json') for p in root.glob(pattern)})
            for old in histories:
                if old==run/'run':continue
                try:
                    mapping=read(old/'paper-policies.json',{})
                    if not isinstance(mapping,dict):raise ValueError('Invalid historical paper policy bindings')
                except (ValueError,TypeError):
                    rows=validate_candidates(read(old/'candidates.json'))
                    if all(identity(p)!=identity(paper) for p in rows):continue
                    raise ValueError('Historical paper policy unavailable for '+key)
                if key in mapping:
                    entry=mapping[key];binding_policy(old.parent,entry)
                    if entry['input_sha256']!=fp:raise ValueError('Changed input for identical version in historical run')
                    candidate=binding_policy(old.parent,entry)
                else:
                    try:rows=validate_candidates(read(old/'candidates.json',[]))
                    except (ValueError,TypeError):continue
                    prior=next((p for p in rows if identity(p)==identity(paper)),None)
                    if prior is None:continue
                    if fingerprint(prior)!=fp:raise ValueError('Changed input for identical version in historical run')
                    if not (old/'processing-policy.json').exists():raise ValueError('Historical processing policy unavailable for '+key)
                    candidate=validate_policy(read(old/'processing-policy.json'))
                if chosen is not None and chosen['sha256']!=candidate['sha256']:raise ValueError('Conflicting historical paper policies for '+key)
                chosen=candidate
            bindings[key]={'input_sha256':fp,'policy_sha256':policy_reference(run,chosen or current)}
        bindings={key:{'input_sha256':entry['input_sha256'],'policy_sha256':policy_reference(run,binding_policy(run,entry))} for key,entry in bindings.items()}
        write(path,bindings)
    return bindings

def paper_policy(run,paper):
    run=Path(run);key='/'.join(identity(paper));bindings=validate_bindings(run)
    if key not in bindings:
        candidate=next((p for p in validate_candidates(read(run/'run/candidates.json',[])) if identity(p)==identity(paper)),None)
        if candidate is None:raise ValueError('Paper has no captured input for processing policy')
        bindings=bind_papers(run,[candidate])
    entry=bindings[key];value=binding_policy(run,entry)
    candidate=next((p for p in validate_candidates(read(run/'run/candidates.json',[])) if identity(p)==identity(paper)),None)
    if candidate is None or entry['input_sha256']!=fingerprint(candidate):raise ValueError('Paper policy input mismatch with current candidates')
    return value

def group_policy(run,papers):
    if not papers:return run_policy(run)
    values=[paper_policy(run,p) for p in papers]
    if any(p['sha256']!=values[0]['sha256'] for p in values):raise ValueError('Classification group mixes processing policies')
    return values[0]

def relevance_template(run,papers=None):
    value=group_policy(run,papers or [])
    return render_relevance(value['relevance'],value['interests'])

def expand_prompt(run,papers,input_file,output_file):
    value=group_policy(run,papers)
    return render_relevance(value['relevance'],value['interests'],input_file,output_file)

def summary_template(run,paper):return paper_policy(run,paper)['summary']

def agent_settings(run,paper,kind):return paper_policy(run,paper)['agents'][kind]
