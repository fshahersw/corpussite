"""Bounded, offline presentation adapter data; external originals are read only."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DOWNLOADS = ROOT.parent
DISCOVERY = ROOT / 'sources/local_asset_discovery_20260918'
MDL = DOWNLOADS / 'LAWONTOLOGY'
SETTLEMENT = DOWNLOADS / 'Court-Document-Library/07-Settlement-References'
REGISTRY = DOWNLOADS / 'Court-Library-Expansion-2026-09-12'
NOW = datetime.now(timezone.utc).isoformat()


def sha(path):
    with path.open('rb') as fh:
        return hashlib.file_digest(fh, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def rows(path):
    with path.open(encoding='utf-8-sig') as fh:
        return [json.loads(line) for line in fh if line.strip()]


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def contained(path, base):
    path = path.resolve(strict=True)
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError('Outside declared source root')
    return path


ASSETS = {}
PROVENANCE = []


def allow(asset_id, path, base, mime, expected=None, attachment=False, evidence=None):
    path = contained(path, base)
    digest = sha(path)
    if expected and digest != expected:
        raise ValueError('Artifact digest mismatch: ' + asset_id)
    ASSETS[asset_id] = dict(id=asset_id, path=path.as_posix(), allowed_root=base.resolve().as_posix(),
                            mime=mime, sha256=digest, bytes=path.stat().st_size,
                            attachment=attachment, evidence=evidence or {})
    return asset_id


def safe_svg(path):
    raw = path.read_text(encoding='utf8')
    if re.search(r'<!DOCTYPE|<!ENTITY', raw, re.I):
        raise ValueError('SVG entity declaration')
    tree = ET.fromstring(raw)
    for node in tree.iter():
        tag = node.tag.rsplit('}', 1)[-1].lower()
        if tag in {'script', 'foreignobject', 'iframe', 'object', 'embed', 'image', 'a', 'animate', 'set'}:
            raise ValueError('SVG active/external element: ' + tag)
        for name, val in node.attrib.items():
            key = name.rsplit('}', 1)[-1].lower()
            if key.startswith('on') or (key in {'href', 'src'} and not val.startswith('#')):
                raise ValueError('SVG external/event attribute')
            if re.search(r'url\(\s*[\'"]?(?!#)[^)]', val, re.I) or '@import' in val.lower():
                raise ValueError('SVG external style reference')
        if tag == 'style' and node.text and (re.search(r'url\(\s*[\'"]?(?!#)[^)]', node.text, re.I) or '@import' in node.text.lower()):
            raise ValueError('SVG external stylesheet')


def build():
    OUT.mkdir(exist_ok=True)
    (OUT / 'assets').mkdir(exist_ok=True)
    (OUT / 'texts').mkdir(exist_ok=True)
    ledger_path = ROOT / 'sources/counties/local_documents_20260914/county_ledger.jsonl'
    ledger = {r['geoid']: r for r in rows(ledger_path)}
    court_path = DISCOVERY / 'court_links.jsonl'
    courts = rows(court_path)
    explicit = {'LC:pa_philadelphia': ('42101', 'Philadelphia County', 'Pennsylvania'),
                'LC:ny_new_york': ('36061', 'New York County', 'New York'),
                'LC:ca_los_angeles': ('06037', 'Los Angeles County', 'California'),
                'LC:ca_alameda': ('06001', 'Alameda County', 'California'),
                'LC:ca_san_francisco': ('06075', 'San Francisco County', 'California')}
    visuals = []
    for court in courts:
        if court['court_id'] not in explicit:
            continue
        geoid, county, state = explicit[court['court_id']]
        assert ledger[geoid]['county'] == county and ledger[geoid]['state'] == state
        county_stem = county.removesuffix(' County')
        assert county_stem in court['name']
        # The observed registry entry supplies state and court identity; not visual recognition.
        registry_rows = read(REGISTRY / 'local/registry.json')
        registry_row = next(r for r in registry_rows if r['collection_id'] == court['court_id'])
        assert registry_row['jurisdiction'] == state and registry_row['name'] == court['name']
        image = court['primary_image']
        source = Path(image['absolute_path'])
        assert sha(source) == image['sha256']
        if image['mime'] == 'image/svg+xml':
            safe_svg(source)
        target = OUT / 'assets' / (image['sha256'] + source.suffix.lower())
        if not target.exists():
            shutil.copyfile(source, target)
        asset_id = 'court-image-' + image['sha256'][:24]
        allow(asset_id, target, OUT, image['mime'], image['sha256'], evidence={
            'original_path': source.as_posix(), 'source_manifest': image['source_manifest'],
            'court_links_path': court_path.relative_to(ROOT).as_posix(), 'court_links_sha256': sha(court_path)})
        visual = dict(geoid=geoid, county=county, state=state, court_id=court['court_id'],
                      court_label=court['name'], asset_id=asset_id,
                      image_kind=court['primary_kind'], identity_scope=court['primary_identity_scope'],
                      is_hero=False, source_url=court['homepage'], width=image['width'], height=image['height'],
                      caption=('Shared judiciary mark observed on this court site' if court['primary_identity_scope'] == 'shared_state_judiciary_mark'
                               else 'Court site icon' if court['primary_kind'] == 'favicon' else 'Court site mark'),
                      captured_at=court['last_retrieved_at'], permission_status=court['permission_status'],
                      identity_basis='Exact saved local court registry ID, court county name, explicit state and Census name/state match',
                      provenance={'registry_path': (REGISTRY / 'local/registry.json').as_posix(),
                                  'registry_sha256': sha(REGISTRY / 'local/registry.json'),
                                  'county_ledger_sha256': sha(ledger_path), 'source_image_sha256': image['sha256']})
        visuals.append(visual)
    save('county_visuals.json', visuals)

    # Only 24 actual MDL originals are allowlisted. Native page bodies are re-used as labeled derivatives.
    docpath = MDL / 'deliverables/corpus-native-text/documents.jsonl'
    docs = rows(docpath)
    selected = docs[:24]
    wanted = {r['source_sha256']: r for r in selected}
    pages = {key: [] for key in wanted}
    with (MDL / 'deliverables/corpus-native-text/pages.jsonl').open(encoding='utf8') as fh:
        for line in fh:
            row = json.loads(line)
            digest = row['source_sha256']
            if digest in wanted:
                body = row.get('native_text', '')
                assert hashlib.sha256(body.encode('utf8')).hexdigest() == row['native_text_sha256']
                pages[digest].append(row)
            if all(len(pages[key]) == wanted[key]['page_count'] for key in wanted):
                break
    mdl_items = []
    for doc in selected:
        digest = doc['source_sha256']
        path = MDL / doc['relative_path']
        asset_id = allow('mdl-pdf-' + digest[:24], path, MDL, 'application/pdf', digest,
                         evidence={'native_document_record_id': doc['record_id'], 'manifest': docpath.as_posix()})
        page_rows = sorted(pages[digest], key=lambda r: r['page_ordinal'])
        assert len(page_rows) == doc['page_count']
        body = '\n\n'.join('[Page %d]\n%s' % (p['page_ordinal'], p['native_text']) for p in page_rows)
        textpath = OUT / 'texts' / (digest + '.txt')
        textpath.write_text(body, encoding='utf8')
        text_id = allow('mdl-text-' + digest[:24], textpath, OUT, 'text/plain; charset=utf-8', attachment=True,
                        evidence={'parent_sha256': digest, 'pages': [{'id': p['record_id'], 'text_sha256': p['native_text_sha256']} for p in page_rows]})
        mdl_items.append(dict(id='mdl-' + digest[:24], title=path.stem, resource_kind='court_document',
                              description='%d-page saved PDF from the MDL 3080 collection.' % doc['page_count'],
                              court_label='U.S. District Court, District of New Jersey — MDL 3080 collection',
                              state=None, source_url=None, source_date=None, asset_id=asset_id, text_asset_id=text_id,
                              excerpt=page_rows[0]['native_text'][:1800] if page_rows else '',
                              quality_notes=['Title transcribed from saved filename; collection affiliation is not a claim that every document originated in this court.',
                                             'Native text derivative; no assertion of a complete docket or current law.'],
                              metadata={'raw_sha256': digest, 'docket_iri': doc['docket_iri'], 'entry_iri': doc['entry_iri'],
                                        'source_record_id': doc['source_record_id'], 'page_count': doc['page_count']}))

    catalog = read(SETTLEMENT / 'catalog/catalog.json')
    settlement_items = []
    evidence_by_record = {}
    evidence_docs = read(SETTLEMENT / 'documents.json')
    for doc in evidence_docs:
        if doc.get('status') != 'downloaded':
            continue
        aid = allow('settlement-pdf-' + doc['sha256'][:24], SETTLEMENT / doc['path'], SETTLEMENT,
                    'application/pdf', doc['sha256'], evidence={'source_url': doc['url'], 'retrieved_at': doc['retrieved_at']})
        evidence_by_record.setdefault(doc['record_id'], []).append(dict(id='settlement-document-' + doc['id'],
            title=doc['title'], asset_id=aid, source_url=doc['url'], resource_kind=doc['kind'],
            source_date=doc.get('source_date'), description=doc['scope'], quality_notes=[doc['rights_note']]))
    for record in catalog['records']:
        pub, review = record['publisher'], record.get('review') or {}
        documents = evidence_by_record.get(review.get('id'), [])
        settlement_items.append(dict(id=record['id'], title=review.get('title') or pub['title'],
            resource_kind='settlement_reference', description=review.get('summary') or pub.get('estimated_payout') or '',
            court_label=review.get('court'), state=None, source_url=pub.get('url'), source_date=None,
            asset_id=documents[0]['asset_id'] if documents else None, text_asset_id=None, excerpt=review.get('eligibility_scope') or '',
            quality_notes=record.get('quality_notes', []) + ['Saved settlement status and deadline reflect the source snapshot, not live verification.'],
            status=review.get('status') or pub.get('status'), claim_deadline=review.get('claim_deadline') or pub.get('claim_deadline'),
            status_as_of=review.get('assessment_date') or pub.get('last_verified'),
            proof_requirement=review.get('proof_requirement') or pub.get('proof_required'),
            official_url=review.get('official_url') or pub.get('official_settlement_url'),
            applicable_states_as_published=pub.get('applicable_states'),
            evidence_label='Selected fields reviewed against saved official sources' if review else 'Publisher reference; not independently reviewed here',
            attribution='Data: SettleSignal (settlesignal.com)', attribution_url='https://settlesignal.com/', documents=documents,
            metadata={'publisher_record_sha256': record['publisher_record_sha256'],
                      'publisher_assertions_are_independent_verification': False,
                      'review': review, 'publisher': pub}))

    registry_items = []
    registry_counts = {}
    for family in ['federal', 'states', 'local']:
        path = REGISTRY / family / 'registry.json'
        records = read(path)
        registry_counts[family] = len(records)
        for r in records:
            registry_items.append(dict(id='registry-' + r['collection_id'].replace(':', '-'), title=r['name'],
                resource_kind='court_registry', description=r.get('notes') or '', court_label=r['name'],
                state=r.get('jurisdiction') if family != 'federal' else None, source_url=r.get('homepage'),
                source_date=None, asset_id=None, text_asset_id=None, excerpt='',
                quality_notes=[r.get('coverage_boundary') or 'A saved registry entry does not establish complete local document coverage.'],
                registry_family=family, links=[{'title': 'Forms and court resources', 'url': u} for u in r.get('forms_pages', [])],
                metadata={'registry_path': path.as_posix(), 'registry_sha256': sha(path), 'source_record': r}))
    image_rows = rows(DISCOVERY / 'image_files.jsonl')
    visual_by_court = {v['court_id']: v for v in visuals}
    asset_items = [dict(id='court-asset-' + r['court_id'].replace(':', '-'), title=r['name'], resource_kind='court_visual_reference',
                  description=('Court mark or site icon saved' if r.get('primary_image') else 'Text fallback; no accepted primary image'),
                  court_label=r['name'], state=None, source_url=r['homepage'], source_date=None,
                  asset_id=visual_by_court.get(r['court_id'], {}).get('asset_id'), text_asset_id=None, excerpt='',
                  quality_notes=['Image identity is limited to its recorded court or judiciary scope.'],
                  metadata={'court_id': r['court_id'], 'visual_status': r['visual_status'], 'primary_identity_scope': r.get('primary_identity_scope'),
                            'permission_status': r.get('permission_status')}) for r in courts]
    cards = [
        dict(id='mdl-3080', title='MDL 3080 · Insulin Pricing Litigation', description='Saved filings, orders and exhibits from the New Jersey multidistrict litigation collection.',
             record_count=len(docs), count_label='saved PDF documents', preview_count=len(mdl_items), source_url=None,
             scope_note='A 24-document preview of the local library. Not a complete PACER docket; collection-level New Jersey affiliation does not identify every originating court.', facets={'native_text_pages': sum(d['page_count'] for d in docs)}),
        dict(id='settlements', title='Settlement Reference Library', description='Settlement references with published deadlines, status, official links and preserved review qualifications.',
             record_count=len(settlement_items), count_label='settlement references', preview_count=len(settlement_items), source_url='https://settlesignal.com/',
             scope_note='Saved September 2026 source snapshots. Nine records have bounded source reviews; four official PDFs are available. Status is not live.', facets={'reviewed_records': sum(bool(r.get('review')) for r in catalog['records']), 'saved_pdfs': len(evidence_docs)},
             attribution='Data: SettleSignal (settlesignal.com)', attribution_url='https://settlesignal.com/'),
        dict(id='court-registries', title='Official Court Website Directory', description='Observed court homepages, forms pages and registry provenance across federal, state and selected local courts.',
             record_count=len(registry_items), count_label='registry entries', preview_count=len(registry_items), source_url=None,
             scope_note='56 state/DC/territory entries, 207 federal or special entries and six selected local entries. Counts are saved registry entries, not all counties or distinct courts.', facets=registry_counts),
        dict(id='court-assets', title='Court and Judiciary Visual Library', description='Verified local court marks, seals, site icons and their observed identity associations.',
             record_count=len(image_rows), count_label='unique saved images', preview_count=len(asset_items), source_url=None,
             scope_note='269 court/judiciary identity records. Five county court associations use four copied images; other images remain reference-only. Judge portraits are integrated separately.',
             facets={'court_entries': len(courts), 'primary_images': sum(bool(r.get('primary_image')) for r in courts), 'county_visual_associations': len(visuals)})
    ]
    for card in cards:
        card['updated_at'] = NOW
    save('collections.json', cards)
    for name, items in [('mdl-3080', mdl_items), ('settlements', settlement_items), ('court-registries', registry_items), ('court-assets', asset_items)]:
        with (OUT / (name + '.jsonl')).open('w', encoding='utf8') as fh:
            for item in items:
                fh.write(json.dumps(item, ensure_ascii=False) + '\n')
    save('assets.json', list(ASSETS.values()))
    summary = dict(built_at=NOW, external_files_modified=0, copied_images=len({v['asset_id'] for v in visuals}),
                   county_visual_associations=len(visuals), allowlisted_assets=len(ASSETS),
                   original_pdfs_allowlisted=len(selected) + len(evidence_docs), copied_original_documents=0,
                   native_text_derivatives=len(selected), collections=[{k:c[k] for k in ['id','record_count','preview_count']} for c in cards])
    save('summary.json', summary)
    save('validation.json', {'status': 'passed', 'validated_at': NOW, 'artifact_count': len(ASSETS),
                             'errors': [], 'files': {p.name: sha(p) for p in OUT.iterdir() if p.is_file() and p.name not in {'validation.json', 'build.py'}}})
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    build()
