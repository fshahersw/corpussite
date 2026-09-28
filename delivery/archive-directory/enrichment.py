"""Small read-only evidence layer; all assets are confined and hash-verified."""
import hashlib, json, re, threading
from pathlib import Path
from urllib.parse import urlsplit
import supplements

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/gap_enrichment_20260927'
COUNTY_DATA = ROOT / 'sources/county_enrichment_20260928/published'
_CACHE = {}; _LOCK = threading.Lock()
_PRIVATE_KEYS = {'path','file','local_path','raw_path','text_path','source_path','metadata_path','evidence_path','extracted_text_path','original_file','text_file'}
_ABSOLUTE = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\|file:/{2,3}|/(?:Users|home|tmp|private|mnt|var|root)(?:/|$))',re.I)


def public_value(value):
    """Retain public evidence/quotes/hashes, excluding internal filesystem locations."""
    if isinstance(value, dict):
        return {k: public_value(v) for k, v in value.items() if k not in _PRIVATE_KEYS and not k.endswith(('_path','_file'))}
    if isinstance(value, list): return [public_value(v) for v in value]
    if isinstance(value, str) and _ABSOLUTE.match(value): return '[Private local source location omitted]'
    return value

def _single_state(folder):
    folder = Path(folder or DATA).resolve()
    try:
        gate = folder / 'validation.json'; file = folder / 'bundle.json'
        gate_bytes = gate.read_bytes(); envelope = json.loads(gate_bytes)
        if not isinstance(envelope, dict) or envelope.get('ready') is not True or envelope.get('status') != 'passed' or envelope.get('schema_version') is None:
            return None
        entries = envelope.get('data_files')
        if not isinstance(entries, list): return None
        paths = {}
        stats = []
        for entry in entries:
            if not isinstance(entry, dict): return None
            path = supplements._confined(folder, entry.get('path'))
            if not path or not path.is_file(): return None
            paths[path] = entry.get('sha256')
            st = path.stat(); stats.append((str(path), st.st_size, st.st_mtime_ns))
        if file not in paths: return None
        signature = (hashlib.sha256(gate_bytes).hexdigest(), tuple(stats))
    except (OSError, ValueError, TypeError): return None
    with _LOCK:
        cached = _CACHE.get(str(folder))
        if cached and cached[0] == signature: return cached[1]
        try:
            description = supplements._describe(folder)
            value = json.loads(file.read_text(encoding='utf-8')) if description['ready'] else None
            if value is not None:
                if not isinstance(value, dict) or value.get('available') is not True or any(not isinstance(value.get(k), list) for k in ('resources','nodes','edges')):
                    value = None
                else:
                    # Every advertised original/reader must itself be registered
                    # in the verified envelope, not merely named by the bundle.
                    for r in value['resources']:
                        if not isinstance(r, dict): value = None; break
                        for field, hash_field in (('original_file','raw_sha256'),('text_file','text_sha256')):
                            if r.get(field):
                                path = supplements._confined(folder, r[field])
                                if path not in paths or paths[path] != r.get(hash_field):
                                    value = None; break
                        if value is None: break
        except (OSError, ValueError, TypeError, KeyError): value = None
        _CACHE[str(folder)] = (signature, value)
        return value


def state(folder=None):
    if folder is not None: return _single_state(folder)
    base = _single_state(DATA)
    extra = _single_state(COUNTY_DATA)
    if extra is None:
        if base is not None and COUNTY_DATA.exists():
            return dict(base,pending_collections=['county_enrichment_20260928'])
        return base
    # A second gate cannot overwrite an existing reader or contradict a stable
    # edge identity. Hold that optional collection rather than merge ambiguity.
    prior_ids = {r['id'] for r in (base or {}).get('resources',[])}
    prior_edges = {e.get('id'):e for e in (base or {}).get('edges',[]) if e.get('id')}
    if (len({r['id'] for r in extra['resources']})!=len(extra['resources']) or any(r['id'] in prior_ids for r in extra['resources']) or
        any(e.get('id') in prior_edges and e != prior_edges[e['id']] for e in extra['edges'])):
        return dict(base,pending_collections=['county_enrichment_20260928']) if base else None
    bundles = [(base,DATA,'gap_enrichment_20260927'),(extra,COUNTY_DATA,'county_enrichment_20260928')]
    resources=[]; nodes={}; edges={}; summaries=[]
    for bundle,root,dataset in bundles:
        if not bundle: continue
        resources.extend(dict(r,_collection_folder=str(root),_collection_dataset=dataset) for r in bundle['resources'])
        for n in bundle['nodes']: nodes.setdefault(n['id'],n)
        for e in bundle['edges']:
            key=e.get('id') or json.dumps(public_value(e),sort_keys=True)
            edges.setdefault(key,e)
        summaries.append(bundle.get('summary',{}))
    summary=dict((base or extra).get('summary',{}))
    for key in ('with_text','held','held_resources','held_relationships','duplicates'):
        summary[key]=sum(s.get(key,0) for s in summaries)
    summary.update(resources=len(resources),original_documents=len({r['raw_sha256'] for r in resources if r.get('raw_sha256')}),graph_nodes=len(nodes),graph_edges=len(edges))
    for field,key in [('lane','by_lane'),('state','by_state'),('resource_type','by_type')]:
        counts={}
        for r in resources:
            label=r.get(field) or 'unspecified'; counts[label]=counts.get(label,0)+1
        summary[key]=counts
    summary['by_relation']={}
    for e in edges.values(): summary['by_relation'][e['relation']]=summary['by_relation'].get(e['relation'],0)+1
    return dict(base or extra,resources=resources,nodes=list(nodes.values()),edges=list(edges.values()),summary=summary,
                generated_at=max(str(b.get('generated_at') or '') for b,_,_ in bundles if b),
                collections=[dataset for b,_,dataset in bundles if b])

