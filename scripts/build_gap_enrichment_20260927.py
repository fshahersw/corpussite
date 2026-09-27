"""Build a dated, evidence-preserving addition without modifying base catalogs."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import hashlib, json, re, shutil, sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'sources/gap_fill_20260927'
OUTPUT = ROOT / 'sources/gap_enrichment_20260927'

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''): h.update(block)
    return h.hexdigest()

def rows(path):
    with path.open(encoding='utf-8-sig') as stream:
        for number, line in enumerate(stream, 1):
            if line.strip(): yield number, json.loads(line)

def url(value):
    try:
        parsed = urlsplit(str(value or ''))
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.username or parsed.password: return None
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ''))
    except ValueError: return None

def source_file(value, lane):
    if not value: return None
    p = Path(value)
    candidates = [p] if p.is_absolute() else [ROOT / p, lane / p]
    for p in candidates:
        p = p.resolve()
        if p.is_relative_to(ROOT) and p.is_file(): return p
    return None

def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def build():
    if (OUTPUT / 'validation.json').exists() and json.loads((OUTPUT / 'validation.json').read_text(encoding='utf-8')).get('ready') is True:
        raise SystemExit('Published addition already exists; use a new dated snapshot instead of replacing it')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'assets').mkdir(exist_ok=True)
    (OUTPUT / 'text').mkdir(exist_ok=True)
    resources, held, nodes, edges, artifacts = [], [], {}, {}, {}
    source_ids, duplicate_keys, input_ids, duplicates = {}, {}, set(), 0
    def node(ident, label, kind, **attrs):
        nodes.setdefault(ident, {'id': ident, 'label': label, 'kind': kind, **attrs})
        return ident
    def edge(source, target, relation, evidence, **attrs):
        ident = hashlib.sha256(json.dumps([source, target, relation, evidence, attrs], sort_keys=True).encode()).hexdigest()
        edges.setdefault(ident, {'id': ident, 'source': source, 'target': target, 'relation': relation, 'evidence': evidence, **attrs})
    def url_node(value, title=''):
        value = url(value)
        if not value: return None
        return node('url:' + hashlib.sha256(value.encode()).hexdigest()[:24], title or value, 'source', url=value)

    # Existing reviewed source-index mappings are references, not new downloads or legal applicability findings.
    sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
    import county_filing
    index = county_filing._state()
    if not index: raise ValueError('Existing county filing index failed its hash gate')
    index_path = county_filing.DATA / 'county_sources.jsonl'
    index_sha = digest(index_path)
    for geoid, row in sorted(index['counties'].items()):
        state = node('state:' + row['state'], row['state'], 'state')
        county = node('county:' + geoid, row['name'], 'county', state=row['state'], fips=geoid)
        evidence = {'source_sha256': index_sha, 'source_artifact': 'county_filing_sources_20260919/county_sources.jsonl', 'pointer': 'geoid=' + geoid}
        edge(county, state, 'in_state', evidence, review_status='saved_identity_mapping')
        for i, item in enumerate(row['items']):
            target = url_node(item.get('url'), item.get('title'))
            if target:
                edge(county, target, 'listed_filing_source', dict(evidence, item_index=i, source_url=item['url']),
                     scope=item.get('scope'), scope_label=item.get('scope_label'), resource_type=item.get('kind'),
                     source_as_of=item.get('as_of'), review_status='saved_index_mapping')

    for manifest in sorted(INPUT.glob('*/manifest.jsonl')):
        lane, manifest_sha = manifest.parent, digest(manifest)
        for line_number, record in rows(manifest):
            ident = str(record.get('id') or '')
            input_ids.add((lane.name, ident))
            try:
                if not ident or not record.get('title') or not url(record.get('source_url')): raise ValueError('Missing identity, title or public source URL')
                if record.get('status') in ('held', 'failed', 'blocked', 'rejected') or str(record.get('review_status') or '').startswith('held'): raise ValueError('Collector held this record')
                original = source_file(record.get('raw_path'), lane)
                if original is None or not record.get('raw_sha256') or digest(original) != record['raw_sha256']: raise ValueError('Original missing or hash mismatch')
                text = source_file(record.get('text_path'), lane)
                if text and (not record.get('text_sha256') or digest(text) != record['text_sha256']): raise ValueError('Text hash mismatch')
                # A court-specific excerpt may share the original compilation.
                # Deduplicate identical readers, while originals are stored once by hash.
                key = (url(record['source_url']), record['raw_sha256'], record.get('text_sha256') or '')
                if key in duplicate_keys:
                    duplicates += 1
                    source_ids[(lane.name, ident)] = duplicate_keys[key]
                    continue
                canonical = 'addition:' + hashlib.sha256((lane.name + ':' + ident).encode()).hexdigest()[:24]
                duplicate_keys[key] = canonical; source_ids[(lane.name, ident)] = canonical
                suffix = original.suffix.lower()
                if suffix not in ('.pdf', '.html', '.htm', '.doc', '.docx', '.xlsx', '.csv', '.xml', '.txt', '.json', '.zip'): suffix = '.bin'
                original_rel = 'assets/' + record['raw_sha256'] + suffix
                if not (OUTPUT / original_rel).exists(): shutil.copyfile(original, OUTPUT / original_rel)
                artifacts[original_rel] = {'path': original_rel, 'sha256': record['raw_sha256'], 'rows': 1}
                text_rel = None
                if text:
                    text_rel = 'text/' + record['text_sha256'] + '.txt'
                    if not (OUTPUT / text_rel).exists(): shutil.copyfile(text, OUTPUT / text_rel)
                    artifacts[text_rel] = {'path': text_rel, 'sha256': record['text_sha256'], 'rows': 1}
                r = {k: v for k, v in record.items() if k not in ('raw_path', 'text_path')}
                r.update(id=canonical, source_id=ident, lane=lane.name, original_file=original_rel, text_file=text_rel,
                         original_url='/api/enrichment/file?id=' + canonical + '&kind=original',
                         text_url='/api/enrichment/file?id=' + canonical + '&kind=text' if text_rel else None,
                         captured_at=record.get('captured_at'), review_status=record.get('review_status') or 'source_evidence_recorded',
                         validation={'manifest_sha256': manifest_sha, 'line': line_number, 'original_hash_verified': True, 'text_hash_verified': bool(text)})
                resources.append(r)
                resource_node = node(canonical, r['title'], 'document', resource_id=canonical, state=r.get('state'), resource_type=r.get('resource_type') or r.get('category'))
                publisher_node = url_node(r['source_url'], r['title'])
                edge(publisher_node, resource_node, 'captured_as', {'source_url': r['source_url'], 'source_sha256': r['raw_sha256']}, captured_at=r.get('captured_at'), review_status='hash_verified_capture')
                for alternate in [r.get('final_url')]:
                    alternate_node = url_node(alternate, r['title'])
                    if alternate_node and alternate_node != publisher_node:
                        edge(alternate_node, resource_node, 'captured_as', {'source_url': alternate, 'source_sha256': r['raw_sha256']}, review_status='hash_verified_capture')
            except (ValueError, OSError) as exc:
                held.append({'lane': lane.name, 'id': ident, 'reason': str(exc), 'manifest_sha256': manifest_sha, 'line': line_number})

    for record in resources:
        parent=record.get('parent_compilation_id')
        if not parent:continue
        canonical=source_ids.get((record['lane'],parent))
        if canonical is None:
            candidates={r['id'] for r in resources if r['source_id']==parent and r['raw_sha256']==record['raw_sha256']}
            canonical=next(iter(candidates)) if len(candidates)==1 else None
        record['source_parent_compilation_id']=parent
        record['parent_compilation_id']=canonical

    for file in sorted(INPUT.glob('*/nodes.jsonl')):
        for number, n in rows(file):
            ident = n.get('id');label=n.get('label') or n.get('title');evidence=n.get('evidence') or n.get('source_evidence')
            parent = source_ids.get((file.parent.name,n.get('record_id')))
            if n.get('record_id') and not parent:continue
            if ident and label and evidence:
                canonical='outline:'+hashlib.sha256((file.parent.name+':'+ident).encode()).hexdigest()[:24]
                source_ids[(file.parent.name,ident)]=canonical
                node(canonical,label,n.get('kind') or n.get('type') or 'source_reference',evidence=evidence,resource_id=parent,url=n.get('source_url'),native_id=n.get('native_id'))
    for file in sorted(list(INPUT.glob('*/edges.jsonl'))+list(INPUT.glob('*/hierarchy_edges.jsonl'))):
        for number, e in rows(file):
            source, target = e.get('source'), e.get('target')
            if any((file.parent.name, ident) in input_ids and (file.parent.name, ident) not in source_ids for ident in (source, target)):
                held.append({'lane': file.parent.name, 'edge_line': number, 'reason': 'Relationship endpoint was held'}); continue
            source = source_ids.get((file.parent.name, source)) or (url_node(source) if url(source) else source)
            target = source_ids.get((file.parent.name, target)) or (url_node(target) if url(target) else target)
            evidence = e.get('evidence')
            if not source or not target or not evidence or not e.get('relation'):
                held.append({'lane': file.parent.name, 'edge_line': number, 'reason': 'Missing relationship evidence'}); continue
            label = str(evidence.get('anchor_text') or evidence.get('label') or evidence.get('quote') or '').strip()
            if e['relation'] == 'links_to' and (source == target or re.fullmatch(r'(?:skip to (?:main )?content|home|homepage|logo|privacy(?: policy)?|terms(?: of use)?|contact(?: us)?|login|log in|sign in|facebook|twitter|instagram|linkedin)', label, flags=re.I)):
                held.append({'lane': file.parent.name, 'edge_line': number, 'reason': 'Navigation or self-link excluded'}); continue
            allowed_kinds = ('mdl', 'case', 'docket', 'docket-reference', 'state', 'county', 'court', 'rule', 'statute', 'hierarchy', 'source', 'document')
            if any(ident not in nodes and ident.split(':', 1)[0] not in allowed_kinds for ident in (source, target)):
                held.append({'lane': file.parent.name, 'edge_line': number, 'reason': 'Unresolved relationship endpoint'}); continue
            # Preserve explicitly supplied identifiers. They are not name-based identity merges.
            for ident in (source, target):
                if ident not in nodes:
                    kind = ident.split(':', 1)[0]
                    if kind not in ('mdl', 'case', 'docket', 'docket-reference', 'state', 'county', 'court', 'rule', 'statute', 'hierarchy', 'source', 'document'): kind = 'unresolved_reference'
                    label = ' · '.join(str(e.get(k)) for k in ('court_as_printed','docket_number') if e.get(k)) if ident == target and kind == 'docket-reference' else ident
                    node(ident, label or ident, kind)
            extra = {k: e[k] for k in ('scope','scope_label','qualification','native_target_exists','mdl_number','docket_number','court_as_printed') if k in e}
            edge(source, target, e['relation'], evidence, review_status=e.get('status') or 'source_evidence_recorded', **extra)

    bundle = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(), 'available': True,
              'qualification': 'Dated saved additions and evidence-backed links. A source index mapping is not a new download or a finding of current legal applicability. Only explicit identifiers link cases, courts and jurisdictions.',
              'resources': resources, 'nodes': list(nodes.values()), 'edges': list(edges.values())}
    summary = {'resources': len(resources), 'with_text': sum(bool(r['text_file']) for r in resources), 'original_documents': len({r['raw_sha256'] for r in resources}),
               'held': len(held), 'held_resources': sum('edge_line' not in r for r in held), 'held_relationships': sum('edge_line' in r for r in held), 'duplicates': duplicates,
               'graph_nodes': len(nodes), 'graph_edges': len(edges), 'by_lane': dict(Counter(r['lane'] for r in resources)),
               'by_state': dict(Counter(r.get('state') or 'unspecified' for r in resources)),
               'by_type': dict(Counter(r.get('resource_type') or r.get('category') or 'unknown' for r in resources)),
               'by_relation': dict(Counter(e['relation'] for e in edges.values()))}
    bundle['summary'] = summary
    write_json(OUTPUT / 'bundle.json', bundle); write_json(OUTPUT / 'held.json', held); write_json(OUTPUT / 'summary.json', summary)
    files = [{'path': name, 'sha256': digest(OUTPUT / name), 'rows': 1} for name in ('bundle.json', 'summary.json', 'held.json')] + list(artifacts.values())
    write_json(OUTPUT / 'validation.json', {'schema_version': 1, 'ready': False, 'status': 'awaiting_independent_audit', 'validated_at': bundle['generated_at'],
        'counts': summary, 'qualification': bundle['qualification'], 'data_files': files})
    print(json.dumps(summary))

if __name__ == '__main__': build()
