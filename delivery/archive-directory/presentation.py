"""Evidence-preserving display groups over immutable source observations."""
import hashlib,json,re
from pathlib import Path

def sid(s):return hashlib.sha256(s.encode()).hexdigest()[:32]

def edition_identity(payload):
    """Keep explicitly recorded legal editions distinct; acquisition time is not an edition."""
    fields={'source_date','source_date_kind','release_point','source_release','edition','source_edition',
            'capture_edition','effective_date','effective_from','effective_to','version','source_version',
            'source_version_statement','current_through_public_law_date','publisher_volume_amendment_marker'}
    wrappers={'metadata','source_record','source_metadata','source_catalog_record','source_reference'}
    found=[]
    def visit(value,path=(),depth=0):
        if not isinstance(value,dict) or depth>4:return
        for key in sorted(fields & value.keys()):
            item=value[key]
            if item is not None and item!='':found.append(('.'.join((*path,key)),item))
        for key in sorted(wrappers & value.keys()):visit(value[key],(*path,key),depth+1)
    visit(payload)
    return found
def build_groups(db,root):
    db.executescript('''CREATE TABLE display_groups(id TEXT PRIMARY KEY,preferred_id TEXT,title TEXT,state TEXT,county TEXT,source_count INTEGER,group_basis TEXT);
    CREATE TABLE display_members(record_id TEXT PRIMARY KEY,display_id TEXT);
    CREATE INDEX display_member_group ON display_members(display_id,record_id);''')
    groups={};notes={};source_to_entity={}
    members=root/'sources/judge_entities_20260918/members.jsonl'
    entity_ids={json.loads(p)['entity_id']:key for key,p in db.execute("SELECT id,payload FROM records WHERE dataset='judge_entities'")}
    if members.exists():
        with members.open(encoding='utf-8') as member_file:
            for line in member_file:
                p=json.loads(line);entity=entity_ids.get(p.get('entity_id'));obs=p.get('source_observation_id') or p.get('observation_id')
                if entity and obs:
                    dataset=p.get('dataset') or p.get('source_dataset','')
                    prefix='vendor:' if 'vendor' in dataset else 'judge:'
                    member_key=sid(prefix+obs)
                    if member_key in source_to_entity and source_to_entity[member_key]!=entity:
                        raise ValueError('Conflicting judge identity memberships: '+member_key)
                    source_to_entity[member_key]=entity
    rows={}
    for row in db.execute('SELECT id,title,group_name,dataset,state,county,kind,source_url,quality,content_id,payload,length(inline_text),original_id,text_id FROM records'):
        key,title,group,dataset,state,county,kind,url,quality,content_id,payload,inline_length,original_id,text_id=row
        p=json.loads(payload);raw=p.get('raw_sha256') or p.get('sha256');rawpath=p.get('raw_path') or '';textsha=p.get('indexed_text_sha256') or p.get('text_sha256')
        if key in source_to_entity:identity='entity:'+source_to_entity[key];basis='Evidence-backed judge identity; source observations retained'
        elif dataset=='judge_entities':identity='entity:'+key;basis='Evidence-backed judge identity; source observations retained'
        elif dataset in {'judge_enrichment','judge_vendor'}:identity='source:'+key;basis='Unmerged source observation'
        elif dataset=='federal' and not raw:identity='federal-link:'+url;basis='Same federal link destination; no captured edition merged'
        elif raw and (Path(rawpath).suffix.lower() in {'.pdf','.docx','.doc','.rtf','.odt','.html','.htm'} or dataset in {'focused','pending_publication'}):identity='raw:'+raw;basis='Identical original SHA-256; richer text representation preferred'
        elif textsha and inline_length>250:identity='body:'+sid(json.dumps([state,p.get('citation') or title,textsha,edition_identity(p)],sort_keys=True,ensure_ascii=False));basis='Identical extracted body, title/citation, jurisdiction and explicit edition'
        else:identity='source:'+key;basis='Separate source or version'
        # A title is descriptive evidence; a URL/hash label is only a fallback.
        title=' '.join((title or url or 'Untitled source').split())
        named=bool(title and not title.startswith(('http','[','{')) and len(title)>4)
        usable=bool(content_id or inline_length or text_id)
        eligible=not isinstance(p.get('metadata'),dict) or p['metadata'].get('retrieval_eligible') is not False
        rank=(eligible,dataset=='judge_entities',usable,named,'needs_content_review' not in (quality or ''),inline_length or 0)
        rows[key]={'title':title,'state':state,'county':county,'rank':rank,'dataset':dataset}
        groups.setdefault(identity,[]).append(key);notes[identity]=basis
    removed=0;exact_groups=0
    for identity,keys in groups.items():
        preferred=max(keys,key=lambda key:(rows[key]['rank'],key))
        display_id=preferred if identity.startswith(('entity:','source:')) else 'doc:'+sid(identity)
        state='; '.join(sorted({s.strip() for key in keys for s in rows[key]['state'].split('; ') if s.strip()}))
        county='; '.join(sorted({s.strip() for key in keys for s in rows[key]['county'].split('; ') if s.strip()}))
        source_count=sum(rows[k]['dataset']!='judge_entities' for k in keys) or 1
        db.execute('INSERT INTO display_groups VALUES(?,?,?,?,?,?,?)',(display_id,preferred,rows[preferred]['title'],state,county,source_count,notes[identity]))
        db.executemany('INSERT INTO display_members VALUES(?,?)',[(key,display_id) for key in keys])
        removed+=len(keys)-1;exact_groups+=len(keys)>1
    return {'source_records':len(rows),'display_groups':len(groups),'grouped_record_difference':removed,'groups_with_multiple_members':exact_groups,'deduplication_policy':'Exact original bytes, exact scoped text, same federal URL, or separately reviewed judge identity links. No name-only judge merges or cross-version law replacement.'}

def source_list(db,display_id):
    return [dict(zip(['id','title','dataset','source_url','state','county','quality'],r)) for r in db.execute('SELECT r.id,r.title,r.dataset,r.source_url,r.state,r.county,r.quality FROM records r JOIN display_members m ON m.record_id=r.id WHERE m.display_id=? AND r.dataset != ? ORDER BY r.dataset,r.title',(display_id,'judge_entities'))]
