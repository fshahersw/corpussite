"""Incrementally publish a frozen capture checkpoint without repeating geography.

Existing artifacts are audited before reuse. Existing county associations and
reading copies remain unchanged; only content classifications are refreshed.
No network requests, acquisition, OCR, or main database writes are performed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import re
import sys

import build
from classify import VERSION, classify, classify_link

sys.path.insert(0, str(build.ROOT / 'scripts'))
from audit_county_litigation_20260919 import audit


def refresh_classification(row):
    """Reclassify source-bound saved text, preserving original review decisions."""
    row = copy.deepcopy(row)
    meta = row['metadata']
    text = build.readbound(build.HERE / row['text_path'], row['text_sha256'], build.HERE).decode('utf-8')
    artifacts = meta.get('artifacts') or []
    original = next((item for item in artifacts if item['path'] == row['raw_path']), {})
    mime = original.get('mime_type') or original.get('mime') or ''
    links = meta.get('related_links') or []

    # Older link inventories did not recognize proposed-order instruction PDFs.
    # Recover their literal hrefs from the saved HTML only for relevant pages.
    if row['resource_kind'] == 'filing_guidance':
        html = next((item for item in artifacts if item.get('mime_type') == 'text/html'), None)
        if html:
            raw = build.readbound(build.HERE / html['path'], html['sha256'], build.HERE)
            from bs4 import BeautifulSoup
            links = []
            seen = set()
            for anchor in BeautifulSoup(raw, 'html.parser').find_all('a', href=True):
                url = build.safe_target(row.get('canonical_url') or row['source_url'], anchor['href'])
                if not url or not build.public_url(url) or url in seen:
                    continue
                label = anchor.get_text(' ', strip=True)
                hint = classify_link(label, url)
                if hint['eligible']:
                    links.append({'url': url, 'label': label, 'resource_type_hint': hint['resource_type'],
                                  'relationship': 'observed_link', 'applicability_inherited': False})
                    seen.add(url)
            meta['related_links'] = links

    classification = classify(row['title'], text, row.get('canonical_url') or row['source_url'], mime, links)
    prior = meta.get('semantic_review') or {}
    if prior.get('reviewed') and any(word in (prior.get('actual_resource_kind') or '').lower()
                                     for word in ('election', 'quorum', 'business', 'permit', 'zoning', 'facility', 'recreation')):
        classification.update(resource_type='source_directory', document_shape='reviewed_non_litigation_reference',
                              substantive=False, status='preserved_prior_negative_review',
                              evidence=[{'field': 'prior_hash_bound_semantic_review', 'value': prior}])
    if not text.strip():
        classification.update(document_shape='unparsed_document', substantive=False,
                              status='needs_text_extraction', legal_status='unknown', legal_status_evidence=[])
    row['resource_kind'] = classification['resource_type']
    meta.update(resource_type=classification['resource_type'], document_shape=classification['document_shape'],
                classification=classification, legal_status=classification['legal_status'],
                legal_status_evidence=classification.get('legal_status_evidence', []))
    meta['classification_refreshed_at'] = build.stamp()
    if classification['resource_type'] != meta.get('document_structure', {}).get('resource_type'):
        meta['document_structure'] = build.extract_structure(text, classification['resource_type'])
    return row


def main(checkpoint, report_path):
    baseline = audit(build.HERE)
    if baseline['status'] != 'passed':
        raise ValueError('Existing package failed independent audit; publication stopped')
    old = build.readl(build.HERE / 'resources.jsonl')
    old_summary = json.loads((build.HERE / 'summary.json').read_text(encoding='utf-8'))
    old_manifest_hash = build.sha((build.HERE / 'resources.jsonl').read_bytes())
    artifacts = {a['path']: a for a in build.readl(build.HERE / 'artifacts.jsonl')}
    holdback_file = build.HERE / 'publication_holdbacks.jsonl'
    holdbacks = build.readl(holdback_file) if holdback_file.exists() else []
    holdback_ids = {row['id'] for row in holdbacks}
    resources = [refresh_classification(row) for row in old if row['id'] not in holdback_ids]
    refreshed_by_id = {r['id']: r for r in resources}
    identities = set(refreshed_by_id) | holdback_ids
    inventory = build.readl(build.ROOT / 'delivery/focused_legal_corpus/counties/counties.jsonl')
    byfips = {r['geoid']: r for r in inventory}
    coverage = build.trellis_coverage.load()
    if not coverage:
        raise ValueError('Validated county crosswalk unavailable')
    websites = defaultdict(list)
    for row in coverage['counties']:
        county = byfips.get(row.get('fips'))
        if not county:
            continue
        for detail in [row, *row.get('detail_sources', [])]:
            website = (detail.get('publisher_reported') or {}).get('website')
            if website and build.public_url(website) and county not in websites[build.normurl(website)]:
                websites[build.normurl(website)].append(county)
    added = []
    for source in build.provider_sources(checkpoint, inventory, identities):
        identity = 'county-litigation:' + build.sha((source.get('collection', '') + '\n' + source['source_url'] + '\n' + build.sha(source['raw'])).encode())[:32]
        if identity in identities:
            continue
        row = build.make_resource(source, inventory, websites, artifacts)
        resources.append(row)
        identities.add(identity)
        added.append({'id': row['id'], 'title': row['title'], 'source_url': row['source_url'],
                      'county_geoids': row['county_geoids'], 'kind': row['resource_kind']})
    changed = [{'id': before['id'], 'title': before['title'],
                'before': [before['resource_kind'], before['metadata']['document_shape']],
                'after': [after['resource_kind'], after['metadata']['document_shape']]}
               for before in old if before['id'] in refreshed_by_id
               for after in [refreshed_by_id[before['id']]]
               if (before['resource_kind'], before['metadata']['document_shape']) !=
                  (after['resource_kind'], after['metadata']['document_shape'])]
    summary = dict(old_summary)
    summary.update(resources=len(resources), artifacts=len(artifacts),
                   by_type=dict(Counter(r['resource_kind'] for r in resources)),
                   by_shape=dict(Counter(r['metadata']['document_shape'] for r in resources)),
                   by_state=dict(Counter(r.get('state') or 'unresolved' for r in resources)),
                   county_geoids=len({g for r in resources for g in r['county_geoids']}),
                   unassigned_county_records=sum(not r['county_geoids'] for r in resources),
                   text_gaps=sum(r['metadata']['extraction']['status'] == 'native_text_missing' for r in resources),
                   normalized_at=build.stamp(), classifier_version=VERSION, checkpoint=str(checkpoint),
                   incremental_from_sha256=old_manifest_hash, incremental_added=len(added),
                   existing_geography_preserved=True, network_requests=0,
                   explicitly_held_non_litigation_captures=len(holdbacks))
    build.publish(resources, artifacts, summary)
    result = audit(build.HERE)
    report = {'status': result['status'], 'checked_at': build.stamp(), 'before': old_summary, 'after': summary,
              'added': added, 'classification_changes': changed, 'held': holdbacks, 'audit': result,
              'network_requests': 0, 'original_sources_modified': False, 'main_database_modified': False}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    build.write(report_path, report)
    if result['status'] != 'passed':
        build.write(build.HERE / 'validation.json', {'status': 'failed', 'ready': False, 'data_files': [],
                                                   'reason': 'Post-publication independent audit failed'})
        raise ValueError('New package failed independent audit; serving gate closed')
    print(json.dumps({'status': 'passed', 'before': len(old), 'after': len(resources), 'added': len(added),
                      'classification_changes': changed, 'county_associations': summary['county_geoids'],
                      'text_gaps': summary['text_gaps'], 'audit_counts': result['counts']}))


if __name__ == '__main__':
    from pathlib import Path
    import msvcrt
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    with (build.HERE / 'build.lock').open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            main(args.checkpoint, args.report)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
