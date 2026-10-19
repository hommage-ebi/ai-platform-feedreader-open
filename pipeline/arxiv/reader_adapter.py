"""Validate arXiv generations and merge their versions into the Reader collection.

Shared collection helpers own locking and crash recovery; arXiv owns eligibility,
publication protection, version ordering, generation ownership and batch records.
"""
import argparse,copy
from urllib.parse import quote
from pathlib import Path
# Direct-file CLI remains usable from a run directory; imports stay package-scoped.
if __package__ in (None, ''):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = 'pipeline.arxiv'

from pipeline.shared.collection import collection_lock, commit_collection, recover_collection
from pipeline.shared.runtime import digest, lock, now, project_root, read, record
from .common import identity, paper_path, protected_versions, recompute_completion, validate_candidates

def eligible_article(run,article,candidates,protected):
    """Validate the current generation at the import boundary, not just its bytes."""
    from .split_merge import classification_proof,prompt_hash,validate_classification
    from .assemble import validate_output,generation_identity
    key=identity(article)
    if article.get('content_sha256') in protected.get(key,set()):return
    paper=candidates.get(key)
    if paper is None:raise ValueError('Article is not a current candidate')
    results={identity(x):x for x in read(run/'run/classifications.json',[])}
    result=results.get(key)
    validate_classification([result],[paper],run)
    proof=read(run/'run/classification-proofs.json',{}).get('/'.join(key))
    if proof!=classification_proof(paper,result,prompt_hash(run,paper),run):raise ValueError('Classification input or prompt changed')
    if result['decision']=='exclude':raise ValueError('Current classification excludes article')
    selected={identity(x):x for x in read(run/'run/selected.json',[])}
    if selected.get(key)!={**paper,**result}:raise ValueError('Current selection is missing or stale')
    output,meta,source,summary,figures=validate_output(paper_path(run,paper),require_provenance=True)
    generation=generation_identity(output,meta,source,summary,figures,result['topics'])
    stage=read(run/'ledger.json',{}).get('stages',{}).get('assemble:'+'/'.join(key),{})
    if article.get('generation_hash')!=generation or stage.get('status')!='complete' or stage.get('details',{}).get('content_sha256')!=article.get('content_sha256'):
        raise ValueError('Assembly is missing or stale')

def validate_run_state(run):
    """Missing durable inputs are unknown state, never evidence of exclusion."""
    required={'ledger.json':dict,'run/candidates.json':list,'run/classifications.json':list,
              'run/classification-proofs.json':dict,'run/selected.json':list}
    records={}
    for name,kind in required.items():
        value=read(run/name)
        if not isinstance(value,kind):raise ValueError('Missing or invalid required run record: '+name)
        records[name]=value
    validate_candidates(records['run/candidates.json'])
    ledger=records['ledger.json']
    if not isinstance(ledger.get('stages'),dict) or not ledger.get('target_date') or not ledger.get('run_id'):
        raise ValueError('Invalid run ledger identity/stages')
    for name in ('run/classifications.json','run/selected.json'):
        keys=[identity(value) for value in records[name]]
        if len(set(keys))!=len(keys):raise ValueError('Duplicate run record identity: '+name)
    return records

def generation_owners(version):
    owners=version.get('generation_runs')
    if owners is None:owners=[version['generation_run']] if version.get('generation_run') else []
    if not isinstance(owners,list) or any(not isinstance(x,str) or not x for x in owners):
        raise ValueError('Invalid generation ownership')
    return set(owners)

def set_generation_owners(version,owners):
    version['generation_runs']=sorted(owners)
    if owners:version['generation_run']=sorted(owners)[0]
    else:version.pop('generation_run',None)

def adapt(site,run,articles_dir):
    run=Path(run)
    from .policy import run_policy,validate_bindings,fingerprint
    with lock(run/'.run.lock',blocking=False):
        validate_run_state(run)
        candidates=validate_candidates(read(run/'run/candidates.json'))
        has_policy=(run/'run/processing-policy.json').exists() or (run/'run/paper-policies.json').exists()
        if has_policy or any(identity(p) not in protected_versions(run) for p in candidates):
            run_policy(run)
            bindings=validate_bindings(run)
            incoming_keys={identity(p) for p in read(Path(site)/'papers.json',[])}
            stages=read(run/'ledger.json')['stages']
            for paper in candidates:
                key='/'.join(identity(paper));entry=bindings.get(key)
                if entry is None and identity(paper) not in incoming_keys and stages.get('policy:'+key,{}).get('status')=='blocked':continue
                if entry is None or entry['input_sha256']!=fingerprint(paper):raise ValueError('Captured paper policy binding is missing or changed')
        return adapt_locked(Path(site),run,Path(articles_dir))

