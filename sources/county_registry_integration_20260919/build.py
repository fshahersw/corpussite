"""Offline projection from a fixed, source-tagged county registry."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCE = Path('C:/Users/firas/Downloads/returnedfiles/court_access_registry_2026-08-21')
REGISTRY_SHA = 'c91ce0e47d7618866b9ac0b9611475987e86f3146000492a8c74530df11be12d'
MANIFEST_SHA = 'c62e501eff221c797dcbad59271bd27d81b9a152e5f5d4ad953a0ae606c80a1a'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def value(field):
    if isinstance(field, dict) and field.get('status') in ('observed', 'derived', 'null'):
        return field.get('value')
    return field


def items(field):
    result = value(field)
    return result if isinstance(result, list) else []


def safe_url(field):
    url = value(field)
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    return url if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password else None


def check_join(row, inventory):
    geoid = value(row['fips'])
    match = inventory.get(geoid)
    if (not isinstance(geoid, str) or not re.fullmatch(r'\d{5}', geoid) or not match
            or match['state'] != value(row['state'])
            or match['usps'] != value(row['state_code'])
            or match['name'].casefold() != (value(row['county']) + ' County').casefold()):
        raise ValueError('Registry county failed exact FIPS/state/name join: ' + str(geoid))
    return match


def check_tags(node, evidence_ids):
    if isinstance(node, dict):
        if node.get('status') in ('observed', 'derived', 'null'):
            if node.get('evidence_id') and node['evidence_id'] not in evidence_ids:
                raise ValueError('Unknown source evidence ID: ' + node['evidence_id'])
            if node['status'] == 'null' and (node.get('value') is not None or not node.get('reason')):
                raise ValueError('Invalid null provenance')
        for child in node.values():
            check_tags(child, evidence_ids)
    elif isinstance(node, list):
        for child in node:
            check_tags(child, evidence_ids)


def project(row, match, source_info):
    courts = []
    for c in items(row.get('court_entities')):
        courts.append({'name': value(c.get('entity_name')), 'type': value(c.get('court_type')),
                       'url': safe_url(c.get('url')), 'url_role': 'source reference',
                       'provenance': {k: c.get(k) for k in ('entity_name', 'court_type', 'url', 'source_page') if k in c}})
    clerks = []
    for c in items(row.get('clerk_offices')):
        clerks.append({'name': value(c.get('clerk_name')), 'office': value(c.get('office_name')),
                       'url': safe_url(c.get('url')), 'url_role': 'source reference',
                       'provenance': {k: c.get(k) for k in ('clerk_name', 'office_name', 'url') if k in c}})
    links = []
    seen = set()
    def add(label, field, scope, provenance, status='Link observed; current availability not checked'):
        url = safe_url(field)
        if url and url not in seen:
            seen.add(url)
            links.append({'label': label, 'url': url, 'status': status, 'scope': scope,
                          'downloaded_document': False, 'provenance': provenance})
    page = row.get('county_page') or {}
    add('County court information', page.get('url'), 'county', page)
    case = row.get('case_search_system') or {}
    if safe_url(case.get('url')):
        add(value(case.get('name')) or 'Case search', case.get('url'), 'statewide', case,
            'Link observed; access not verified in saved snapshot' if value(case.get('verification_status')) == 'link_observed_host_not_fetched' else 'Link observed; current availability not checked')
    # Publish substantive directory/access/rules links, not menu padding.
    for link in items(row.get('published_content')):
        label = value(link.get('content_type')) or ''
        if not re.search(r'forms|rules|court_calendar|records_request|directory|litigation|case_search|Remote Court', label, re.I):
            continue
        if label == 'court_administration_reporting_directory':
            display = 'Court administration directory (not case search)'
        else:
            display = label.replace('_', ' ')
        scope = value(link.get('scope')) or ('county' if value(link.get('county_specific')) else 'statewide')
        add(display, link.get('url'), scope, link)
    limitations = ['Historical snapshot; present-day service and link availability are not verified.',
                   'Judge-seat associations may repeat people across counties; this is not a unique judge count.',
                   'County coverage does not establish complete local rules, filings or documents.',
                   'Court and clerk URLs identify saved source references, which may be statewide documents.']
    if value(row.get('state_code')) == 'TX':
        limitations += ['County-specific website and case-search discovery was not completed in this snapshot.',
                        'One separate clerk-directory PDF was not saved; clerk rows are sourced from the saved trial-personnel PDF.']
    for gap in items(row.get('unresolved_content')):
        reason = (gap.get('value') or {}).get('reason')
        if reason and reason not in limitations:
            limitations.append(reason)
    return {'available': True, 'county': match['name'], 'geoid': match['geoid'], 'state': match['state'],
            'source_as_of': row['_meta']['generated_at'][:10], 'saved_at': None,
            'counts': {'courts': len(courts), 'clerks': len(clerks), 'judge_seats': len(items(row.get('judges_seated')))},
            'courts': courts, 'clerks': clerks, 'links': links, 'limitations': limitations,
            'current_service_verified': False, 'source': {**source_info, 'record_id': row['_meta']['record_id']},
            'provenance': {'fips': row['fips'], 'county': row['county'], 'state': row['state'],
                           'case_search_system': case, 'access_model': row.get('access_model'),
                           'unresolved_content': row.get('unresolved_content')}}


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / 'validation.json').write_text(json.dumps({'status': 'building', 'ready': False}))
    registry_raw = (SOURCE / 'registry.jsonl').read_bytes()
    manifest_raw = (SOURCE / 'evidence_manifest.jsonl').read_bytes()
    if sha(registry_raw) != REGISTRY_SHA or sha(manifest_raw) != MANIFEST_SHA:
        raise ValueError('Source manifest differs from the independently audited snapshot')
    evidence = [json.loads(l) for l in manifest_raw.decode('utf-8-sig').splitlines() if l]
    checked = []
    missing = []
    for item in evidence:
        if not item.get('sha256') or not item.get('local_path'):
            if item['evidence_id'] != 'TX-DISTRICT-COUNTY-CLERKS-PDF':
                raise ValueError('Unexpected missing evidence')
            missing.append(item)
            continue
        path = (SOURCE / item['local_path']).resolve()
        if not path.is_relative_to(SOURCE.resolve()) or not path.is_file() or sha(path.read_bytes()) != item['sha256']:
            raise ValueError('Missing, changed, or unconfined evidence: ' + item['evidence_id'])
        checked.append({'evidence_id': item['evidence_id'], 'sha256': item['sha256'], 'local_path': item['local_path']})
    assert len(checked) == 167 and len(missing) == 1
    inventory_path = ROOT / 'delivery/focused_legal_corpus/counties/counties.jsonl'
    inventory_raw = inventory_path.read_bytes()
    inventory = {r['geoid']: r for r in map(json.loads, inventory_raw.decode('utf-8-sig').splitlines())}
    now = datetime.now(timezone.utc).isoformat()
    source_info = {'label': 'Kentucky and Texas county court-access registry',
                   'snapshot': '2026-08-22T03:12:17.609152Z', 'run_label': '2026-08-21',
                   'verified_artifacts': 167, 'missing_artifacts': 1,
                   'missing_evidence': missing, 'verified_at': now,
                   'registry_sha256': REGISTRY_SHA, 'evidence_manifest_sha256': MANIFEST_SHA,
                   'qualification': 'Source observations were collected August 21–22, 2026; snapshot creation is not the effective date of a rule or appointment.'}
    projected = {}
    for row in map(json.loads, registry_raw.decode('utf-8-sig').splitlines()):
        check_tags(row, {e['evidence_id'] for e in evidence})
        match = check_join(row, inventory)
        if match['geoid'] in projected:
            raise ValueError('Duplicate county identity')
        projected[match['geoid']] = project(row, match, source_info)
    assert len(projected) == 374
    data = json.dumps(projected, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    (OUT / 'counties.json').write_bytes(data)
    (OUT / 'evidence_verification.json').write_text(json.dumps({'verified_at': now, 'verified': checked, 'missing': missing}, indent=2), encoding='utf-8')
    summary = {'status': 'passed', 'ready': True, 'verified_at': now, 'county_count': len(projected),
               'states': dict(Counter(r['state'] for r in projected.values())),
               'counts': {key: sum(r['counts'][key] for r in projected.values()) for key in ('courts', 'clerks', 'judge_seats')},
               'verified_artifacts': 167, 'missing_artifacts': 1, 'counties_sha256': sha(data),
               'registry_sha256': REGISTRY_SHA, 'evidence_manifest_sha256': MANIFEST_SHA,
               'county_inventory_sha256': sha(inventory_raw), 'checks': ['exact FIPS/state/name joins', 'source manifest hash binding', '167 fresh evidence hashes', 'all tagged evidence IDs recognized', 'null reasons preserved']}
    (OUT / 'validation.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