def integer(value, fallback, maximum):
    try: return max(1, min(maximum, int(value)))
    except (TypeError, ValueError): return fallback

def public_record(r):
    allowed = ('id', 'title', 'state', 'county_fips', 'county_geoids', 'mdl_number', 'resource_type', 'category', 'document_shape',
               'native_id', 'source_url', 'final_url', 'mime_type', 'captured_at', 'source_as_of', 'published_at', 'effective_from', 'filed_at',
               'jurisdiction', 'jurisdiction_scope', 'applicability', 'legal_currency_verified', 'legal_status', 'contains_rescinded_rule_notices', 'qualification', 'caption_as_printed', 'caption_basis', 'quality_notes', 'source_evidence',
               'source_page', 'page_range', 'parent_compilation_id',
               'date_evidence', 'review_status', 'original_url', 'text_url', 'raw_sha256', 'text_sha256', 'raw_bytes', 'text_characters', 'lane', 'hierarchy')
    return public_value({k: r[k] for k in allowed if k in r})

def listing(params, folder=None):
    data = state(folder)
    if not data: return {'available': False, 'total': 0, 'items': [], 'qualification': 'This source addition has not been published.'}
    all_rows = data['resources']; found = all_rows
    for key in ('state', 'lane', 'resource_type', 'document_shape'):
        if params.get(key):
            found = [r for r in found if str(r.get(key) or (r.get('category') if key == 'resource_type' else '') or '').casefold() == str(params[key]).casefold()]
    if params.get('mdl'):
        entity = 'mdl:' + str(params['mdl'])
        ids = {e['source'] for e in data['edges'] if e['target'] == entity} | {e['target'] for e in data['edges'] if e['source'] == entity}
        found = [r for r in found if r['id'] in ids or str(r.get('mdl_number') or '') == str(params['mdl'])]
    if params.get('county'):
        entity = 'county:' + str(params['county'])
        linked = {e['target'] for e in data['edges'] if e['source'] == entity} | {e['source'] for e in data['edges'] if e['target'] == entity}
        captured = {e['target'] for e in data['edges'] if e['source'] in linked and e['relation'] == 'captured_as'}
        found = [r for r in found if r['id'] in linked | captured]
    if params.get('q'):
        terms = str(params['q']).casefold().split()
        found = [r for r in found if all(t in ' '.join(str(r.get(k) or '') for k in ('title','state','native_id','resource_type','source_url')).casefold() for t in terms)]
    page = integer(params.get('page'), 1, 1000000); limit = integer(params.get('limit'), 25, 100)
    found = sorted(found, key=lambda r: (r.get('state') or '', r['title'], r['id']))
    result = {'available': True, 'total': len(found), 'page': page, 'limit': limit, 'items': [public_record(r) for r in found[(page-1)*limit:page*limit]],
            'summary': data['summary'], 'generated_at': data['generated_at'], 'qualification': data['qualification'],
            'facets': {key: sorted({str(r.get(key)) for r in all_rows if r.get(key)}) for key in ('state','lane','resource_type','document_shape')}}
    if data.get('pending_collections'): result['pending_collections']=data['pending_collections']
    return result

def detail(ident, folder=None):
    data = state(folder)
    if not data: return None
    r = next((r for r in data['resources'] if r['id'] == ident), None)
    if not r: return None
    result = public_record(r)
    content = asset(ident, 'text', folder)
    result['text'] = content[0].decode('utf-8-sig', errors='replace') if content else ''
    result['relationships'] = graph({'entity': ident, 'limit': 100}, folder)
    return result

def graph(params, folder=None):
    data = state(folder)
    if not data: return {'available': False, 'nodes': [], 'edges': [], 'total': 0}
    entity = str(params.get('entity') or '')
    # An empty selector is a summary, never an unbounded graph response.
    found = [e for e in data['edges'] if entity and entity in (e['source'], e['target'])]
    limit = integer(params.get('limit'), 100, 500)
    selected = found[:limit]; ids = {e[k] for e in selected for k in ('source','target')}
    result = {'available': True, 'entity': entity, 'total': len(found), 'edges': public_value(selected), 'nodes': public_value([n for n in data['nodes'] if n['id'] in ids]),
            'truncated': len(found) > limit, 'summary': data['summary'], 'qualification': data['qualification']}
    if str(params.get('related')) == '1' and entity:
        result['related'] = related_readers(data, entity)
    if data.get('pending_collections'): result['pending_collections']=data['pending_collections']
    return result


