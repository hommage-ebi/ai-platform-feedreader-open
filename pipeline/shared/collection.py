"""Source-neutral collection locking and recoverable multi-file commits.

Adapters hold collection_lock, replay any pending transaction before reading,
and pass their validated relative-path file mapping to commit_collection.
Article identity, eligibility, version ordering and indexes belong to adapters.
"""
import contextlib
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from .runtime import now, project_root, read, write

@contextlib.contextmanager
def collection_lock(dest, run):
    """Atomic directory lock shared with JavaScript build/publication tooling."""
    root=project_root(run)
    guard=Path(dest)/'.collection.lock'
    try: guard.mkdir()
    except FileExistsError: raise RuntimeError('Article collection is locked; wait for its owner or verify crash recovery')
    token=secrets.token_hex(16)
    try:
        write(guard/'owner.json',{'token':token,'pid':os.getpid(),'started_at':now(),'operation':'reader_adapter'})
        if (root/'.sites-runtime/publish.lock').exists():
            raise RuntimeError('Publication is pending; reconcile it before changing the article collection')
        yield
    finally:
        try: owner=read(guard/'owner.json',{})
        except (OSError,ValueError): owner={}
        if owner.get('token')==token: shutil.rmtree(guard)

TRANSACTION='.collection-transaction.json'

def sync_directory(path):
    fd=os.open(Path(path),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def recover_collection(dest):
    """Replay a durable intent before exposing the collection to any reader.

    Build tooling refuses this marker. Each file replacement is atomic; the
    marker disappears only after every replacement has succeeded.
    """
    dest=Path(dest);transaction=read(dest/TRANSACTION)
    if transaction is None:return
    if transaction.get('schema_version')!=1 or not isinstance(transaction.get('files'),dict):
        raise ValueError('Invalid collection transaction; manual recovery required')
    for name,entry in transaction['files'].items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name or name==TRANSACTION:
            raise ValueError('Unsafe collection transaction path')
        target=dest/rel
        if entry['kind']=='json':write(target,entry['data'])
        elif entry['kind']=='html':
            target.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.'+target.name)
            try:
                with os.fdopen(fd,'w') as stream:
                    stream.write(entry['data']);stream.flush();os.fsync(stream.fileno())
                os.replace(tmp,target)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
        else:raise ValueError('Unknown collection transaction entry')
        sync_directory(target.parent)
    (dest/TRANSACTION).unlink();sync_directory(dest)

def commit_collection(dest, files):
    """Persist and replay a replacement set while the caller holds collection_lock.

    Each relative path maps to {"kind": "json" | "html", "data": ...}.
    Interrupted commits keep the durable journal for recover_collection.
    """
    dest = Path(dest)
    write(dest / TRANSACTION, {'schema_version': 1, 'files': files})
    sync_directory(dest)
    recover_collection(dest)
