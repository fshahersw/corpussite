"""Export the validated, category-mapped law outline without changing source files.

Retains original node IDs, rowid segments and the local adapter's exact natural
citation order. No provision bodies or unclassified publisher collections are copied.
"""
from __future__ import annotations
import argparse, collections, datetime, hashlib, json, sqlite3, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
APP=ROOT/'delivery/archive-directory'
sys.path.insert(0,str(APP))
import categories
import law_outline as native

ALLOWED={'statutes','rules','constitutions','regulations','forms','guidance','directories','judges'}
DEFAULT=ROOT/'_transfer_scratch/supabase_export/law_outline'

def connect(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');return db

def signature(path):
    value=path.stat();return [value.st_size,value.st_mtime_ns]

def save_rows(folder,name,rows):
    path=folder/(name+'.jsonl');temp=path.with_suffix('.tmp');digest=hashlib.sha256();count=0;size=0
    with temp.open('wb') as stream:
        for row in rows:
            body=(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
            stream.write(body);digest.update(body);count+=1;size+=len(body)
    temp.replace(path)
    return {'table':'corpus_law_'+name,'path':path.name,'rows':count,'bytes':size,'sha256':digest.hexdigest()}

def export(folder=DEFAULT):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    ready,reason=native._state()
    if not ready:raise ValueError('Outline validation gate is closed: '+str(reason))
    source=native.DATA/native.DB_NAME;catalog=native.bulk_laws.DB
    before={str(p):signature(p) for p in (source,catalog)}
    db=connect(source);bulk=connect(catalog)
    all_collections=[dict(r) for r in db.execute('SELECT * FROM collections ORDER BY state,kind')]
    collections_kept=[r for r in all_collections if categories.classify(r['kind']) in ALLOWED]
    selected={(r['state'],r['kind']) for r in collections_kept}
    nodes={r['id']:dict(r) for r in db.execute('SELECT * FROM nodes ORDER BY id') if (r['state'],r['kind']) in selected}
    children=collections.Counter(r['parent'] for r in nodes.values())
    assert all(not r['parent'] or r['parent'] in nodes for r in nodes.values())
    assert all(not r['parent'] or nodes[r['parent']]['depth']<r['depth'] for r in nodes.values())
    segments=[dict(r) for r in db.execute('SELECT node,lo,hi FROM segments ORDER BY lo')]
    assert all(a['hi']<b['lo'] for a,b in zip(segments,segments[1:]))
    selected_segments=[r for r in segments if r['node'] in nodes]
    sizes=collections.Counter()
    for r in selected_segments:sizes[r['node']]+=r['hi']-r['lo']+1
    assert all(sizes[r['id']]==r['direct'] for r in nodes.values())
    assert sum(sizes.values())==sum(r['provisions'] for r in collections_kept)
    small={r['id']:[] for r in nodes.values() if r['direct']<=native.SORT_LIMIT}
    slot=0;scanned=0;mapped=0
    for row in bulk.execute('SELECT rowid AS source_rowid,title,citation FROM records ORDER BY rowid'):
        rid=row['source_rowid'];scanned+=1
        while slot<len(segments) and segments[slot]['hi']<rid:slot+=1
        if slot==len(segments) or segments[slot]['lo']>rid:raise ValueError('Publisher row outside validated outline')
        node=segments[slot]['node']
        if node in nodes:
            mapped+=1
            if node in small:small[node].append((row['citation'] or row['title'] or '',rid))
        if scanned%500000==0:print(json.dumps({'stage':'outline','publisher_rows':scanned,'mapped':mapped}),flush=True)
    for node,rows in small.items():
        assert len(rows)==nodes[node]['direct']
        rows.sort(key=lambda r:(native._natural(r[0]),r[1]))
    assert mapped==sum(sizes.values())
    def node_rows():
        for node in nodes.values():
            item={k:node[k] for k in ('id','state','kind','parent','position','direct','total','label','raw_label','label_basis')}
            item['has_children']=bool(children[node['id']])
            item['ordered_rowids']=[r[1] for r in small[node['id']]] if node['id'] in small else None
            item['metadata']={k:v for k,v in node.items() if k not in item}
            yield item
    descriptors=[save_rows(folder,'collections',collections_kept),save_rows(folder,'nodes',node_rows()),save_rows(folder,'segments',selected_segments)]
    names=native.bulk_laws.state_names()
    context={'key':'law_outline','data':{'ready':True,'qualification':ready['qualification'],'state_names':names,
        'kind_labels':native.KIND_LABELS,'kind_order':native.KIND_ORDER,'sort_limit':native.SORT_LIMIT,
        'statute_audits':{state:native._audit(state) for state in sorted({r['state'] for r in collections_kept})},
        'category_policy':'Only collections mapped to an existing semantic category; original IDs and ranges retained.',
        'expected_tables':{r['table']:r['rows'] for r in descriptors},'expected_provisions':mapped},
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    (folder/'context.json').write_text(json.dumps(context,ensure_ascii=False,indent=2)+'\n','utf-8')
    db.close();bulk.close()
    if any(signature(Path(p))!=v for p,v in before.items()):raise ValueError('Source database changed during export')
    manifest={'schema_version':1,'status':'passed','generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'tables':descriptors,'context':'context.json','category_mapped_provisions':mapped,'publisher_rows_scanned':scanned,
        'excluded_collections':[r for r in all_collections if (r['state'],r['kind']) not in selected],
        'source_signatures':before,'source_databases_read_only':True,'elapsed_seconds':round(time.monotonic()-started,2),
        'checks':{'no_segment_overlap':True,'direct_counts_match':True,'collection_counts_match':True,'parent_closure':True,'acyclic':True,'exact_natural_order':True}}
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n','utf-8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=DEFAULT)
    print(json.dumps(export(parser.parse_args().output)),flush=True)
