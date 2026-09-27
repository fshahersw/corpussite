"""Bounded read-only inventory of named legal-project assets; writes only this folder."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DOWNLOADS = Path('C:/Users/firas/Downloads')
PACK = DOWNLOADS / 'Court-Document-Library/05-Court-and-Judge-Assets'
EVIDENCE = DOWNLOADS / 'Court-Library-Expansion-2026-09-12/assets/page-evidence'
ENTITIES = ROOT / 'sources/judge_entities_20260918/entities.jsonl'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(name, rows, jsonl=True):
    path = OUT / name
    with path.open('w', encoding='utf-8', newline='\n') as f:
        if jsonl:
            for row in rows: f.write(json.dumps(row, ensure_ascii=False) + '\n')
        else: json.dump(rows, f, ensure_ascii=False, indent=2)


def normalize(value):
    return ' '.join(str(value or '').casefold().replace('.', '').split())


def inventory():
    now = datetime.now(timezone.utc).isoformat()
    portraits = read(PACK / 'judge-assets.json')
    courts = read(PACK / 'court-assets.json')
    assets = [json.loads(line) for line in (PACK / 'assets.jsonl').read_text(encoding='utf-8').splitlines() if line]
    excluded = read(PACK / 'asset-policy-exclusions.json')
    exclusions = {(row['court_id'], row['sha256']) for row in excluded}
    issues = []; image_files = {}; associations = []
    for asset in assets:
        path = (PACK / asset['local_path']).resolve()
        assert path.is_relative_to(PACK.resolve())
        if (asset['court_id'], asset['sha256']) in exclusions:
            issues.append({'kind': 'excluded_asset_reintroduced', 'asset_id': asset['asset_id']})
            continue
        if asset['sha256'] not in image_files:
            actual_sha = sha(path)
            actual_size = path.stat().st_size
            good = actual_sha == asset['sha256'] and actual_size == asset['bytes']
            if not good: issues.append({'kind': 'hash_or_size_mismatch', 'path': str(path)})
            image_files[asset['sha256']] = {'asset_id': asset['asset_id'], 'absolute_path': path.as_posix(),
                'sha256': actual_sha, 'bytes': actual_size, 'mime': asset['mime_type'],
                'width': asset.get('width'), 'height': asset.get('height'), 'verified': good,
                'raster_image': path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico', '.bmp'},
                'source_manifest': (PACK / 'assets.jsonl').as_posix(),
                'serving_note': 'Allowlisted asset only; do not accept arbitrary filesystem paths. SVG needs an explicit safe serving policy.'}
        associations.append({**asset, 'absolute_path': path.as_posix(), 'file_verified': image_files[asset['sha256']]['verified']})
    write('image_files.jsonl', list(image_files.values()))
    write('asset_associations.jsonl', associations)
    evidence = []
    for pattern in ['judge-*.json', 'portrait-pass-*.json']:
        for path in EVIDENCE.glob(pattern):
            obj = read(path)
            if isinstance(obj, dict) and obj.get('judge_name') and obj.get('source_page'):
                evidence.append((path, obj))
    exact_names = {normalize(a['judge_name']) for a in portraits}
    surnames = {a['judge_name'].split()[-1].casefold() for a in portraits}
    entities = []; entities_sha = hashlib.sha256()
    with ENTITIES.open('rb') as f:
        for line_number, line in enumerate(f, 1):
            entities_sha.update(line)
            if not line.strip(): continue
            row = json.loads(line)
            # Keep only a compact relevant view, never duplicate the large entity corpus.
            names = [row['name']] + [a['name'] for a in row.get('aliases', [])]
            if any(normalize(n) in exact_names or n.split()[-1].casefold().rstrip('.') in surnames for n in names):
                row['_line'] = line_number; row['_raw_line_sha256'] = hashlib.sha256(line).hexdigest()
                entities.append(row)
    accepted = []; held = []; portrait_inventory = []
    court_name = 'U.S. District Court for the Northern District of California'
    for asset in portraits:
        image_file = image_files[asset['sha256']]
        proofs = [(p, obj) for p, obj in evidence if obj['source_page'] == asset['source_page']
                  and obj['judge_name'] == asset['judge_name'] and obj.get('court_id') == asset['court_id']]
        profile_proof = []
        for path, obj in proofs:
            image_match = any(im.get('url') == asset['asset_url'] for im in obj.get('accepted_candidates', []))
            profile_proof.append({'path': path.as_posix(), 'sha256': sha(path),
                'profile_url': obj['source_page'], 'title': obj.get('title'), 'headings': obj.get('headings'),
                'judge_name': obj['judge_name'], 'image_url_in_accepted_candidates': image_match})
        match_candidates = []
        possible = []
        for entity in entities:
            entity_names = [entity['name']] + [a['name'] for a in entity.get('aliases', [])]
            court_matches = court_name in entity.get('courts', [])
            name_exact = normalize(asset['judge_name']) in {normalize(n) for n in entity_names}
            urls = {entity.get('best_profile_source_url')} | {m.get('source_url') for m in entity.get('members', [])}
            url_exact = asset['source_page'] in urls
            if asset['court_id'] == 'FD:cand' and urlparse(asset['source_page']).hostname == 'cand.uscourts.gov' and court_matches:
                if name_exact or url_exact:
                    match_candidates.append((entity, 'exact_profile_url' if url_exact else 'exact_full_displayed_name_and_explicit_court'))
                elif asset['judge_name'].split()[-1].casefold() in entity['name'].casefold().split():
                    possible.append({'entity_id': entity['entity_id'], 'name': entity['name'], 'courts': entity['courts'],
                        'reason': 'Name differs; surname and court are discovery hints only, not accepted identity evidence'})
        common = {'asset_id': asset['asset_id'], 'judge_name': asset['judge_name'], 'court_id': asset['court_id'],
            'court_name': asset['court_name'], 'absolute_path': image_file['absolute_path'],
            'sha256': asset['sha256'], 'bytes': asset['bytes'], 'mime': asset['mime_type'],
            'source_url': asset['source_page'], 'image_source_url': asset['asset_url'], 'captured_at': asset['retrieved_at'],
            'source_manifest': (PACK / 'judge-assets.json').as_posix(), 'source_manifest_sha256': sha(PACK / 'judge-assets.json'),
            'file_verified': image_file['verified'], 'profile_evidence': profile_proof,
            'identity_evidence': asset.get('identity_evidence'), 'permission_status': asset.get('permission_status'),
            'reuse_note': asset.get('reuse_note'), 'observed_role_or_status': asset.get('observed_role_or_status'),
            'current_service_verified': False, 'matter_assignment': None}
        portrait_inventory.append(common)
        if len(match_candidates) == 1 and image_file['verified'] and any(p['image_url_in_accepted_candidates'] for p in profile_proof):
            entity, basis = match_candidates[0]
            court_provenance = [p for p in entity.get('field_provenance', {}).get('appointments', [])
                if isinstance(p.get('value_as_reported'), dict) and p['value_as_reported'].get('court') == court_name]
            accepted.append({'entity_id': entity['entity_id'], 'absolute_path': image_file['absolute_path'],
                'sha256': asset['sha256'], 'mime': asset['mime_type'], 'source_url': asset['source_page'],
                'identity_basis': basis, 'asset_id': asset['asset_id'],
                'metadata': {**common, 'entity_name': entity['name'], 'entity_courts': entity['courts'],
                    'entity_manifest': ENTITIES.relative_to(ROOT).as_posix(), 'entity_manifest_line': entity['_line'],
                    'entity_raw_line_sha256': entity['_raw_line_sha256'],
                    'court_crosswalk': {'asset_court_id': 'FD:cand', 'official_profile_host': 'cand.uscourts.gov',
                        'entity_court_literal': court_name, 'basis': 'Explicit named federal district in court asset registry and entity service evidence'},
                    'corroborating_court_provenance': court_provenance,
                    'identity_caveat': 'No face recognition or initials expansion; this link makes no current-service or matter-assignment assertion'}})
        else:
            held.append({**common, 'integration_status': 'not_linked_to_entity',
                'reason': 'No unique exact full-name/profile-URL match with explicit court corroboration',
                'possible_entities_not_accepted': possible,
                'exact_candidates': [e['entity_id'] for e, _ in match_candidates]})
    write('judge_portraits.jsonl', portrait_inventory)
    write('portrait_links.jsonl', accepted)
    write('portrait_unmatched.jsonl', held)
    by_id = {f['asset_id']: f for f in image_files.values()}
    court_links = []
    for key, court in courts.items():
        image = by_id.get(court.get('primary_asset_id'))
        court_links.append({**court, 'source_registry': (PACK / 'court-assets.json').as_posix(),
            'primary_image': image, 'local_or_county_scope_inferred': False,
            'integration_note': 'Join only explicit court identity/homepage. Statewide judiciary branding is not a logo for every county court.'})
    write('court_links.jsonl', court_links)
    write('source_exclusions.json', excluded, False)
    summary = {'created_at': now, 'scope': 'Read-only discovery of named local legal projects; no network or library import',
        'judge_portraits': len(portraits), 'accepted_entity_portrait_links': len(accepted), 'unmatched_portraits': len(held),
        'court_entries': len(courts), 'court_entries_with_primary_image': sum(bool(c['primary_image']) for c in court_links),
        'unique_verified_image_files': sum(f['verified'] for f in image_files.values()),
        'unique_image_bytes': sum(f['bytes'] for f in image_files.values()),
        'image_associations': len(associations), 'source_policy_exclusions_retained': len(excluded),
        'court_image_kind_counts': dict(Counter(a['kind'] for a in associations)),
        'all_portraits_explicitly_associated_with_one_federal_district': 'Northern District of California',
        'entities_manifest_sha256': entities_sha.hexdigest(), 'issues': issues}
    write('summary.json', summary, False)
    write('validation.json', {'status': 'passed' if not issues else 'failed', 'created_at': now, 'issues': issues,
        'all_accepted_portraits_have_raw_image_and_source_profile_evidence': all(r['metadata']['profile_evidence'] for r in accepted),
        'no_name_only_or_face_based_joins': True, 'external_files_modified': False,
        'hashes': {name: sha(OUT / name) for name in ['portrait_links.jsonl', 'judge_portraits.jsonl', 'portrait_unmatched.jsonl', 'court_links.jsonl', 'image_files.jsonl']}}, False)
    print(json.dumps(summary))


if __name__ == '__main__': inventory()
