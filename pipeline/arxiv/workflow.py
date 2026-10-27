"""Run the arXiv stages and resume saved runs; model execution is an external handoff."""
import argparse,datetime as dt,json,uuid
import os
from zoneinfo import ZoneInfo
from pathlib import Path
# Direct-file CLI remains usable from a run directory; imports stay package-scoped.
if __package__ in (None, ''):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = 'pipeline.arxiv'

from pipeline.shared.runtime import lock, now, project_root, read, record, write
from .common import identity, paper_path, recompute_completion

def start(root,started_at=None,refresh_rss=False):
    root=Path(root).resolve();started_at=started_at or dt.datetime.now(dt.timezone.utc)
    if started_at.tzinfo is None:raise ValueError('Invocation timestamp must include timezone')
    date=started_at.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat();base=root/'data/runs'
    with lock(base/'.start.lock'):
        previous=sorted(base.glob('*/*/ledger.json'));today_runs=[]
        for file in previous:
            try:
                ledger=read(file)
                if ledger.get('target_date')==date:today_runs.append(file.parent)
            except (ValueError,TypeError):pass  # Isolate damaged runs after today's capture.
        run=today_runs[-1] if today_runs else base/date/(started_at.strftime('%H%M%S')+'-'+uuid.uuid4().hex[:8]);run.mkdir(parents=True,exist_ok=True)
        with lock(run/'.run.lock',blocking=False):
            with lock(run/'.ledger.lock'):
                ledger=read(run/'ledger.json',{'schema_version':1,'target_date':date,'run_id':run.name,'status':'incomplete','created_at':now(),'stages':{}})
                ledger['today_at_start']=date;ledger['invocation_started_at']=started_at.isoformat();write(run/'ledger.json',ledger)
            # Never let historical corruption prevent capturing today's expiring RSS.
            try:
                from .collect import collect
                collect(run,date,refresh_rss=refresh_rss)
            except Exception as exc:record(run,'collect','failed',{'error':str(exc)})
            historical=[]
            for file in previous:
                if file.parent==run or file.parent.parent.name>date:continue
                entry={'target_date':file.parent.parent.name,'run_id':file.parent.name,'run_dir':str(file.parent),'candidates_available':(file.parent/'run/candidates.json').is_file()}
                try:
                    with lock(file.parent/'.run.lock',blocking=False):
                        status=recompute_completion(file.parent)
                        if status=='complete':continue
                        if not entry['candidates_available']:
                            entry.update(status='blocked',error='Saved candidates unavailable; current RSS cannot reconstruct history')
                            record(file.parent,'resume','blocked',{'error':entry['error']})
                except BlockingIOError:
                    entry.update(status='blocked',error='Run is busy; retry after its current stage finishes')
                except Exception as exc:
                    entry.update(status='blocked',error=str(exc))
                    # A corrupt ledger must not be overwritten with an empty one.
                    with lock(file.parent/'.ledger.lock'):
                        with (file.parent/'failures.jsonl').open('a') as stream:stream.write(json.dumps({'at':now(),'stage':'resume','status':'blocked','details':{'error':str(exc)}})+'\n')
                historical.append(entry)
            with lock(run/'.ledger.lock'):
                ledger=read(run/'ledger.json');ledger['backlog_at_start']=historical;write(run/'ledger.json',ledger)
    return {'today':str(run),'today_collection':read(run/'ledger.json').get('stages',{}).get('collect',{'status':'not_attempted'}),'today_collection_attempt':read(run/'ledger.json').get('stages',{}).get('collect_attempt',{'status':'not_attempted'}),'backlog':historical}

