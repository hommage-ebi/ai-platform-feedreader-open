"""Editable interests, validated once per operation and bound into prompt identity."""
import json
import re
from pathlib import Path

ID_RE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
PLACEHOLDERS=re.compile(r'\{(interest_config|topic_example|input_file|output_file)\}')

def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate interests JSON key: '+key)
        result[key]=value
    return result

def load_interests(root):
    path=Path(root)/'pipeline/config/interests.json'
    try:config=json.loads(path.read_text(),object_pairs_hook=unique_object)
    except (OSError,ValueError) as exc:raise ValueError('Cannot load interests configuration: '+str(exc)) from exc
    return validate_interests(config)

def validate_interests(config):
    if not isinstance(config,dict) or set(config)!={'schema_version','interests','application_guidance'} or type(config['schema_version']) is not int or config['schema_version']!=1:
        raise ValueError('Invalid interests configuration schema')
    rows=config['interests'];guidance=config['application_guidance']
    if not isinstance(rows,list) or not rows:raise ValueError('Interests must be a nonempty array')
    if not isinstance(guidance,str) or not guidance.strip():raise ValueError('Application guidance must be nonempty text')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'id','label','criteria'}:raise ValueError('Invalid interest entry schema')
        key=row['id']
        if not isinstance(key,str) or not ID_RE.fullmatch(key) or key in seen:raise ValueError('Invalid or duplicate interest id')
        if any(not isinstance(row[field],str) or not row[field].strip() for field in ('label','criteria')):raise ValueError('Interest label and criteria must be nonempty text')
        seen.add(key)
    return config

def render_relevance(template,config,input_file=None,output_file=None):
    for name in ('interest_config','topic_example','input_file','output_file'):
        if template.count('{'+name+'}')!=1:raise ValueError('Relevance template requires exactly one {'+name+'} placeholder')
    example={'id':'入力と同じID','version':'入力と同じ版','decision':'include','topics':[config['interests'][0]['id']],'reason':'タイトルとabstractに基づく具体的な判定理由'}
    values={'interest_config':json.dumps(config,ensure_ascii=False,sort_keys=True,indent=2),
            'topic_example':json.dumps(example,ensure_ascii=False,separators=(',',':'))}
    if input_file is not None:values.update(input_file=str(input_file),output_file=str(output_file))
    return PLACEHOLDERS.sub(lambda match:values.get(match[1],match[0]),template)
