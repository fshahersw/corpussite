"""Small read-only evidence layer; all assets are confined and hash-verified."""
import hashlib, json, re, threading
from pathlib import Path
from urllib.parse import urlsplit
import supplements

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'sources/gap_enrichment_20260927'
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

def state(folder=None):
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

def integer(value, fallback, maximum):
    try: return max(1, min(maximum, int(value)))
    except (TypeError, ValueError): return fallback

def public_record(r):
    allowed = ('id', 'title', 'state', 'county_fips', 'county_geoids', 'mdl_number', 'resource_type', 'category', 'document_shape',
               'native_id', 'source_url', 'final_url', 'mime_type', 'captured_at', 'source_as_of', 'published_at', 'effective_from', 'filed_at',
               'jurisdiction', 'jurisdiction_scope', 'applicability', 'legal_currency_verified', 'qualification', 'caption_as_printed', 'caption_basis', 'quality_notes', 'source_evidence',
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
    return {'available': True, 'total': len(found), 'page': page, 'limit': limit, 'items': [public_record(r) for r in found[(page-1)*limit:page*limit]],
            'summary': data['summary'], 'generated_at': data['generated_at'], 'qualification': data['qualification'],
            'facets': {key: sorted({str(r.get(key)) for r in all_rows if r.get(key)}) for key in ('state','lane','resource_type','document_shape')}}

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
    return {'available': True, 'entity': entity, 'total': len(found), 'edges': public_value(selected), 'nodes': public_value([n for n in data['nodes'] if n['id'] in ids]),
            'truncated': len(found) > limit, 'summary': data['summary'], 'qualification': data['qualification']}

def asset(ident, kind='original', folder=None):
    if kind not in ('text','original'): return None
    folder = Path(folder or DATA); data = state(folder)
    if not data: return None
    r = next((r for r in data['resources'] if r['id'] == ident), None)
    if not r: return None
    path = supplements._confined(folder, r.get('text_file' if kind == 'text' else 'original_file'))
    if not path: return None
    try: body = path.read_bytes()
    except OSError: return None
    if hashlib.sha256(body).hexdigest() != r.get('text_sha256' if kind == 'text' else 'raw_sha256'): return None
    mime = 'text/plain; charset=utf-8' if kind == 'text' else r.get('mime_type') or 'application/octet-stream'
    return body, mime, path.name
