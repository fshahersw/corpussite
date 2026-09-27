"""Hash-gated, read-only historical court coverage and existing-original crosswalk."""
from __future__ import annotations
import hashlib, json, re, threading
from collections import Counter
from pathlib import Path
from urllib.parse import quote

DATA = Path(__file__).resolve().parents[2] / 'sources/docsupload_coverage_20260927'
_LOCK = threading.Lock()
_CACHE = {}
VIEW = 'court-coverage'

def _load():
    gate_path = DATA / 'validation.json'
    gate_bytes = gate_path.read_bytes(); gate = json.loads(gate_bytes)
    if gate.get('status') != 'passed' or gate.get('ready') is not True:
        raise ValueError('Publication unavailable')
    entries = gate.get('data_files', [])
    expected = {e.get('path'): e for e in entries}
    if not {'courts.jsonl', 'documents.jsonl'}.issubset(expected): raise ValueError('Missing bound tables')
    signature = [hashlib.sha256(gate_bytes).hexdigest()]
    for rel in expected:
        path = (DATA / rel).resolve()
        if not path.is_relative_to(DATA.resolve()) or Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise ValueError('Invalid evidence path')
        st = path.stat(); signature.append((rel, st.st_size, st.st_mtime_ns))
    signature = (str(DATA), tuple(signature))
    with _LOCK:
        if _CACHE.get('signature') == signature: return _CACHE['data']
        blobs = {}
        for rel, info in expected.items():
            raw = (DATA / rel).read_bytes()
            if hashlib.sha256(raw).hexdigest() != info.get('sha256'): raise ValueError('Changed publication evidence')
            if rel.endswith('.jsonl'):
                blobs[rel] = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line]
                if info.get('rows') is not None and len(blobs[rel]) != info['rows']: raise ValueError('Row count mismatch')
        courts, documents = blobs['courts.jsonl'], blobs['documents.jsonl']
        if len({r['id'] for r in courts + documents}) != len(courts) + len(documents): raise ValueError('Duplicate identity')
        data = {'courts': courts, 'documents': documents, 'by_id': {r['id']: r for r in courts + documents},
                'qualification': gate['qualification'], 'source_as_of': gate['source_as_of'], 'counts': gate['counts']}
        _CACHE.update(signature=signature, data=data)
        return data

def _one(p, k, default=''):
    x = (p or {}).get(k, default)
    return str(x[0] if isinstance(x, (list, tuple)) and x else x).strip()

def _integer(p, k, default, hi):
    try: return min(hi, max(1, int(_one(p, k, default))))
    except (TypeError, ValueError): return default

def _options(values):
    counts = Counter(v for v in values if v)
    return [{'value': v, 'label': v, 'count': n} for v, n in sorted(counts.items())]

def listing(params=None):
    try: data = _load()
    except (OSError, ValueError, KeyError, TypeError):
        return {'available': False, 'reason': 'Court coverage publication is unavailable or changed.', 'total': 0, 'results': []}
    collection, q = _one(params, 'collection'), _one(params, 'q').casefold().split()
    mode = 'documents' if collection or _one(params, 'mode') == 'documents' else 'courts'
    source = data['documents'] if mode == 'documents' else data['courts']
    rows = source
    if collection: rows = [r for r in rows if collection in r['known_collection_ids']]
    jurisdiction, level, availability = _one(params, 'jurisdiction'), _one(params, 'level'), _one(params, 'availability')
    if jurisdiction: rows = [r for r in rows if jurisdiction in (r.get('jurisdictions') or [r.get('jurisdiction')])]
    if level and mode == 'courts': rows = [r for r in rows if r['level'] == level]
    if availability and mode == 'courts': rows = [r for r in rows if bool(r['linked_documents']) == (availability == 'saved')]
    if q:
        rows = [r for r in rows if all(t in ' '.join([r['title'], str(r.get('jurisdiction', '')), ' '.join(r.get('jurisdictions', [])),
            ' '.join(r.get('source_courts', [])), r.get('collection_id', '')]).casefold() for t in q)]
    rows = sorted(rows, key=lambda r: r['title'].casefold())
    page, limit = _integer(params, 'page', 1, 100000), _integer(params, 'limit', 25, 100)
    filters = [{'name': 'q', 'label': 'Find court or document', 'type': 'search'},
        {'name': 'mode', 'label': 'Browse', 'type': 'select', 'options': [{'value': 'courts', 'label': 'Court coverage'}, {'value': 'documents', 'label': 'Existing documents'}]},
        {'name': 'jurisdiction', 'label': 'Jurisdiction', 'type': 'select', 'options': _options(c['jurisdiction'] for c in data['courts'])},
        {'name': 'collection', 'label': 'Court / source collection', 'type': 'select', 'options': [{'value': c['collection_id'], 'label': c['title'], 'count': c['linked_documents']} for c in sorted(data['courts'], key=lambda c:c['title'])]},
        {'name': 'level', 'label': 'Court level (coverage view)', 'type': 'select', 'options': _options(c['level'] for c in data['courts'])},
        {'name': 'availability', 'label': 'Coverage view availability', 'type': 'select', 'options': [{'value': 'saved', 'label': 'Has exact document links'}, {'value': 'missing', 'label': 'No exact document links'}]}]
    results = []
    for r in rows[(page-1)*limit:page*limit]:
        if mode == 'documents':
            results.append({'id': r['id'], 'title': r['title'], 'subtitle': ' · '.join(r['source_courts']),
                'cells': {'name': r['title'], 'jurisdiction': ', '.join(r['jurisdictions']), 'coverage': r['format'].upper()},
                'badges': ['Existing original', 'Readable text available' if r['text_id'] else 'Text not indexed'],
                'links': [{'label': 'Read saved document', 'url': '#record/' + r['record_id']}]})
        else:
            results.append({'id': r['id'], 'title': r['title'], 'subtitle': r['level'] + ' · ' + r['jurisdiction'],
                'cells': {'name': r['title'], 'jurisdiction': r['jurisdiction'], 'coverage': str(r['linked_documents']) + ' exact document links'},
                'badges': ['Snapshot ' + r['source_as_of'][:10]],
                'links': [{'label': 'Browse linked documents', 'url': '#' + VIEW + '?collection=' + quote(r['collection_id'], safe='')} ] if r['linked_documents'] else []})
    return {'available': True, 'total': len(rows), 'page': page, 'limit': limit, 'qualification': data['qualification'],
        'filters': filters, 'columns': [{'key': 'name', 'label': 'Court / document'}, {'key': 'jurisdiction', 'label': 'Jurisdiction'}, {'key': 'coverage', 'label': 'Saved coverage'}],
        'results': results, 'counts': data['counts'], 'source_as_of': data['source_as_of']}

