"""Conservative extraction. Missing affiliations are not inferred from names."""
from html.parser import HTMLParser
class HeaderParser(HTMLParser):
    def __init__(self):super().__init__();self.meta={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and a.get('name','').startswith('citation_'):self.meta.setdefault(a['name'],[]).append(a.get('content',''))
def metadata(p,html=None):
    values={};sources={}
    if html:
        parser=HeaderParser();parser.feed(html);values=parser.meta
    title=(values.get('citation_title') or [p.get('title','')])[0]
    authors=values.get('citation_author',[]);affiliations=values.get('citation_author_institution',[])
    for name,items in [('title',values.get('citation_title')),('authors',authors),('affiliations',affiliations)]:
        if items:sources[name]={'url':p['source_url'],'method':'HTML citation metadata; verify against paper header'}
    return {'schema_version':1,'id':p['id'],'version':p['version'],'title':title,'authors':authors,'affiliations':list(dict.fromkeys(affiliations)),'institution_names':[],'source':p['source_url'],'field_sources':sources,'missing_fields':[k for k,v in [('title',title),('authors',authors),('affiliations',affiliations),('institution_names',[])] if not v],'notes':['Verify metadata against original paper; institution names are not inferred.']}
