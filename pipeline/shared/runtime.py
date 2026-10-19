"""Source-neutral durable JSON, hashes, locks, and repository discovery.

Source adapters own identities, candidate validation, policies and completion.
"""
from __future__ import annotations
import contextlib, datetime as dt, fcntl, hashlib, json, os, tempfile
from pathlib import Path

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(data):
    if not isinstance(data, bytes): data=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(data).hexdigest()
def read(path, default=None):
    p=Path(path)
    return json.loads(p.read_text()) if p.exists() else default

def write(path, data):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=p.parent,prefix='.'+p.name)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(data,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

@contextlib.contextmanager
def lock(path, blocking=True):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try: yield
        finally: fcntl.flock(f,fcntl.LOCK_UN)

def record(run,stage,status,details=None,key=None):
    run=Path(run)
    with lock(run/'.ledger.lock'):
        ledger=read(run/'ledger.json',{})
        ledger.setdefault('stages',{})[stage+(':'+key if key else '')]={'status':status,'at':now(),'details':details or {}}
        ledger['updated_at']=now(); write(run/'ledger.json',ledger)
        if status in ('failed','blocked'):
            with (run/'failures.jsonl').open('a') as f:f.write(json.dumps({'at':now(),'stage':stage,'key':key,'status':status,'details':details},ensure_ascii=False)+'\n')

def root_from_run(run):
    p=Path(run).resolve()
    for parent in [p,*p.parents]:
        if (parent/'pipeline'/'pyproject.toml').is_file() and (parent/'package.json').is_file():return parent
    raise ValueError('Run must be inside project or PROJECT_ROOT must be set')

def project_root(run):
    return Path(os.environ['PROJECT_ROOT']).resolve() if os.environ.get('PROJECT_ROOT') else root_from_run(run)