def detail(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'(?:coverage_[a-f0-9]{20}|document_[a-f0-9]{64})', identifier): return None
    try: data = _load()
    except (OSError, ValueError, KeyError, TypeError): return None
    r = data['by_id'].get(identifier)
    if r is None: return None
    sections, links = [], []
    if identifier.startswith('document_'):
        facts = [['Court labels (source catalog)', '; '.join(r['source_courts'])], ['Jurisdictions (source catalog)', '; '.join(r['jurisdictions'])],
            ['Original format', r['format'].upper()], ['Bytes', str(r['bytes'])], ['Original SHA-256', r['sha256']],
            ['Library snapshot', r['source_as_of']], ['Originally saved', r.get('saved_at') or 'Not recorded'],
            ['Source date', r['source_date'] or 'Not stated'], ['Date meaning', r['source_date_kind'] or 'Not stated'],
            ['Text availability', 'Indexed text available; review extraction notes in reader' if r['text_id'] else 'No indexed text'],
            ['Language', r['language']], ['Source review status', r['review_status']]]
        links = [{'label': 'Read saved document', 'url': '#record/' + r['record_id']}]
        links += [{'label': 'Original publisher', 'url': u} for u in r['source_urls']]
        sections = [{'heading': 'Document scope', 'text': r['scope_note'] or data['qualification']},
                    {'heading': 'Extraction and title evidence', 'text': '\n\n'.join(x for x in [r['language_note'], r['title_evidence'], r['technical_flags']] if x)},
                    {'heading': 'Exact-source association', 'text': r['association_basis']}]
    else:
        docs = [d for d in data['documents'] if r['collection_id'] in d['known_collection_ids']]
        facts = [['Source collection', r['collection_id']], ['Jurisdiction', r['jurisdiction']], ['Court level', r['level']],
            ['Library snapshot', r['source_as_of']], ['Documents reported by source', str(r['source_reported_documents'])],
            ['Exact document links in this crosswalk', str(r['linked_documents'])], ['Observed URLs (source report)', str(r['observed_urls'])],
            ['URLs with local copies (source report)', str(r['source_reported_acquired_urls'])],
            ['URLs without local copies (source report)', str(r['source_reported_unacquired_urls'])]]
        links = ([{'label': 'Court website (saved source)', 'url': r['homepage']}] if r['homepage'] else [])
        if r['existing_court_id']: links.append({'label': 'Court registry profile', 'url': '#courts/' + quote(r['existing_court_id'], safe='')})
        if docs: links.append({'label': 'Browse all ' + str(len(docs)) + ' exact document links', 'url': '#' + VIEW + '?collection=' + quote(r['collection_id'], safe='')})
        if r['forms_pages']: sections.append({'heading': 'Observed court resource pages', 'items': [{'title': u, 'links': [{'label': 'Visit source page', 'url': u}]} for u in r['forms_pages']]})
        sections.extend([{'heading': 'Coverage scope', 'text': r['scope_note'] or data['qualification']},
            {'heading': 'Source verification notes', 'text': '\n\n'.join(x for x in [r['verification'], r['notes']] if x)}])
        if r['source_reported_documents'] != r['linked_documents']:
            sections.append({'heading': 'Count distinction', 'text': 'The source roster counts and this exact collection-ID crosswalk differ. Legacy aliases are not inferred or merged. Use the separate counts above.'})
        if docs: sections.append({'heading': 'Saved documents (first 20; use browse for all)', 'items': [{'title': d['title'], 'subtitle': d['format'].upper(), 'links': [{'label': 'Read saved document', 'url': '#record/' + d['record_id']}]} for d in docs[:20]]})
    return {'id': r['id'], 'title': r['title'], 'subtitle': 'Historical court-library coverage', 'facts': facts,
            'links': links, 'sections': sections, 'qualification': data['qualification']}

def original(identifier):
    """Originals use the existing document reader; this adapter exposes no new filesystem route."""
    return None
