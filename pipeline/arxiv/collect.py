"""Fetch official RSS, validate the feed date, then append immutable candidates."""
import datetime as dt, email.utils, re, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo
from pipeline.shared.runtime import digest, lock, now, read, record, write
from .common import identity, recompute_completion, today, validate_candidates
from .policy import run_policy,bind_papers
from .inputs import compact,remember,input_hash

def fetch(url):
    if not url.startswith('https://rss.arxiv.org/rss/'): raise ValueError('Only official arXiv RSS allowed')
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ai-platform-feedreader/1.0'}),timeout=45) as r:
        if not r.geturl().startswith('https://rss.arxiv.org/rss/'):raise ValueError('RSS redirected outside official source')
        return r.read()

def parse(data,target_date):
    root=ET.fromstring(data); channel=root.find('channel')
    if channel is None: raise ValueError('Not RSS 2 channel')
    date=channel.findtext('lastBuildDate') or channel.findtext('pubDate')
    if not date: raise ValueError('Missing feed date')
    stamp=email.utils.parsedate_to_datetime(date)
    if stamp.tzinfo is None: raise ValueError('Feed date lacks timezone')
    actual=stamp.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()
    if actual!=target_date: raise ValueError(f'RSS date mismatch: {actual} != {target_date}')
    rows=[]
    for item in channel.findall('item'):
        link=item.findtext('link',''); identifier=item.findtext('{http://arxiv.org/schemas/atom}id','') or link
        match=re.search(r'(?:arxiv.org/(?:abs|html)/|arXiv:)?((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(v\d+)?(?:$|[?#])',identifier)
        if not match: raise ValueError('Missing arXiv ID in feed item')
        version=match.group(2)
        if not version:
            for e in item.iter():
                if e.tag.split('}')[-1]=='version':version=(e.text or '').strip()
        if not version:
            match_description=re.search(r'arXiv:'+re.escape(match.group(1))+r'(v[1-9]\d*)\b',item.findtext('description',''))
            if match_description:version=match_description.group(1)
        if not version: raise ValueError('RSS item lacks explicit version; refusing to guess')
        title=re.sub(r'\s*\(arXiv:.*?\)\s*$','',item.findtext('title','')).strip()
        description=item.findtext('description','')
        # RSS arXiv abstract prefix is bookkeeping, not scientific text.
        abstract=re.sub(r'^arXiv:.*?Abstract:\s*','',description,flags=re.S).strip()
        rows.append({'id':match.group(1),'version':version,'title':title,'abstract':abstract,'date':target_date,'source_url':f'https://arxiv.org/abs/{match.group(1)}{version}'})
    return validate_candidates(rows)

def collect(run,target_date,feeds=None,fetcher=fetch):
    run=Path(run); ledger=read(run/'ledger.json',{})
    if target_date!=ledger.get('today_at_start',today()) or target_date!=ledger.get('target_date',target_date):raise ValueError('Cannot replace historical candidates with current RSS')
    policy=run_policy(run)
    required=list(feeds) if feeds is not None else ['https://rss.arxiv.org/rss/'+category for category in policy['sources']['categories']]
    if not required or any(not isinstance(url,str) or not re.fullmatch(r'https://rss\.arxiv\.org/rss/[A-Za-z0-9.-]+',url) for url in required):raise ValueError('Invalid official RSS feed scope')
    successes=[];errors=[];attempts=[];verified={}
    for url in required:
        try:
            rows=parse(fetcher(url),target_date);successes.extend(rows)
            attempt={'url':url,'status':'verified','count':len(rows),'retrieved_at':now()}
            attempts.append(attempt);verified[url]=attempt
        except Exception as exc:errors.append({'url':url,'error':str(exc)});attempts.append({'url':url,'status':'failed','retrieved_at':now()})
    conflicts=[]
    with lock(run/'.candidates.lock'):
        old=validate_candidates(read(run/'run/candidates.json',[])); merged={identity(x):x for x in old}
        capture=read(run/'run/collection-capture.json',{})
        covered={}
        if capture.get('target_date')==target_date and capture.get('candidates_sha256')==digest(old):
            covered={item['url']:item for item in capture.get('scope',[]) if item.get('status')=='verified'}
        for p in successes:
            key=identity(p)
            if key in merged and input_hash(merged[key])!=input_hash(p):
                conflicts.append({'id':p['id'],'error':'Changed input for identical version'})
            else:merged[key]=p
        remember(run,list(merged.values()))
        rows=[compact(p) for p in merged.values()]
        for paper in rows:
            try:
                bind_papers(run,[paper]);record(run,'policy','complete',{},'/'.join(identity(paper)))
            except (ValueError,TypeError,KeyError) as exc:record(run,'policy','blocked',{'error':str(exc)},'/'.join(identity(paper)))
        write(run/'run/candidates.json',rows)
        # Captured evidence survives a later transport/date failure. It is bound
        # to the exact saved candidates and target date, never inferred from an
        # empty list or from a prior status label alone.
        if conflicts:covered={}
        else:covered.update(verified)
        capture={'schema_version':1,'target_date':target_date,'candidates_sha256':digest(rows),'required_feeds':required,'scope':list(covered.values())}
        write(run/'run/collection-capture.json',capture)
    errors.extend(conflicts)
    details={'target_date':target_date,'scope':attempts,'errors':errors,'candidate_count':len(merged),'new_count':len(merged)-len(old)}
    record(run,'collect_attempt','failed' if errors else 'complete',details)
    missing=[url for url in required if url not in covered]
    complete=not missing and not conflicts
    record(run,'collect','complete' if complete else 'failed',{
        **details,'scope':[covered[url] for url in required if url in covered],
        'missing_feeds':missing,'last_attempt_status':'failed' if errors else 'complete',
        'retained_verified_capture':bool(errors and complete),'capture_sha256':digest(capture)})
    recompute_completion(run)
    return rows
