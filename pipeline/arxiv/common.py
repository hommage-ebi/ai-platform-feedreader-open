"""arXiv identities, candidate validation, completion and publication evidence.

Stages import source-neutral storage/runtime helpers directly from shared.
"""
from __future__ import annotations
import datetime as dt
import re
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pipeline.shared.runtime import digest, lock, now, project_root, read, write

ID_RE = re.compile(r'(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})\Z')
VERSION_RE = re.compile(r'v[1-9]\d*\Z')

def today(): return dt.datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat()

def identity(p):
    if not isinstance(p,dict) or not ID_RE.fullmatch(p.get('id','')) or not VERSION_RE.fullmatch(p.get('version','')): raise ValueError('Invalid paper ID/version')
    return p['id'],p['version']
def paper_path(run,p): return Path(run)/'run'/'papers'/quote(identity(p)[0],safe='')/p['version']
def validate_candidates(rows):
    if not isinstance(rows,list): raise ValueError('Candidates must be array')
    seen=set()
    for p in rows:
        key=identity(p)
        if key in seen: raise ValueError('Duplicate ID/version')
        seen.add(key)
        from .inputs import input_hash
        input_hash(p)
    return rows

def protected_versions(run):
    """Publication evidence is global; imports alone do not freeze drafts.

    Pending deployments are protected until reconciled. Keep successful historical
    records, even if a later deployment fails or originates in another run.
    """
    root=project_root(run); protected={}
    paths=set(root.glob('data/runs/*/*/release.json'))
    paths.update(root.glob('data/releases/*.json'))
    for path in paths:
        release=read(path,{})
        status=release.get('status',release.get('deployment_status'))
        if status not in ('succeeded','success','published','verification_pending'):continue
        for article in release.get('articles',[]):
            key=(article.get('article_id',article.get('id')),article.get('version'))
            sha=article.get('content_sha256')
            if not sha:raise ValueError('Publication evidence lacks a content hash')
            protected.setdefault(key,set()).add(sha)
    return protected

def published_versions(run):
    """Published versions stay immutable across later prompt revisions."""
    run=Path(run);release=read(run/'release.json',{})
    if release.get('status',release.get('deployment_status')) not in ('succeeded','success','published'):return {}
    deployed={(x.get('article_id',x.get('id')),x.get('version')):x.get('content_sha256') for x in release.get('articles',[])}
    accepted={}
    for base in (run/'site',project_root(run)/'data/articles'):
        for record in read(base/'papers.json',[]):
            for article in record.get('versionHistory',[record]):
                key=(article.get('id',record.get('id')),article.get('version'))
                if key not in deployed:continue
                sha=article.get('content_sha256');path=(base/article.get('html_path','')).resolve()
                if sha and deployed.get(key)==sha and path.is_relative_to(base.resolve()) and path.is_file() and digest(path.read_bytes())==sha:accepted[key]=article
    return accepted

def recompute_completion(run):
    """Derive terminal state from current inputs and immutable publication evidence."""
    from .split_merge import input_hash, prompt_hash, validate_classification, classification_proof
    from .assemble import validate_output,generation_identity
    run=Path(run)
    with lock(run/'.ledger.lock'):
        ledger=read(run/'ledger.json',{})
        if not (run/'run/candidates.json').is_file():
            ledger['status']='incomplete';ledger['pending']=[{'stage':'collect','error':'Saved candidates unavailable; cannot reconstruct historical scope'}];ledger['updated_at']=now();write(run/'ledger.json',ledger);return 'incomplete'
        candidates=validate_candidates(read(run/'run/candidates.json'))
        classified={identity(x):x for x in read(run/'run/classifications.json',[])}
        proofs=read(run/'run/classification-proofs.json',{})
        stages=ledger.get('stages',{});pending=[];selected=0
        frozen=published_versions(run)
        assembled={};assembly_bases={};candidate_keys={identity(p) for p in candidates}
        for base in (project_root(run)/'data/articles',run/'site'):
            for record_data in read(base/'papers.json',[]):
                for article in record_data.get('versionHistory',[record_data]):
                    key=(article.get('id',record_data.get('id')),article.get('version'))
                    if key in candidate_keys:assembled[key]=article;assembly_bases[key]=base
        release=read(run/'release.json',{})
        published=release.get('status',release.get('deployment_status')) in ('succeeded','success','published')
        deployed={(x.get('article_id',x.get('id')),x.get('version')):x for x in release.get('articles',[])} if published else {}
        for paper in candidates:
            key=identity(paper);result=classified.get(key)
            if key in frozen:
                selected+=1
                continue
            try:
                if proofs.get('/'.join(key))!=classification_proof(paper,result,prompt_hash(run,paper),run):raise ValueError('Classification input or prompt changed')
                validate_classification([result],[paper],run)
            except (ValueError,TypeError,KeyError):
                pending.append({'id':key[0],'version':key[1],'stage':'classify'});continue
            if result['decision']=='exclude':continue
            selected+=1
            try:
                output,meta,source,summary,figures=validate_output(paper_path(run,paper),require_provenance=True)
                generation=generation_identity(output,meta,source,summary,figures,result['topics'])
                article=assembled.get(key,{})
                base=assembly_bases.get(key,run/'site');path=(base/article.get('html_path','')).resolve()
                if article.get('generation_hash')!=generation or not path.is_relative_to(base.resolve()) or not path.is_file() or digest(path.read_bytes())!=article.get('content_sha256'):raise ValueError('Assembly missing or stale')
                if stages.get('assemble:'+'/'.join(key),{}).get('status')!='complete':raise ValueError('Assembly did not complete')
            except (ValueError,TypeError,KeyError,OSError):
                pending.append({'id':key[0],'version':key[1],'stage':'assemble'});continue
            if deployed.get(key,{}).get('content_sha256')!=article['content_sha256']:
                pending.append({'id':key[0],'version':key[1],'stage':'publication'})
        collected=stages.get('collect',{}).get('status')=='complete'
        capture_hash=stages.get('collect',{}).get('details',{}).get('capture_sha256')
        if capture_hash:
            capture=read(run/'run/collection-capture.json',{})
            collected=collected and digest(capture)==capture_hash and capture.get('candidates_sha256')==digest(candidates) and capture.get('target_date')==ledger.get('target_date')
        if not collected:pending.append({'stage':'collect','error':'Verified collection coverage is missing or invalid; inspect collect and collect_attempt'})
        adapter_ok=not selected or stages.get('adapter',{}).get('status')=='complete'
        ledger['pending']=pending
        ledger['status']='complete' if collected and not pending and adapter_ok and (not selected or published) else 'incomplete'
        ledger['updated_at']=now();write(run/'ledger.json',ledger)
        return ledger['status']
