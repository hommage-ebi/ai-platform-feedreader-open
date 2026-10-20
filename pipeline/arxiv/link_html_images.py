"""Apply an explicitly requested image-delivery change without rewriting articles."""
import argparse, copy, html
from pathlib import Path
from pipeline.shared.collection import collection_lock, recover_collection, commit_collection
from pipeline.shared.runtime import read, write, digest, now
from .prepare import fetch
from .selected_figures import link_html_figure
from .assemble import generation_identity


def migrate(root, run, cache):
    root=Path(root).resolve();run=Path(run).resolve();cache=Path(cache).resolve()
    dest=root/'data/articles';changes={};updates={}
    with collection_lock(dest,run):
        recover_collection(dest)
        papers=read(dest/'papers.json');manifest=read(dest/'images-manifest.json')
        for article in papers:
            for version in article.get('versionHistory',[article]):
                directory=run/'run/papers'/article['id']/version['version']
                source=read(directory/'source.json',{})
                if source.get('kind')!='html' or not version.get('stored_images'):continue
                original=cache/(source['id']+source['version']);original.mkdir(parents=True,exist_ok=True)
                if not (original/'source.html').exists():(original/'source.html').write_bytes(fetch(source['url'])[0])
                result=read(directory/'result.json')
                linked=[link_html_figure(original,source,figure) for figure in result['figures']]
                old={image['source_ref']:image for image in version['stored_images']}
                if len(old)!=len(linked) or set(old)!={image['source_ref'] for image in linked}:raise ValueError('Migration figure references differ')
                path=dest/version['html_path'];before=path.read_bytes()
                if digest(before)!=version['content_sha256']:raise ValueError('Migration article hash differs')
                text=before.decode('utf-8')
                for image in linked:
                    src='src="'+html.escape(old[image['source_ref']]['serving_path'],quote=True)+'"'
                    if text.count(src)!=1:raise ValueError('Migration image is not unique')
                    text=text.replace(src,'src="'+html.escape(image['url'],quote=True)+'" referrerpolicy="no-referrer"')
                previous=version['content_sha256'];version['stored_images']=[];version['linked_images']=linked
                version['content_sha256']=digest(text.encode('utf-8'))
                candidates={f['src']:{'selection':f['selection'],'source_sha256':source['sha256']} for f in result['figures']}
                version['generation_hash']=generation_identity(result,read(directory/'metadata.json'),source,(directory/'summary.md').read_text(),candidates,version.get('tags',[]))
                version['rendering_update']={'policy':'arxiv-html-direct-v1','previous_content_sha256':previous}
                changes[version['html_path']]={'kind':'html','data':text}
                updates[(article['id'],version['version'])]=copy.deepcopy(version)
            if article.get('versionHistory'):
                latest=next(v for v in article['versionHistory'] if v['version']==article['version'])
                article.update(copy.deepcopy(latest))
        if not updates:return {'changed_articles':0,'linked_images':0}
        used={(article['id'],v['version'],image['source_ref']) for article in papers for v in article.get('versionHistory',[article]) for image in v.get('stored_images',[])}
        manifest['images']=[i for i in manifest['images'] if (i['article_id'],i['version'],i['source_ref']) in used]
        changes['papers.json']={'kind':'json','data':papers};changes['images-manifest.json']={'kind':'json','data':manifest}
        for batch_path in [dest/'batch-status.json',*sorted((dest/'batch-history').glob('*.json'))]:
            batch=read(batch_path);affected=False
            for identity in batch.get('completed_articles',[]):
                version=updates.get((identity['article_id'],identity['version']))
                if version:identity['content_sha256']=version['content_sha256'];affected=True
            if affected:
                batch.update(published_count=0,publication_status='not_deployed',updated_at=now())
                changes[str(batch_path.relative_to(dest))]={'kind':'json','data':batch}
        commit_collection(dest,changes)
    # Staging is a reproducible copy; the original model outputs stay unchanged.
    staging=read(run/'site/papers.json',[])
    for index,version in enumerate(staging):
        updated=updates.get((version['id'],version['version']))
        if updated:
            staging[index]=updated;target=run/'site'/updated['html_path'];target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(changes[updated['html_path']]['data']);write(target.with_suffix('.json'),updated)
    write(run/'site/papers.json',staging)
    return {'changed_articles':len(updates),'linked_images':sum(len(v['linked_images']) for v in updates.values()),'stored_images':len(manifest['images'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',required=True);parser.add_argument('--run',required=True);parser.add_argument('--cache',required=True)
    args=parser.parse_args()
    print(migrate(args.project_root,args.run,args.cache))