def related_readers(data, entity):
    """Two-edge evidence walks matching the hosted, frozen-context limits."""
    from collections import defaultdict
    usable = {'captured_as','excerpt_of','has_native_identifier','source_names_county','listed_filing_source','order_in_mdl','cites_mdl','cites_docket','lists_docket_in_schedule','contains','county_context','named_court','serves_geography_as_source_reported'}
    bridges = {'url','county','mdl','rule','docket-reference','court-source'}
    records = {r['id']: r for r in data['resources']}
    adjacent = defaultdict(list)
    for edge in data['edges']:
        adjacent[edge['source']].append(edge)
        if edge['source'] != edge['target']: adjacent[edge['target']].append(edge)
    def other(edge, ident): return edge['target'] if edge['source'] == ident else edge['source']
    first = adjacent[entity][:500]
    edges = [e for e in first if e['relation'] in usable]
    candidates = sorted({other(e,entity) for e in edges if other(e,entity).split(':')[0] in bridges})
    selected = candidates[:8]
    incomplete = len(adjacent[entity]) > 500 or len(candidates) > 8
    paths = [(other(e,entity), [e]) for e in edges if other(e,entity) in records]
    for bridge in selected:
        incomplete |= len(adjacent[bridge]) > 500
        for edge in adjacent[bridge][:500]:
            ident = other(edge,bridge)
            if edge['relation'] not in usable or ident == entity or ident not in records: continue
            for first_edge in edges:
                if other(first_edge,entity) == bridge: paths.append((ident,[first_edge,edge]))
    def kind(path):
        rels = {e['relation'] for e in path}
        for relations,key,label in [
            ({'has_native_identifier'},'identifier','Same exact recorded rule identifier'),
            ({'cites_docket','lists_docket_in_schedule'},'docket','Same printed docket reference'),
            ({'order_in_mdl','cites_mdl'},'mdl','Orders or citations connected to this MDL'),
            ({'source_names_county','county_context'},'county','Documents that name this county'),
            ({'serves_geography_as_source_reported'},'county','Court sources linked to this county'),
            ({'named_court'},'court','Documents naming the same court'),
            ({'listed_filing_source'},'listed_source','Saved readers of listed filing sources'),
            ({'excerpt_of'},'source','Sections and captures of the same source'),
            ({'contains'},'sections','Sections contained in this source')]:
            if rels & relations: return key,label
        return 'source','Captures of the same source'
    groups = {}
    for ident,path in paths:
        if ident == entity: continue
        key,label = kind(path)
        group = groups.setdefault(key, {'key':key,'label':label,'rows':{}})
        item = group['rows'].setdefault(ident,dict(public_record(records[ident]),evidence_paths=[]))
        cleaned = public_value(path)
        if cleaned not in item['evidence_paths'] and len(item['evidence_paths']) < 3: item['evidence_paths'].append(cleaned)
    clusters = []
    for key,group in sorted(groups.items()):
        rows = sorted(group['rows'].values(),key=lambda r:((r.get('title') or r['id']).casefold(),r['id']))
        clusters.append({'key':key,'label':group['label'],'matched':len(rows),'items':rows[:6],'truncated':len(rows)>6})
    return {'available':True,'entity':entity,'clusters':clusters,'matched':len({ident for ident,_ in paths if ident != entity}),
            'incomplete':bool(incomplete),'pending_entities':[],
            'walk':{'max_hops':2,'max_bridges':8,'visited_bridges':len(selected),'bridge_candidates':len(candidates),'max_edges_per_entity':500,'max_items_per_cluster':6},
            'qualification':'These are recorded source connections, not legal applicability, current MDL membership, or a finding that two documents are equivalent. Separate captures and versions are retained.'}

def asset(ident, kind='original', folder=None):
    if kind not in ('text','original'): return None
    data = state(folder)
    if not data: return None
    r = next((r for r in data['resources'] if r['id'] == ident), None)
    if not r: return None
    root = Path(folder) if folder is not None else Path(r.get('_collection_folder') or DATA)
    if folder is None and root.resolve() not in {Path(DATA).resolve(),Path(COUNTY_DATA).resolve()}: return None
    path = supplements._confined(root, r.get('text_file' if kind == 'text' else 'original_file'))
    if not path: return None
    try: body = path.read_bytes()
    except OSError: return None
    if hashlib.sha256(body).hexdigest() != r.get('text_sha256' if kind == 'text' else 'raw_sha256'): return None
    mime = 'text/plain; charset=utf-8' if kind == 'text' else r.get('mime_type') or 'application/octet-stream'
    return body, mime, path.name
