"""Build the evidence-based judge overlay (offline, re-runnable, read-only on every input).

One row per consolidated judge entity (sources/judge_entities_20260918/entities.jsonl), keyed by entity_id:
  fjc / fjc_status   structured FJC appointment facts and a dated FJC-reported status (never "current")
  ids                fjc_nid -> fjc_jid -> CourtListener person id, native ids only (names/birth year only veto a link)
  courtlistener      political affiliations as recorded (with source + dates), educations, positions count, how_selected
  role_normalized    district|circuit|magistrate|bankruptcy|state_trial|state_appellate|other, with basis + evidence
  completeness       deterministic evidence-coverage score (not a quality score)
No network. No name joins. Nothing is inferred about present service.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENTITIES = ROOT / 'sources/judge_entities_20260918/entities.jsonl'
FJC_CSV = ROOT / 'sources/judges/enrichment_20260914/federal_biographies/judges.csv'
CL_DB = ROOT / 'sources/courtlistener_people_20260918/catalog.sqlite3'
FJC_URL = 'https://www.fjc.gov/sites/default/files/history/judges.csv'
NID_KEY = re.compile(r'\|fjc:nid:(\d+):')
BRIDGE_BASIS = ('entity member key fjc:nid:<nid> -> FJC export row (nid) -> jid in the same row == CourtListener '
                'people.fjc_id -> CourtListener person id; native ids only, surname and birth year are used only to '
                'withhold a link, never to create one')
ROLE_VALUES = ('district', 'circuit', 'magistrate', 'bankruptcy', 'state_trial', 'state_appellate', 'other')
STATUS_VALUES = ('active', 'senior', 'terminated', 'deceased')
CL_PARTY = {'d': 'Democratic', 'r': 'Republican', 'i': 'Independent', 'g': 'Green', 'l': 'Libertarian', 'f': 'Federalist',
            'w': 'Whig', 'j': 'Jeffersonian Republican', 'u': 'National Union', 'z': 'Reform Party'}
CL_PARTY_SOURCE = {'b': 'Ballot', 'a': 'Appointer (party of the appointing official, not a registration of the judge)',
                   'o': 'Other'}
CL_SELECTED = {'e_part': 'Partisan election', 'e_non_part': 'Non-partisan election', 'a_pres': 'Appointment (President)',
               'a_gov': 'Appointment (Governor)', 'a_legis': 'Appointment (Legislature)', 'a_judge': 'Appointment (Judge)',
               'ct_trans': 'Transferred (court restructuring)'}
WEIGHTS = (('fjc_appointments', 25), ('fjc_status', 15), ('cl_bridge', 10), ('cl_political_affiliation', 5),
           ('education', 10), ('role_established', 10), ('court_label', 5), ('state_label', 5), ('biography', 5),
           ('professional_career', 5), ('analyses', 5))
APPELLATE = re.compile(r'appellate|court of appeal|court of (criminal|civil|special) appeals|supreme judicial court|'
                       r'commonwealth court|appeals court')
TRIAL = re.compile(r'superior court|circuit court|district court|county court|court of common pleas|municipal|probate|'
                   r'family court|juvenile|chancery|general sessions|justice court|magistrate court|city court|'
                   r'judicial district|judicial circuit|surrogate|trial court|housing court|civil court|criminal court|'
                   r'state court|summary court|orphans|court at law|town court|village court|police court|land court|'
                   r'traffic|small claims|water court|workers|metropolitan court|justice of the peace|equity court|'
                   r'parish court|business court|magistrate.s court')
MAGISTRATE_TEXT = re.compile(r"[^.]{0,80}\b(?:is|was|serves as|served as) (?:a |an )?(?:united states|u\.s\.) magistrate judge\b[^.]{0,60}|"
                             r"[^.]{0,80}\bmagistrate judge (?:for|of|on|in|at) the (?:united states|u\.s\.)[^.]{0,60}", re.I)
ARTICLE_III_TEXT = re.compile(r'district judge|article iii|nominated by president|appointed by president|senior judge', re.I)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def clean(value):
    value = (value or '').strip()
    return value or None


def norm_name(value):
    text = unicodedata.normalize('NFKD', value or '')
    return re.sub(r'[^a-z]', '', ''.join(c for c in text if not unicodedata.combining(c)).casefold())


def fjc_appointments(row):
    out = []
    for i in range(1, 7):
        if not clean(row.get('Court Name (%d)' % i)) and not clean(row.get('Court Type (%d)' % i)):
            continue
        g = lambda label: clean(row.get('%s (%d)' % (label, i)))  # noqa: E731
        out.append({
            'sequence': i, 'court_type': g('Court Type'), 'court': g('Court Name'), 'title': g('Appointment Title'),
            'appointing_president': g('Appointing President'),
            'party_of_appointing_president': g('Party of Appointing President'),
            'reappointing_president': g('Reappointing President'),
            'party_of_reappointing_president': g('Party of Reappointing President'),
            'aba_rating': g('ABA Rating'), 'seat_id': g('Seat ID'),
            'recess_appointment_date': g('Recess Appointment Date'), 'nomination_date': g('Nomination Date'),
            'hearing_date': g('Hearing Date'), 'judiciary_committee_action': g('Judiciary Committee Action'),
            'committee_action_date': g('Committee Action Date'), 'senate_vote_type': g('Senate Vote Type'),
            'senate_vote_ayes_nays': g('Ayes/Nays'), 'confirmation_date': g('Confirmation Date'),
            'commission_date': g('Commission Date'), 'chief_judge_begin': g('Service as Chief Judge, Begin'),
            'chief_judge_end': g('Service as Chief Judge, End'), 'senior_status_date': g('Senior Status Date'),
            'termination_reason': g('Termination'), 'termination_date': g('Termination Date'),
            'party_label_note': 'Party of the appointing president as published by the FJC; not the judge\'s own affiliation.',
        })
    return out


def start_key(appointment):
    return (appointment['commission_date'] or appointment['recess_appointment_date'] or '', appointment['sequence'])


def fjc_status(row, appointments, as_of):
    open_ = [a for a in appointments if not a['termination_date'] and not a['termination_reason']]
    death = clean(row.get('Death Year'))
    courts = sorted({a['court'] for a in open_ if a['court']})
    if death:
        value, detail, courts = 'deceased', 'FJC-reported death year %s' % death, []
    elif open_:
        regular = [a for a in open_ if not a['senior_status_date']]
        if regular:
            value, detail = 'active', 'open appointment with no senior status date or termination in the FJC export'
        else:
            value = 'senior'
            detail = 'senior status since %s' % max(a['senior_status_date'] for a in open_)
    else:
        last = max(appointments, key=lambda a: (a['termination_date'] or '', a['sequence']))
        value = 'terminated'
        detail = '%s, %s' % (last['termination_reason'] or 'termination reason not published',
                             last['termination_date'] or 'date not published')
    return {'value': value, 'as_of': as_of,
            'as_of_basis': 'capture date of the FJC Biographical Directory export; the file carries no internal as-of date',
            'label': 'FJC-reported status as of %s' % as_of, 'detail': detail, 'open_appointment_courts': courts,
            'verified_independently': False}


def fjc_role(appointments):
    mapping = {'U.S. District Court': 'district', 'U.S. Court of Appeals': 'circuit'}
    open_ = [a for a in appointments if not a['termination_date'] and not a['termination_reason']]
    pool, basis = (open_, 'fjc_court_type_of_open_appointment') if open_ else (appointments, 'fjc_court_type_of_latest_appointment')
    chosen = max(pool, key=start_key)
    values = sorted({mapping.get(a['court_type'], 'other') for a in appointments})
    return {'value': mapping.get(chosen['court_type'], 'other'), 'basis': basis,
            'evidence': '%s: %s' % (chosen['court_type'], chosen['court']), 'all_values': values}


def classify_court(text, state):
    t = re.sub(r'^[A-Z]{2} - ', '', text).casefold()
    if 'bankruptcy' in t:
        return 'bankruptcy'
    if t.startswith('u.s.') or 'united states' in t:
        if 'court of appeals' in t:
            return 'circuit'
        return 'us_district' if 'district court' in t else 'other'
    if APPELLATE.search(t):
        return 'state_appellate'
    if 'supreme court' in t:
        return 'state_trial' if (state == 'NY' or 'county supreme court' in t) else 'state_appellate'
    if state == 'PA' and 'superior court' in t:
        return 'state_appellate'
    return 'state_trial' if TRIAL.search(t) else None


def pattern_role(entity):
    states = entity.get('state_codes') or []
    found = []
    for court in entity.get('courts') or []:
        prefix = re.match(r'^([A-Z]{2}) - ', court)
        state = prefix.group(1) if prefix else (states[0] if len(states) == 1 else None)
        code = classify_court(court, state)
        if code:
            found.append((code, court))
    if not found:
        return {'value': 'other', 'basis': 'no_role_evidence', 'evidence': None, 'all_values': ['other']}
    code, court = found[0]
    values = sorted({'other' if c == 'us_district' else c for c, _ in found})
    if code == 'us_district':
        text = ' '.join([entity.get('biography') or ''] + [s for s in (entity.get('service') or []) if isinstance(s, str)])
        hit = MAGISTRATE_TEXT.search(text)
        if hit and not ARTICLE_III_TEXT.search(text):
            return {'value': 'magistrate', 'basis': 'source_profile_text_states_magistrate_judge_and_no_fjc_record',
                    'evidence': hit.group(0).strip()[:200], 'all_values': sorted(set(values) - {'other'} | {'magistrate'})}
        return {'value': 'other', 'basis': 'us_district_court_without_fjc_record_title_not_established',
                'evidence': court, 'all_values': values}
    return {'value': code, 'basis': 'court_name_pattern', 'evidence': court, 'all_values': values,
            'ambiguous': len(values) > 1}


def load_courtlistener():
    con = sqlite3.connect('file:%s?mode=ro' % CL_DB.as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    snapshot = json.loads(con.execute("select value_json from import_metadata where key='source_snapshot'").fetchone()[0])
    people = {}
    for r in con.execute("select id, fjc_id, name_last, date_dob from people where fjc_id <> '' and is_alias_of_id = ''"):
        people.setdefault(r['fjc_id'], []).append(dict(r))
    schools = {r['id']: r['name'] for r in con.execute('select id, name from schools')}
    return con, snapshot, people, schools


def cl_block(con, schools, person_id, snapshot):
    affiliations = [{
        'political_party_code': r['political_party'], 'political_party_label': CL_PARTY.get(r['political_party']),
        'source_code': r['source'] or None, 'source_label': CL_PARTY_SOURCE.get(r['source']),
        'date_start': r['date_start'] or None, 'date_granularity_start': r['date_granularity_start'] or None,
        'date_end': r['date_end'] or None, 'date_granularity_end': r['date_granularity_end'] or None,
    } for r in con.execute('select * from political_affiliations where person_id = ? order by date_start, id', (person_id,))]
    educations = [{
        'school': schools.get(r['school_id']), 'degree_level': r['degree_level'] or None,
        'degree_detail': r['degree_detail'] or None, 'degree_year': r['degree_year'] or None,
    } for r in con.execute('select * from educations where person_id = ? order by degree_year, id', (person_id,))]
    selected = Counter()
    positions = 0
    for r in con.execute('select how_selected from positions where person_id = ?', (person_id,)):
        positions += 1
        if r['how_selected']:
            selected[r['how_selected']] += 1
    return {
        'source': 'CourtListener people bulk data (Free Law Project)', 'source_snapshot': snapshot,
        'source_snapshot_basis': 'snapshot label of the local bulk files; not a fresh provider request',
        'political_affiliations': affiliations,
        'political_affiliations_note': 'As recorded by CourtListener with its stated source and dates; not a finding about the judge.',
        'educations': educations, 'positions_count': positions,
        'positions_count_label': 'Position rows recorded by CourtListener (judicial and non-judicial, historical)',
        'how_selected': [{'code': code, 'label': CL_SELECTED.get(code), 'positions': n} for code, n in sorted(selected.items())],
    }


def completeness(flags):
    present = [name for name, _ in WEIGHTS if flags.get(name)]
    missing = [name for name, _ in WEIGHTS if not flags.get(name)]
    return {'score': sum(weight for name, weight in WEIGHTS if flags.get(name)), 'max': 100, 'present': present,
            'missing': missing, 'weights': dict(WEIGHTS),
            'note': 'Evidence coverage of this record in the saved corpus; not a measure of the judge.'}


def main():
    with open(FJC_CSV, encoding='utf-8-sig', newline='') as handle:
        fjc_rows = list(csv.DictReader(handle))
    by_nid = {}
    for row in fjc_rows:
        by_nid.setdefault(row['nid'].strip(), []).append(row)
    con, cl_snapshot, cl_people, schools = load_courtlistener()

    rows, unresolved, problems = [], [], []
    counts = Counter()
    seen_person = {}
    with open(ENTITIES, encoding='utf-8') as handle:
        for line in handle:
            entity = json.loads(line)
            entity_id = entity['entity_id']
            counts['entities'] += 1
            nids, captured = set(), set()
            for member in entity['members']:
                hit = NID_KEY.search(member.get('member_key') or '')
                if hit and member.get('source_class') == 'federal_biographies':
                    nids.add(hit.group(1))
                    stamp = member.get('captured_at')
                    captured.update(stamp if isinstance(stamp, list) else [stamp] if stamp else [])
            fjc = status = ids = courtlistener = None
            if len(nids) > 1 or (nids and len(by_nid.get(next(iter(nids)), [])) != 1):
                problems.append('entity %s: FJC nid not unique or not in export' % entity_id)
                nids = set()
            if nids:
                nid = next(iter(nids))
                source = by_nid[nid][0]
                as_of = max(captured)[:10]
                appointments = fjc_appointments(source)
                if not appointments:
                    problems.append('entity %s: FJC row without appointments' % entity_id)
                name = ' '.join(p for p in (clean(source.get(k)) for k in ('First Name', 'Middle Name', 'Last Name', 'Suffix')) if p)
                fjc = {'name_as_published': name, 'appointments': appointments,
                       'source': 'Federal Judicial Center, Biographical Directory of Article III Federal Judges (export)',
                       'source_url': FJC_URL, 'captured_at': max(captured)}
                status = fjc_status(source, appointments, as_of)
                counts['fjc_entities'] += 1
                counts['fjc_status_' + status['value']] += 1
                jid = clean(source.get('jid'))
                ids = {'fjc_nid': nid, 'fjc_jid': jid, 'cl_person_id': None, 'bridge_status': 'no_courtlistener_person_with_this_fjc_jid',
                       'bridge_basis': BRIDGE_BASIS,
                       'id_note': 'FJC nid, FJC jid and the CourtListener person id are three distinct namespaces.'}
                candidates = cl_people.get(jid or '', [])
                if len(candidates) == 1:
                    person = candidates[0]
                    reasons = []
                    if norm_name(person['name_last']) != norm_name(source.get('Last Name')):
                        reasons.append('surname_disagrees')
                    birth, dob = clean(source.get('Birth Year')), (person['date_dob'] or '')[:4]
                    if birth and dob and birth != dob:
                        reasons.append('birth_year_disagrees')
                    if reasons:
                        ids['bridge_status'] = 'withheld_for_review'
                        unresolved.append({'entity_id': entity_id, 'fjc_nid': nid, 'fjc_jid': jid,
                                           'cl_person_id_candidate': person['id'], 'reason': reasons})
                        counts['bridge_withheld'] += 1
                    else:
                        ids['cl_person_id'], ids['bridge_status'] = person['id'], 'linked_native_id'
                        if person['id'] in seen_person:
                            problems.append('CourtListener person %s bridged twice' % person['id'])
                        seen_person[person['id']] = entity_id
                        courtlistener = cl_block(con, schools, person['id'], cl_snapshot)
                        counts['bridged'] += 1
                elif len(candidates) > 1:
                    ids['bridge_status'] = 'withheld_for_review'
                    unresolved.append({'entity_id': entity_id, 'fjc_nid': nid, 'fjc_jid': jid,
                                       'cl_person_id_candidate': [p['id'] for p in candidates], 'reason': ['multiple_people_share_fjc_id']})
                    counts['bridge_withheld'] += 1
                role = fjc_role(appointments) if appointments else pattern_role(entity)
                if status['value'] in ('active', 'senior'):
                    counts['fjc_serving'] += 1
                    if role['value'] == 'district':
                        counts['fjc_serving_district'] += 1
                        counts['fjc_serving_district_' + status['value']] += 1
                        counts['fjc_serving_district_bridged'] += 1 if ids['cl_person_id'] else 0
            else:
                role = pattern_role(entity)
            counts['role_' + role['value']] += 1
            presidents = sorted({a['appointing_president'] for a in (fjc or {}).get('appointments', [])
                                 if a['appointing_president'] and not a['appointing_president'].startswith('None')})
            flags = {
                'fjc_appointments': bool(fjc and fjc['appointments']), 'fjc_status': bool(status),
                'cl_bridge': bool(ids and ids['cl_person_id']),
                'cl_political_affiliation': bool(courtlistener and courtlistener['political_affiliations']),
                'education': bool(entity.get('education')) or bool(courtlistener and courtlistener['educations']),
                'role_established': role['value'] != 'other', 'court_label': bool(entity.get('courts')),
                'state_label': bool(entity.get('state_labels')), 'biography': bool(entity.get('biography')),
                'professional_career': bool(entity.get('professional_career')), 'analyses': bool(entity.get('analysis_ids')),
            }
            rows.append({
                'entity_id': entity_id, 'name': entity.get('name'), 'fjc': fjc, 'fjc_status': status, 'ids': ids,
                'courtlistener': courtlistener, 'role_normalized': role, 'completeness': completeness(flags),
                'facets': {'status': status['value'] if status else None, 'role': role['value'],
                           'appointing_president': presidents},
                'temporal': {
                    'captured_at': fjc['captured_at'] if fjc else None,
                    'captured_at_basis': 'capture timestamp of the FJC export' if fjc else None,
                    'source_as_of': status['as_of'] if status else None,
                    'source_as_of_basis': status['as_of_basis'] if status else None,
                    'published_at': None, 'published_at_basis': None, 'effective_from': None, 'effective_from_basis': None,
                    'effective_to': None, 'effective_to_basis': None},
            })
    con.close()

    rows.sort(key=lambda r: r['entity_id'])
    unresolved.sort(key=lambda r: r['entity_id'])
    overlay = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows).encode('utf-8')
    review = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in unresolved).encode('utf-8')
    (HERE / 'overlay.jsonl').write_bytes(overlay)
    (HERE / 'unresolved.jsonl').write_bytes(review)

    text = overlay.decode('utf-8')
    ids_seen = [r['entity_id'] for r in rows]
    checks = [
        {'name': 'no_input_problems', 'passed': not problems, 'detail': problems[:20]},
        {'name': 'entity_ids_unique', 'passed': len(ids_seen) == len(set(ids_seen)), 'detail': len(ids_seen)},
        {'name': 'fjc_entities_match_export_rows', 'passed': counts['fjc_entities'] == len(fjc_rows) == len(by_nid),
         'detail': [counts['fjc_entities'], len(fjc_rows)]},
        {'name': 'every_status_is_dated_and_in_vocabulary',
         'passed': all(r['fjc_status'] is None or (r['fjc_status']['value'] in STATUS_VALUES and r['fjc_status']['as_of']
                       and r['fjc_status']['label'] == 'FJC-reported status as of ' + r['fjc_status']['as_of']) for r in rows)},
        {'name': 'word_current_absent_from_overlay', 'passed': 'current' not in text.casefold()},
        {'name': 'every_bridge_is_native_id_chain',
         'passed': all(not (r['ids'] and r['ids']['cl_person_id']) or (r['ids']['fjc_nid'] and r['ids']['fjc_jid']
                       and r['ids']['bridge_status'] == 'linked_native_id') for r in rows)},
        {'name': 'courtlistener_block_only_for_bridged',
         'passed': all((r['courtlistener'] is None) == (not (r['ids'] and r['ids']['cl_person_id'])) for r in rows)},
        {'name': 'role_in_vocabulary', 'passed': all(r['role_normalized']['value'] in ROLE_VALUES for r in rows)},
        {'name': 'no_filesystem_paths_in_overlay', 'passed': re.search(r'(?<![A-Za-z])[A-Za-z]:[\\/](?!/)|sources/|delivery/|\\\\Users', text) is None},
        {'name': 'audit_reference_counts (serving 1,464; serving district 1,134 = 648 active + 486 senior)',
         'passed': (counts['fjc_serving'], counts['fjc_serving_district'], counts['fjc_serving_district_active'],
                    counts['fjc_serving_district_senior']) == (1464, 1134, 648, 486),
         'detail': [counts['fjc_serving'], counts['fjc_serving_district'], counts['fjc_serving_district_active'],
                    counts['fjc_serving_district_senior']]},
    ]
    passed = all(c['passed'] for c in checks)
    counts['overlay_rows'], counts['unresolved_rows'] = len(rows), len(unresolved)
    as_of = next(r['fjc_status']['as_of'] for r in rows if r['fjc_status'])
    validation = {
        'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': passed,
        'validated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'data_files': [{'path': 'overlay.jsonl', 'sha256': hashlib.sha256(overlay).hexdigest(), 'rows': len(rows)},
                       {'path': 'unresolved.jsonl', 'sha256': hashlib.sha256(review).hexdigest(), 'rows': len(unresolved)}],
        'counts': dict(sorted(counts.items())), 'checks': checks,
        'qualification': ('Overlay of source-attributed facts keyed by judge entity id. Status is the FJC-reported status as of '
                          '%s (capture date of the FJC export), not independently verified service. CourtListener blocks come '
                          'from a bulk snapshot labelled %s and are reached only through the native id chain nid -> jid -> '
                          'people.fjc_id; no people are joined by name. Roles outside the FJC are pattern readings of the court '
                          'name as saved. Completeness measures evidence coverage in this corpus, not the judge.'
                          % (as_of, cl_snapshot)),
        'license_ref': 'FJC Biographical Directory export (U.S. government work); CourtListener bulk data (Free Law Project, CC0/public domain dedication as published by the provider).',
        'inputs': [{'path': p.relative_to(ROOT).as_posix(), 'sha256': sha256(p)} for p in (ENTITIES, FJC_CSV, CL_DB)],
        'source_as_of': {'fjc_export_captured': as_of, 'courtlistener_snapshot_label': cl_snapshot},
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': validation['counts'],
                      'failed': [c for c in checks if not c['passed']]}, indent=1)[:4000])
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
