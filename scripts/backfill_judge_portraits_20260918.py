"""Offline, evidence-checked integration manifest for nine held local portraits.

No network requests, facial recognition, upstream changes or presentation writes.
The official profile responses were obtained in a separate bounded nine-request
corroboration and are retained as provider representations, not HTTP originals.
"""
from __future__ import annotations
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources/judge_portrait_backfill_20260918'
DISCOVERY = ROOT / 'sources/local_asset_discovery_20260918'
ENTITIES = ROOT / 'sources/judge_entities_20260918/entities.jsonl'
HISTORY = Path('C:/Users/firas/Downloads/Court-Library-Expansion-2026-09-12/federal/http-pages/7e855888bbc4609f9afe89794d226e80184ac5e81469fb50a1e352d96ac58bbd.html')
HISTORY_URL = 'https://cand.uscourts.gov/about-court/northern-district-history/article-iii-judges-northern-district'
COURT = 'U.S. District Court for the Northern District of California'

# Manually reviewed literal evidence pairs. Entity-side dates use the FJC's ISO
# format; profile-side dates preserve the court's own wording. These do not
# expand initials mechanically or infer identity from appearance.
BRIDGES = {
    'Edward M. Chen': ('judge-entity-4a1e774cdf276aedb23bb321', [
        ('undergraduate education', 'University of California, Berkeley, A.B., 1975', 'education', 'University of California, Berkeley, A.B., 1975'),
        ('commission date', 'received commission on May 12, 2011', 'appointments', 'Commission Date: 2011-05-12'),
        ('magistrate service', 'U.S. Magistrate Judge, U.S. District Court, Northern District of California, 2001-2011', 'service', 'U.S. Magistrate Judge, U.S. District Court for the Northern District of California, 2001-2011')]),
    'Jeffrey S. White': ('judge-entity-1262bc03cdffbe175d019a2c', [
        ('undergraduate education', 'Queens College of City University of New York, B.A. 1967', 'education', 'Queens College, City University of New York, B.A., 1967'),
        ('commission date', 'received commission November 15, 2002', 'appointments', 'Commission Date: 2002-11-15')]),
    'Jon S. Tigar': ('judge-entity-e70e911544455c15beeda738', [
        ('district service start month', 'since January 2013', 'appointments', 'Commission Date: 2013-01-18'),
        ('undergraduate institution', 'degree in Economics and English from Williams College', 'education', 'Williams College, B.A., 1984'),
        ('judicial clerkship', 'Judge Robert S. Vance of the United Court of Appeals for the Eleventh Circuit', 'professional_career', 'Hon. Robert S. Vance, U.S. Court of Appeals for the Eleventh Circuit, 1989-1990')]),
    'P. Casey Pitts': ('judge-entity-363b47f59045c2860f0d274b', [
        ('law degree', 'Yale Law School, J.D., 2008', 'education', 'Yale Law School, J.D., 2008'),
        ('commission date', 'Received commission July 7, 2023', 'appointments', 'Commission Date: 2023-07-07')]),
    'Richard Seeborg': ('judge-entity-d776996a4e2eb109f449ebea', [
        ('undergraduate education', 'Yale College, B.A., 1978', 'education', 'Yale College, B.A., 1978'),
        ('commission date', 'received commission on January 4, 2010', 'appointments', 'Commission Date: 2010-01-04'),
        ('magistrate service', 'U.S. Magistrate Judge, U.S. District Court for the Northern District of California, 2001-2009', 'service', 'U.S. Magistrate Judge, U.S. District Court for the Northern District of California, 2001-2009')]),
    'Rita F. Lin': ('judge-entity-0b9b6baaac174883fb3ada21', [
        ('undergraduate education', 'Harvard University, B.A., 2000', 'education', 'Harvard University, B.A., 2000'),
        ('law degree', 'Harvard Law School, J.D., 2003', 'education', 'Harvard Law School, J.D., 2003'),
        ('confirmation date', 'Confirmed by the Senate on September 19, 2023', 'appointments', 'Confirmation Date: 2023-09-19')]),
    'Trina L. Thompson': ('judge-entity-a979a6adf15f8f823297f978', [
        ('undergraduate education', 'University of California, Berkeley, A.B., 1983', 'education', 'University of California, Berkeley, A.B., 1983'),
        ('law degree', 'University of California, Berkeley, School of Law, J.D., 1986', 'education', 'University of California, Berkeley, School of Law, J.D., 1986'),
        ('commission date', 'received commission on August 5, 2022', 'appointments', 'Commission Date: 2022-08-05')]),
    'Vince Chhabria': ('judge-entity-13edb12788423813feff8902', [
        ('undergraduate education', 'University of California, Santa Cruz, B.A., 1991', 'education', 'University of California, Santa Cruz, B.A., 1991'),
        ('commission date', 'received commission on March 7, 2014', 'appointments', 'Commission Date: 2014-03-07')]),
    'William H. Orrick': ('judge-entity-2e4b97eeef0ca2c940b1079e', [
        ('undergraduate education', 'Yale University, B.A., 1976', 'education', 'Yale University, B.A., 1976'),
        ('law degree', 'Boston College Law School, J.D., 1979', 'education', 'Boston College Law School, J.D., 1979'),
        ('commission date', 'received commission on May 16, 2013', 'appointments', 'Commission Date: 2013-05-16'),
        ('senior status date', 'Assumed Senior Status on May 20, 2023', 'appointments', 'Senior Status Date: 2023-05-20')]),
}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def norm(value): return re.sub(r'\s+', ' ', str(value)).strip().casefold()
def namekey(value): return re.sub(r'[^a-z0-9]', '', norm(value))
def rel(path): return Path(path).relative_to(ROOT).as_posix()
def rows(path): return [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
def write(name, value):
    target = OUT / name; target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def jsonl(name, values):
    (OUT / name).write_text(''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in values), encoding='utf-8')