def cli():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['start','collect','split','classify_tasks','merge','prepare','summary_tasks','assemble','claim','finish','release','cleanup','status']);parser.add_argument('--date');parser.add_argument('--started-at',help='Invocation start ISO timestamp with timezone, frozen before bootstrap');parser.add_argument('--timezone',default='Asia/Tokyo');parser.add_argument('--run-dir',default='.');parser.add_argument('--project-root');parser.add_argument('--task-file');parser.add_argument('--task-index',type=int,default=0);parser.add_argument('--owner');parser.add_argument('--token');parser.add_argument('--refresh-rss',action='store_true',help='Refetch RSS with a new cache-busting URL instead of reusing saved feeds (start/collect only)');args=parser.parse_args();run=Path(args.run_dir).resolve()
    if args.refresh_rss and args.stage not in ('start','collect'):parser.error('--refresh-rss is only supported for start and collect')
    if args.project_root:os.environ['PROJECT_ROOT']=str(Path(args.project_root).resolve())
    if args.timezone!='Asia/Tokyo':parser.error('Only Asia/Tokyo is supported')
    started_at=dt.datetime.fromisoformat(args.started_at.replace('Z','+00:00')) if args.started_at else dt.datetime.now(dt.timezone.utc)
    if started_at.tzinfo is None:parser.error('--started-at must include a timezone')
    invocation_date=started_at.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()
    if args.stage=='start':
        if args.date and args.date!=invocation_date:parser.error('--date must match the invocation start date in Asia/Tokyo')
        print(json.dumps(start(project_root(run),started_at,refresh_rss=args.refresh_rss),ensure_ascii=False,indent=2));return
    run.mkdir(parents=True,exist_ok=True)
    with lock(run/'.run.lock',blocking=False):
        if not (run/'ledger.json').exists():
            date=args.date or invocation_date
            if date!=invocation_date:parser.error('New run --date must match --started-at in Asia/Tokyo; use saved historical runs for backlog')
            write(run/'ledger.json',{'schema_version':1,'target_date':date,'today_at_start':invocation_date,'invocation_started_at':started_at.isoformat(),'run_id':run.name,'status':'incomplete','stages':{}})
        prerequisites={'split':'candidates.json','classify_tasks':'groups.json','merge':'groups.json','prepare':'selected.json','summary_tasks':'selected.json','assemble':'selected.json'}
        if args.stage in prerequisites and not (run/'run'/prerequisites[args.stage]).is_file():raise ValueError('Missing prerequisite run/'+prerequisites[args.stage]+'; run the preceding stage first')
        if args.stage=='status':
            status=recompute_completion(run)
            result={'status':status,'pending':read(run/'ledger.json').get('pending',[])}
        elif args.stage=='collect':
            from .collect import collect
            result=collect(run,args.date or read(run/'ledger.json')['target_date'],refresh_rss=args.refresh_rss)
        elif args.stage in ('split','merge'):
            from . import split_merge
            result=getattr(split_merge,args.stage)(run)
        elif args.stage in ('classify_tasks','summary_tasks'):
            from .make_tasks import make_tasks
            result=make_tasks(run,'classify' if args.stage=='classify_tasks' else 'summary')
        elif args.stage=='prepare':
            from .prepare import prepare
            result=prepare(run)
        elif args.stage=='assemble':
            from .assemble import assemble
            result=assemble(run)
        elif args.stage=='cleanup':
            from .prepare import cleanup
            from .inputs import cleanup_inputs
            cleanup_inputs(run)
            completed=read(run/'site/papers.json',[])
            for paper in completed:cleanup(paper_path(run,paper))
            for name in ('classifier_tasks.json','summary_tasks.json'):(run/'run'/name).unlink(missing_ok=True)
            result={'cleaned':len(completed)}
        else:
            from .make_tasks import claim,finish,release
            if not args.task_file:parser.error('--task-file is required')
            task=read(args.task_file)[args.task_index];root=project_root(run)
            if args.stage=='claim':
                if not args.owner:parser.error('--owner is required')
                result={'claim_token':claim(root,task,args.owner),'task_name':task['task_name']}
            else:
                if not args.token:parser.error('--token is required')
                result=finish(root,task,args.token) if args.stage=='finish' else release(root,task['cache_key'],args.token)
        failures=[];ledger=read(run/'ledger.json',{})
        if args.stage in ('assemble','prepare'):
            for paper in read(run/'run/selected.json',[]):
                key=args.stage+':'+ '/'.join(identity(paper));stage=ledger.get('stages',{}).get(key,{})
                if stage.get('status')!='complete':failures.append({'paper':list(identity(paper)),**stage})
        elif args.stage=='collect':
            stage=ledger.get('stages',{}).get('collect_attempt',ledger.get('stages',{}).get('collect',{}))
            if stage.get('status')!='complete':failures.append(stage)
        elif args.stage in ('classify_tasks','summary_tasks'):
            stage=ledger.get('stages',{}).get('classify_tasks' if args.stage=='classify_tasks' else 'summary_tasks',{})
            if stage.get('details',{}).get('blocked_count'):failures.append(stage)
        elif args.stage=='merge':
            candidates=read(run/'run/candidates.json',[]);classified={identity(p) for p in read(run/'run/classifications.json',[])}
            failures=[{'paper':list(identity(p)),'error':'Classification missing or invalid; inspect failure ledger'} for p in candidates if identity(p) not in classified]
        print(json.dumps({'status':'partial_failure','result':result,'failures':failures} if failures else result,ensure_ascii=False,indent=2))
        if failures:raise SystemExit(1)

if __name__=='__main__':
    import sys
    try:cli()
    except Exception as exc:
        run=Path(sys.argv[sys.argv.index('--run-dir')+1]) if '--run-dir' in sys.argv else Path.cwd()
        # A busy run belongs to another process; even failure bookkeeping must
        # not mutate its ledger outside the run lock.
        if not isinstance(exc,BlockingIOError):
            record(run,'workflow','failed',{'error':str(exc),'stage':sys.argv[1] if len(sys.argv)>1 else 'unknown'})
        print(str(exc),file=sys.stderr);raise SystemExit(1)