def adapt_locked(site,run,dest):
    dest.mkdir(parents=True,exist_ok=True)
    added=[];failures=[]
    with collection_lock(dest,run), lock(dest/'.merge.lock'):
        recover_collection(dest)
        incoming=read(site/'papers.json')
        if not isinstance(incoming,list):raise ValueError('Missing or invalid site completion index; run assemble before import')
        keys=[identity(article) for article in incoming]
        if len(set(keys))!=len(keys):raise ValueError('Duplicate site article identity')
        candidates={identity(x):x for x in validate_candidates(read(run/'run/candidates.json',[]))}
        protected=protected_versions(run)
        has_papers=(dest/'papers.json').is_file();has_manifest=(dest/'images-manifest.json').is_file()
        if has_papers!=has_manifest or (not has_papers and any((dest/name).exists() for name in ('papers','batch-history','batch-status.json'))):
            raise ValueError('Existing collection is incomplete; restore canonical records or recover its transaction')
        current=read(dest/'papers.json',[])
        if not isinstance(current,list):raise ValueError('Existing papers must be array')
        if has_manifest:
            existing_manifest=read(dest/'images-manifest.json')
            if not isinstance(existing_manifest,dict) or existing_manifest.get('schema_version')!=1 or not isinstance(existing_manifest.get('images'),list):raise ValueError('Invalid existing image manifest')
        run_identity=str(run.resolve().relative_to(project_root(run).resolve()))
        indexed={};files={};valid=[]
        previous={(v.get('id',a['id']),v['version']):v for a in current for v in a.get('versionHistory',[a])}
        # Retire only this run's unprotected drafts. Published bytes are retained;
        # unrelated articles are never removed by an empty/partial run.
        for old in current:
            history=copy.deepcopy(old.get('versionHistory',[old]))
            retained=[]
            for version in history:
                key=(version.get('id',old['id']),version.get('version'))
                owners=generation_owners(version)
                if run_identity in owners and not protected.get(key):
                    classifications={identity(x):x for x in read(run/'run/classifications.json')}
                    proofs=read(run/'run/classification-proofs.json')
                    if key not in candidates or key not in classifications or '/'.join(key) not in proofs:
                        raise ValueError('Owned draft state is incomplete; cannot infer exclusion')
                    owners.remove(run_identity)
                    if not owners:continue
                    set_generation_owners(version,owners)
                retained.append(version)
            if retained:
                head=next((x for x in retained if x.get('version')==old.get('version')),retained[0])
                indexed[old['id']]={**head,'versionHistory':retained}
        for article in incoming:
            try:
                key=identity(article);eligible_article(run,article,candidates,protected)
                rel=Path(article['html_path'])
                if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0]!='papers' or rel.suffix!='.html' or '\\' in str(rel):raise ValueError('Unsafe article path')
                if str(rel)!=f"papers/{quote(key[0],safe='')}/{key[1]}.html":raise ValueError('Article path does not match identity')
                data=(site/rel).read_bytes()
                if digest(data)!=article['content_sha256'] or b'class="narrative"' not in data:raise ValueError('Article HTML integrity mismatch')
                if protected.get(key) and article['content_sha256'] not in protected[key]:raise ValueError('Immutable published or pending version conflict')
                old=indexed.get(article['id']);history=copy.deepcopy(old.get('versionHistory',[]) if old else [])
                existing=next((v for v in history if v['version']==article['version']),None)
                if existing:
                    if existing.get('content_sha256')==article['content_sha256']:
                        owners=generation_owners(existing)|{run_identity}
                        if not protected.get(key):
                            if existing.get('generation_hash')!=article.get('generation_hash'):owners={run_identity}
                            # HTML may be unchanged while current classification
                            # tags change. Refresh unpublished record metadata.
                            refreshed=copy.deepcopy(article);refreshed.pop('versionHistory',None)
                            existing.clear();existing.update(refreshed)
                        set_generation_owners(existing,owners)
                        head=next(v for v in history if v['version']==old['version'])
                        indexed[article['id']]={**head,'versionHistory':history}
                        valid.append(article);continue
                    if protected.get(key):raise ValueError('Immutable published or pending version conflict')
                    # A verified new generation can replace an unprotected draft
                    # from another run. Failed work cannot retire another owner.
                    history=[v for v in history if v['version']!=article['version']]
                for image in article['stored_images']:
                    if (image['article_id'],image['version'])!=key:raise ValueError('Image article/version mismatch')
                    if not image.get('verified_at'):raise ValueError('Image storage is not verified')
                files[str(rel)]={'kind':'html','data':data.decode('utf-8')}
                version=copy.deepcopy(article);version.pop('versionHistory',None);set_generation_owners(version,{run_identity})
                history.append(version);history.sort(key=lambda v:int(v['version'][1:]),reverse=True)
                indexed[article['id']]={**history[0],'versionHistory':history}
                if previous.get(key,{}).get('content_sha256')!=article['content_sha256']:added.append(key)
                valid.append(article)
            except Exception as exc:
                failures.append({'id':article.get('id'),'version':article.get('version'),'error':str(exc)});record(run,'adapter','failed',{'error':str(exc)},str(article.get('id')))
        images={}
        for article in indexed.values():
            for version in article.get('versionHistory',[article]):
                for image in version.get('stored_images',[]):
                    key=(image['article_id'],image['version'],image['source_ref'])
                    if key in images and images[key]!=image:raise ValueError('Image manifest conflict')
                    images[key]=image
        files['images-manifest.json']={'kind':'json','data':{'schema_version':1,'images':list(images.values())}}
        files['papers.json']={'kind':'json','data':sorted(indexed.values(),key=lambda p:(p.get('date',''),p['id']),reverse=True)}
        valid_keys={identity(a) for a in valid}
        for a in indexed.values():
            for version in a.get('versionHistory',[a]):
                key=(version.get('id',a['id']),version['version'])
                if key in candidates and key not in valid_keys and version.get('content_sha256') in protected.get(key,set()):
                    valid.append(version);valid_keys.add(key)
        ledger=read(run/'ledger.json',{});candidates=read(run/'run/candidates.json',[]);classified=read(run/'run/classifications.json',[]);selected=read(run/'run/selected.json',[])
        stage_failures=sum(v.get('status') in ('failed','blocked') for v in ledger.get('stages',{}).values())
        valid_completed=sum(1 for a in valid if a['id'] in indexed and any(v['version']==a['version'] and v.get('content_sha256')==a.get('content_sha256') for v in indexed[a['id']]['versionHistory']))
        safe_scope=[{k:v for k,v in item.items() if k in ('url','status','count','retrieved_at')} for item in ledger.get('stages',{}).get('collect',{}).get('details',{}).get('scope',[])]
        batch={'collection_scope':safe_scope,'target_date':ledger.get('target_date'),'run_id':ledger.get('run_id'),'collection_status':ledger.get('stages',{}).get('collect',{}).get('status','not_attempted'),'candidate_count':len(candidates),'selected_count':len(selected),'uncertain_count':sum(x['decision']=='uncertain' for x in classified),'completed_count':valid_completed,'added_count':len(added),'published_count':0,'publication_status':'not_deployed','failed_count':stage_failures,'unclassified_count':max(0,len(candidates)-len(classified)),'updated_at':now()}
        batch['completed_articles']=[{'article_id':a['id'],'version':a['version'],'content_sha256':a['content_sha256']} for a in valid if a['id'] in indexed and any(v['version']==a['version'] and v.get('content_sha256')==a.get('content_sha256') for v in indexed[a['id']]['versionHistory'])]
        release=read(run/'release.json',{})
        released={(x.get('article_id',x.get('id')),x.get('version')):x.get('content_sha256') for x in release.get('articles',[])} if release.get('status')=='succeeded' else {}
        published_count=sum(1 for a in valid if released.get(identity(a))==a.get('content_sha256') and a.get('content_sha256'))
        batch['published_count']=published_count
        if release.get('status')=='succeeded':batch['publication_status']='succeeded' if published_count==valid_completed else ('partial' if published_count else 'not_deployed')
        elif release.get('status') in ('verification_pending','failed'):batch['publication_status']=release['status']
        name=f"{ledger.get('target_date','unknown')}-{ledger.get('run_id','unknown')}.json"
        if '/' in name or '..' in name:raise ValueError('Invalid batch identity')
        files['batch-history/'+name]={'kind':'json','data':batch};files['batch-status.json']={'kind':'json','data':batch}
        commit_collection(dest,files)
    record(run,'adapter','failed' if failures else 'complete',{'added_count':len(added),'failures':failures})
    recompute_completion(run)
    return {'added':added,'failures':failures}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--site',required=True);p.add_argument('--run',required=True);p.add_argument('--articles-dir',required=True);a=p.parse_args();run=Path(a.run).resolve();run=run.parent if run.name=='run' else run
    result=adapt(a.site,run,a.articles_dir);print(__import__('json').dumps(result));raise SystemExit(bool(result['failures']))