def entity_matches(entity, facts):
    return COURT in (entity.get('courts') or []) and all(
        norm(entity_text) in norm(' '.join(entity.get(field) or []))
        for _, _, field, entity_text in facts
    )


def scoped_biography(markdown, name):
    start = markdown.find('### Federal Judicial Service:')
    if name == 'Jon S. Tigar': start = markdown.find('Judge Jon S. Tigar has served as a District Judge')
    if start < 0: raise ValueError('No explicitly identified biography section: ' + name)
    end = markdown.find('[Return to top]', start)
    return markdown[start:end if end > start else len(markdown)]


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    held = rows(DISCOVERY / 'portrait_unmatched.jsonl')
    all_portraits = rows(DISCOVERY / 'judge_portraits.jsonl')
    previous = rows(DISCOVERY / 'portrait_links.jsonl')
    entities = {e['entity_id']: e for e in rows(ENTITIES)}
    assert len(held) == 9 and len(all_portraits) == 14 and len(previous) == 5
    known_hashes = {p['sha256'] for p in all_portraits}
    for p in all_portraits: assert sha(p['absolute_path']) == p['sha256']
    history_copy = OUT / 'evidence/court-history.html'
    history_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HISTORY, history_copy)
    assert sha(history_copy) == sha(HISTORY)
    accepted, projected, captures = [], [], []
    for portrait in held:
        name = portrait['judge_name']; expected_id, facts = BRIDGES[name]
        filename = 'orrick-profile.json' if name == 'William H. Orrick' else portrait['source_url'].rstrip('/').split('/')[-1] + '.json'
        capture = OUT / 'official' / filename
        response = json.loads(capture.read_text(encoding='utf-8'))
        metadata = response.get('metadata', {})
        assert metadata.get('statusCode') == 200
        assert metadata.get('url') == metadata.get('sourceURL') == portrait['source_url']
        assert 'Northern District of California' in metadata.get('title', '')
        assert namekey(name) in namekey(metadata['title'])
        biography = scoped_biography(response['markdown'], name)
        surname = name.split()[-1].lower()
        candidates = [e for e in entities.values() if surname in re.findall(r'[a-z]+', e['name'].lower()) and COURT in (e.get('courts') or [])]
        matching = [e for e in candidates if entity_matches(e, facts)]
        assert [e['entity_id'] for e in matching] == [expected_id], (name, [e['name'] for e in matching])
        entity = matching[0]
        evidence_facts = []
        for label, profile_text, field, entity_text in facts:
            assert norm(profile_text) in norm(biography), (name, profile_text)
            evidence_facts.append({'fact': label, 'official_profile_literal': profile_text,
                'entity_field': field, 'entity_literal': entity_text,
                'entity_field_provenance': entity.get('field_provenance', {}).get(field, [])})
        original_path = Path(portrait['absolute_path'])
        copied_image = OUT / 'images' / (portrait['sha256'] + original_path.suffix.lower())
        copied_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_path, copied_image)
        assert sha(copied_image) == portrait['sha256']
        proof_file = 'evidence/' + portrait['asset_id'] + '.json'
        differences = []
        if name == 'William H. Orrick':
            differences = [
                'Official profile re-nomination is January 3, 2013; FJC entity says January 4, 2013. This date is not used as a matching fact and neither version is overwritten.',
                'Official profile DOJ career ends in 2012; FJC entity says 2013. That boundary is not used as a matching fact.'
            ]
        rejected = [{'entity_id': e['entity_id'], 'name': e['name'], 'education': e.get('education'),
                     'appointments': e.get('appointments'), 'reason': 'Fails the corroborated education/service/appointment facts'}
                    for e in candidates if e['entity_id'] != expected_id]
        proof = {'portrait_name_as_reported': name, 'matched_entity_id': expected_id,
                 'matched_entity_name': entity['name'], 'court': COURT,
                 'identity_basis': 'named_official_portrait_profile_plus_explicit_court_and_independent_education_service_or_appointment_facts',
                 'facts': evidence_facts, 'candidate_entities_considered': len(candidates),
                 'rejected_namesakes': rejected, 'source_differences_preserved': differences,
                 'image_identity_evidence': portrait['identity_evidence'],
                 'saved_profile_image_evidence': portrait['profile_evidence'],
                 'official_profile_capture': {'path': rel(capture), 'sha256': sha(capture), 'source_url': portrait['source_url'],
                      'scrape_id': metadata['scrapeId'], 'status_code': metadata['statusCode'],
                      'representation': 'Firecrawl extracted page representation; not original HTTP bytes'},
                 'official_profile_biography_excerpt': biography,
                 'court_history': {'source_url': HISTORY_URL, 'original_path': HISTORY.as_posix(),
                                   'saved_path': rel(history_copy), 'sha256': sha(history_copy), 'role': 'Supplemental historical name and service-year corroboration'},
                 'entity_manifest': {'path': rel(ENTITIES), 'sha256': sha(ENTITIES)},
                 'entity_record': entity, 'image_original_path': original_path.as_posix(),
                 'image_sha256': portrait['sha256'], 'facial_identification_used': False,
                 'current_service_verified': False, 'matter_assignment_asserted': False}
        write(proof_file, proof)
        item = portrait | {'entity_id': expected_id, 'entity_name': entity['name'],
            'integration_status': 'accepted_source_supported_identity_bridge',
            'identity_basis': proof['identity_basis'], 'identity_evidence_path': rel(OUT / proof_file),
            'identity_evidence_sha256': sha(OUT / proof_file), 'verified_copy_path': rel(copied_image),
            'proposed_presentation_path': 'sources/judge_presentation_20260918/images/' + copied_image.name}
        # Remove obsolete candidate conclusions while retaining their source file.
        for key in ['possible_entities_not_accepted', 'exact_candidates', 'reason']: item.pop(key, None)
        accepted.append(item)
        projected.append({'entity_id': expected_id, 'id': portrait['asset_id'], 'path': item['proposed_presentation_path'],
            'sha256': portrait['sha256'], 'mime': portrait['mime'], 'provenance': {
                'source_url': portrait['source_url'], 'image_source_url': portrait['image_source_url'],
                'captured_at': portrait['captured_at'], 'identity_basis': proof['identity_basis'],
                'permission_status': portrait['permission_status'],
                'source_manifest': rel(OUT / 'accepted_links.jsonl'),
                'identity_evidence_path': item['identity_evidence_path'],
                'identity_evidence_sha256': item['identity_evidence_sha256'],
                'current_service_verified': False, 'matter_assignment_asserted': False}})
        captures.append({'path': rel(capture), 'sha256': sha(capture), 'source_url': portrait['source_url'],
                         'scrape_id': metadata['scrapeId'], 'credits_used': metadata.get('creditsUsed'),
                         'status_code': metadata['statusCode'], 'returned_url': metadata['url']})
    scans = []
    # Inspect both original staging and curated manifests; rejected candidates
    # remain rejected and are never silently promoted to accepted portraits.
    roots = [Path('C:/Users/firas/Downloads/Court-Library-Expansion-2026-09-12/assets'),
             Path('C:/Users/firas/Downloads/Court-Document-Library/05-Court-and-Judge-Assets')]
    extra = {}
    for root in roots:
        for name in ['judge-assets.json', 'assets.jsonl', 'portrait-pass-candidates.jsonl', 'rejected-candidates.jsonl', 'publisher-assets.jsonl']:
            path = root / name
            if not path.exists(): continue
            values = rows(path) if path.suffix == '.jsonl' else json.loads(path.read_text(encoding='utf-8-sig'))
            portraits = [v for v in values if v.get('kind') == 'judge_photo' or v.get('judge_name')]
            scans.append({'path': path.as_posix(), 'sha256': sha(path), 'rows': len(values),
                          'named_portrait_rows': len(portraits), 'distinct_portrait_hashes': sorted({v.get('sha256') for v in portraits if v.get('sha256')})})
            for value in portraits:
                if value.get('sha256') and value['sha256'] not in known_hashes: extra[value['sha256']] = value | {'source_manifest': path.as_posix()}
    jsonl('accepted_links.jsonl', accepted)
    jsonl('portraits.append.jsonl', projected)
    jsonl('remaining_gaps.jsonl', [])
    rejected_extra = [p for p in extra.values() if p.get('exclusion_reason')]
    eligible_extra = [p for p in extra.values() if not p.get('exclusion_reason')]
    write('additional_asset_scan.json', {'scope': 'Named portrait metadata in curated and original legal asset packs; prior eleven-folder inventory retained as discovery context.',
        'prior_folder_inventory': {'path': rel(DISCOVERY / 'folder_inventory.json'), 'sha256': sha(DISCOVERY / 'folder_inventory.json')},
        'manifests_inspected': scans, 'additional_distinct_candidates': list(extra.values()),
        'eligible_new_portrait_candidates': eligible_extra, 'previous_exclusions_retained': rejected_extra,
        'new_portraits_downloaded': 0, 'face_matching': False, 'national_completeness_asserted': False})
    write('capture_receipt.json', {'captures': captures, 'provider_requests': len(captures),
        'credits_reported_by_provider': sum(c.get('credits_used') or 0 for c in captures),
        'remaining_credits_before': 5087, 'remaining_credits_after': 5078, 'observed_credit_delta': 9, 'images_downloaded': 0,
        'host_preflight': 'No cand.uscourts.gov row in shared host pause database; saved September13 robots receipt allowed profile paths.',
        'source_representations': 'Provider extractions are kept distinct from original locally saved image bytes.'})
    summary = {'completed_at': datetime.now(timezone.utc).isoformat(), 'known_local_portraits': 14,
        'previously_linked': 5, 'new_evidence_supported_links': len(accepted), 'known_portraits_linked_after_integration': 5 + len(accepted),
        'remaining_known_portrait_identity_gaps': 0, 'additional_distinct_portraits_discovered': len(eligible_extra),
        'previously_rejected_nonportrait_candidates_retained': len(rejected_extra),
        'jurisdiction': COURT, 'national_judge_portrait_coverage_complete': False,
        'presentation_modified': False, 'source_images_modified': False, 'entities_modified': False,
        'facial_identification_used': False, 'official_profile_requests': 9, 'credits_reported': 9,
        'current_service_verified': False, 'matter_assignment_asserted': False,
        'accepted_manifest_sha256': sha(OUT / 'accepted_links.jsonl'),
        'integration_manifest_sha256': sha(OUT / 'portraits.append.jsonl')}
    write('summary.json', summary)
    return summary


if __name__ == '__main__': print(json.dumps(build(), indent=2))
