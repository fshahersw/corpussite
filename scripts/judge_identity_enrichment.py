"""Build a conservative, offline identity layer over a verified judge snapshot."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import datetime
import hashlib
import itertools
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'delivery/judge_intelligence_20260913'
DEFAULT_OUT = ROOT / 'delivery/judge_enrichment_20260914/identity'
VERSION = 'judge-identity-conservative-1.0.0'
GENERIC_COURTS = {
    'court', 'district court', 'circuit court', 'superior court', 'supreme court',
    'court of appeals', 'county court', 'municipal court', 'probate court',
    'family court', 'justice court', 'state court', 'juvenile court', 'trial court',
    'court of common pleas', 'court of chancery', 'appellate court',
}
NAME_PREFIX = re.compile(r'^(?:(?:the\s+)?hon(?:orable)?\.?\s+|judge\s+|justice\s+)', re.I)
SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def ident(prefix, values):
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return prefix + hashlib.sha256(raw.encode()).hexdigest()[:24]


def clean(value):
    return unicodedata.normalize('NFKC', value or '').replace('\u2019', "'").replace('\u2018', "'").replace('\u2011', '-').replace('\u2010', '-').strip()


def normalize_name(value):
    value = clean(value)
    for _ in range(3):
        new = NAME_PREFIX.sub('', value)
        if new == value:
            break
        value = new
    # Preserve accents, surname punctuation, middle names/initials and suffixes.
    value = re.sub(r'[.,]', ' ', value).casefold()
    return ' '.join(value.split())


def full_name_eligible(value):
    tokens = normalize_name(value).split()
    if len(tokens) >= 3 and tokens[-1] in SUFFIXES:
        tokens = tokens[:-1]
    if len(tokens) < 2:
        return False
    return all(sum(c.isalpha() for c in t) >= 2 for t in [tokens[0], tokens[-1]]) and not any(c.isdigit() for c in ' '.join(tokens))


def normalize_court(value, state_code, state_name):
    value = clean(value).casefold()
    for state in [state_code or '', state_name or '']:
        if state:
            value = re.sub(r'^' + re.escape(state.casefold()) + r'\s*[-\u2013\u2014]\s*', '', value)
    if state_code:
        value = re.sub(r'\s*\(' + re.escape(state_code.casefold()) + r'\)\s*$', '', value)
    if state_name:
        value = re.sub(r'\s+of\s+(?:the\s+state\s+of\s+)?' + re.escape(state_name.casefold()) + r'\s*$', '', value)
    value = value.replace('&', ' and ')
    value = re.sub(r'[.,:;()\[\]]', ' ', value)
    return ' '.join(value.split())


def specific_court(key):
    return bool(key and key not in GENERIC_COURTS and re.search(r'\bcourt\b', key))


def trellis_family(row):
    value = row.get('profile_url') or (row.get('source_url') if row['source_class'] == 'trellis_profile' else None)
    if not value:
        return None
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or p.hostname not in ('trellis.law', 'www.trellis.law') or not re.fullmatch(r'/judge/[^/]+/?', p.path) or p.query:
        return None
    return urlunsplit(('https', 'trellis.law', p.path.rstrip('/'), '', ''))


class Snapshot:
    def __init__(self):
        latest_path = BASE / 'latest.json'
        latest_bytes = latest_path.read_bytes()
        latest = json.loads(latest_bytes)
        self.path = (ROOT / latest['snapshot_path']).resolve()
        self.path.relative_to((BASE / 'snapshots').resolve())
        manifest_path = self.path / 'files.sha256.json'
        if sha(manifest_path) != latest['manifest_sha256']:
            raise ValueError('Sealed package manifest does not match latest.json')
        self.manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        self.inputs = {}
        self.pin = {'snapshot_path': self.path.relative_to(ROOT).as_posix(),
                    'manifest_sha256': latest['manifest_sha256'],
                    'latest_path': latest_path.relative_to(ROOT).as_posix(),
                    'latest_sha256_at_start': hashlib.sha256(latest_bytes).hexdigest()}

    def load(self, name):
        path = (self.path / name).resolve()
        path.relative_to(self.path)
        relative = path.relative_to(ROOT).as_posix()
        expected = self.manifest[relative]
        digest = sha(path)
        if digest != expected['sha256'] or path.stat().st_size != expected['bytes']:
            raise ValueError('Sealed input changed: ' + relative)
        self.inputs[relative] = {'sha256': digest, 'bytes': path.stat().st_size}
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def prepare(snapshot):
    reports = snapshot.load('reports.jsonl')
    parts = {kind: snapshot.load(name) for kind, name in {
        'official_observation': 'official_profiles/profiles.jsonl',
        'trellis_profile': 'trellis_profiles/profiles.jsonl',
        'trellis_directory': 'trellis_directories/trellis_directory_rows.jsonl',
    }.items()}
    part_names = {'official_observation': 'official_profiles/profiles.jsonl', 'trellis_profile': 'trellis_profiles/profiles.jsonl', 'trellis_directory': 'trellis_directories/trellis_directory_rows.jsonl'}
    state_rows = snapshot.load('official_profiles/state_coverage.jsonl')
    state_names = {x['state_code']: x['state'] for x in state_rows}
    profile_facts = defaultdict(list)
    for index, fact in enumerate(snapshot.load('trellis_profiles/facts.jsonl'), 1):
        if fact['field'] in {'reported_name', 'reported_state', 'reported_court', 'reported_county', 'current_appointment_label'}:
            profile_facts[fact['capture_id']].append({'line': index, 'fact': fact})
    observations, sources = [], {}
    if len({r['record_id'] for r in reports}) != len(reports):
        raise ValueError('Duplicate input source observation ID')
    for index, row in enumerate(reports, 1):
        kind = row['source_class']
        part_path = (snapshot.path / part_names[kind]).relative_to(ROOT).as_posix()
        if row['evidence_file'] != part_path:
            raise ValueError('Report points outside its expected sealed component')
        part = parts[kind][row['evidence_line'] - 1]
        if kind == 'official_observation':
            expected_id, name, state, courts = 'official:' + part['profile_id'], part['observed_name'], part['state'], part['courts']
            identity_facts = [{'fact_index': i, 'fact': f} for i, f in enumerate(part['facts']) if f['category'] in {'observed_name', 'state', 'court', 'court_context', 'court_and_county_context', 'county', 'county_as_listed'}]
            originals = [{'path': part['raw_path'], 'sha256': part['raw_sha256']}]
            if part.get('text_path'):
                originals.append({'path': part['text_path'], 'sha256': part['text_sha256']})
            court_evidence = [f['fact']['value'] for f in identity_facts if f['fact']['category'] in {'court', 'court_context', 'court_and_county_context'} and isinstance(f['fact']['value'], str)]
        elif kind == 'trellis_profile':
            expected_id, name, state = 'trellis-profile:' + part['capture_id'], part['reported_name'], part['reported_state']
            courts = [part['reported_court']] if part['reported_court'] else []
            identity_facts = profile_facts[part['capture_id']]
            originals = [{'path': part['source_path'], 'sha256': part['source_sha256']}]
            court_evidence = [f['fact']['value'] for f in identity_facts if f['fact']['field'] == 'reported_court']
        else:
            expected_id, name, state = 'trellis-directory:' + part['row_id'], part['reported_name'], part['state']
            courts = [part['reported_court_county_combined']] if part['reported_court_county_combined'] else []
            identity_facts = [{'directory_row_evidence': part['evidence']}]
            originals = [{'path': part['source_path'], 'sha256': part['source_sha256']}]
            court_evidence = [c['text'] for c in part['evidence']['cells'] if 'Court' in c.get('logical_headers', [])]
        if (row['record_id'], row['name'], row['state'], row['courts'], row['source_url']) != (expected_id, name, state, courts, part['source_url']):
            raise ValueError('Report/component identity fields disagree: ' + row['record_id'])
        code = row.get('state_code')
        state_valid = code in state_names and clean(state).casefold() == clean(state_names[code]).casefold()
        court_keys = sorted(set(normalize_court(c, code, state_names.get(code)) for c in courts if c))
        evidence_keys = set(normalize_court(c, code, state_names.get(code)) for c in court_evidence if c)
        refs = {'report_path': (snapshot.path / 'reports.jsonl').relative_to(ROOT).as_posix(),
                'report_line': index, 'component_path': part_path, 'component_line': row['evidence_line'],
                'component_sha256': snapshot.inputs[part_path]['sha256'], 'originals_as_recorded_in_sealed_package': originals}
        if kind == 'trellis_profile':
            refs['identity_facts_path'] = (snapshot.path / 'trellis_profiles/facts.jsonl').relative_to(ROOT).as_posix()
        obs = {'source_observation_id': row['record_id'], 'source_class': kind,
               'source_url': row['source_url'], 'publisher_family_url': trellis_family(row),
               'reported_name': name, 'normalized_full_name': normalize_name(name),
               'full_name_eligible': full_name_eligible(name), 'state': state, 'state_code': code,
               'explicit_state_consistent': state_valid, 'reported_courts': courts,
               'court_keys': court_keys, 'reported_counties': row.get('counties', []),
               'specific_court_with_literal_evidence': len(court_keys) == 1 and specific_court(court_keys[0]) and court_keys[0] in evidence_keys,
               'source_record': refs, 'identity_evidence': identity_facts,
               'source_status_as_reported': row.get('status')}
        observations.append(obs)
        sources[row['record_id']] = {'report': row, 'component': part}
    return observations, sources, reports


def match(observations):
    by_name, by_state_name, families = defaultdict(list), defaultdict(list), defaultdict(list)
    lookup = {o['source_observation_id']: o for o in observations}
    for o in observations:
        by_name[o['normalized_full_name']].append(o)
        by_state_name[(o['normalized_full_name'], o['state_code'])].append(o)
        if o['publisher_family_url']:
            families[o['publisher_family_url']].append(o)
    bad_families = {key for key, members in families.items() if len({(x['normalized_full_name'], x['state_code']) for x in members}) > 1}
    group_issues = {}
    for key, group in by_state_name.items():
        reasons = []
        if any(not x['full_name_eligible'] for x in group): reasons.append('initial_only_or_incomplete_full_name')
        if any(not x['explicit_state_consistent'] for x in group): reasons.append('missing_or_conflicting_explicit_state')
        if len({c for x in group for c in x['court_keys']}) > 1: reasons.append('conflicting_reported_court_context_no_history_bridge')
        if any(not x['specific_court_with_literal_evidence'] for x in group): reasons.append('missing_generic_or_unsupported_court_context')
        urls = {x['publisher_family_url'] for x in group if x['publisher_family_url']}
        if len(urls) > 1: reasons.append('competing_trellis_profile_urls')
        if urls & bad_families: reasons.append('publisher_profile_url_name_or_state_conflict')
        official_counts = Counter(x['source_url'] for x in group if x['source_class'] == 'official_observation')
        if any(n > 1 for n in official_counts.values()): reasons.append('duplicate_official_source_entries_ambiguous')
        group_issues[key] = reasons
    parent = {key: key for key in lookup}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]; key = parent[key]
        return key
    decisions = []
    confirmed_counts, candidate_counts = Counter(), Counter()
    for name, group in sorted(by_name.items()):
        if not name or len(group) < 2:
            continue
        for a, b in itertools.combinations(sorted(group, key=lambda x: x['source_observation_id']), 2):
            reasons = []
            if a['state_code'] != b['state_code']:
                reasons = ['explicit_state_mismatch']
            else:
                reasons = list(group_issues[(name, a['state_code'])])
            if a['source_class'].startswith('trellis') and b['source_class'].startswith('trellis') and not (a['publisher_family_url'] and a['publisher_family_url'] == b['publisher_family_url']):
                reasons.append('publisher_profile_link_not_identical')
            confirmed = not reasons
            level = None
            if confirmed:
                level = 'same_publisher_profile_link_and_exact_name_state_court' if a['source_class'].startswith('trellis') and b['source_class'].startswith('trellis') else 'independent_sources_exact_full_name_state_court'
                pa, pb = find(a['source_observation_id']), find(b['source_observation_id'])
                parent[max(pa, pb)] = min(pa, pb)
            pair = [a['source_observation_id'], b['source_observation_id']]
            for key in pair:
                candidate_counts[key] += 1
                if confirmed: confirmed_counts[key] += 1
            decisions.append({'decision_id': ident('decision-', pair), 'source_observation_ids': pair,
                              'decision': 'confirmed_rule_link' if confirmed else 'not_linked',
                              'certainty_basis': level, 'reasons': reasons,
                              'normalized_full_name': name, 'state_codes': [a['state_code'], b['state_code']],
                              'court_keys': [a['court_keys'], b['court_keys']],
                              'source_records': [a['source_record'], b['source_record']],
                              'not_linked_does_not_establish_different_people': not confirmed})
    groups = defaultdict(list)
    for key in sorted(lookup): groups[find(key)].append(key)
    identities, mapping = [], []
    for member_ids in sorted(groups.values(), key=lambda x: x[0]):
        members = [lookup[x] for x in member_ids]
        linked = len(members) > 1
        independent = linked and any(x['source_class'] == 'official_observation' for x in members)
        judge_id = ident('judge-', member_ids)
        basis = 'confirmed_independent_source_group' if independent else ('confirmed_within_publisher_group' if linked else 'unresolved_source_observation')
        statuses = sorted({str(x['source_status_as_reported']) for x in members if x['source_status_as_reported'] not in (None, '', 'unknown', 'unresolved')})
        identities.append({'judge_id': judge_id, 'canonical_judge_id': judge_id,
                           'preferred_display_name': members[0]['reported_name'],
                           'names_as_reported': sorted({x['reported_name'] for x in members if x['reported_name']}),
                           'normalized_full_names': sorted({x['normalized_full_name'] for x in members}),
                           'state_codes': sorted({x['state_code'] for x in members if x['state_code']}),
                           'source_observation_ids': member_ids, 'observation_count': len(members),
                           'identity_status': basis, 'unique_real_person_certified': False,
                           'current_judicial_status': None, 'current_service_verified': False,
                           'status_claims': [{'source_observation_id': x['source_observation_id'], 'value': x['source_status_as_reported']} for x in members],
                           'conflicting_nonempty_status_claims': len(statuses) > 1,
                           'court_claims': [{'source_observation_id': x['source_observation_id'], 'courts': x['reported_courts'], 'counties': x['reported_counties']} for x in members],
                           'known_conflicts': group_issues[(members[0]['normalized_full_name'], members[0]['state_code'])] if not linked else []})
        for o in members:
            reasons = group_issues[(o['normalized_full_name'], o['state_code'])]
            status = basis if linked else ('unresolved_candidate_group' if candidate_counts[o['source_observation_id']] else 'single_source_observation')
            mapping.append({**o, 'judge_id': judge_id, 'match_status': status,
                            'automatic_link_blockers': reasons if not linked else [],
                            'candidate_pair_count': candidate_counts[o['source_observation_id']],
                            'confirmed_pair_count': confirmed_counts[o['source_observation_id']],
                            'current_service_verified': False})
    return identities, sorted(mapping, key=lambda x: x['source_observation_id']), decisions


def timelines(mapping, sources):
    events = []
    for m in mapping:
        source = sources[m['source_observation_id']]
        row, part = source['report'], source['component']
        values = [('court_affiliation_as_reported', {'courts': row['courts'], 'counties': row.get('counties', []), 'roles': row.get('roles', [])})]
        for field, kind in [('appointments', 'appointment_or_selection_passage_as_reported'), ('service', 'service_passage_as_reported')]:
            values.extend((kind, value) for value in row.get(field, []))
        values.extend(('career_history_row_as_reported', value) for value in row.get('career_history_as_reported', []))
        if m['source_class'] == 'official_observation':
            values.extend(('structured_service_field_as_reported', {'field': f['category'], 'value': f['value'], 'evidence': f['evidence']}) for f in part['facts'] if f['category'] in {'service_begin_as_listed', 'service_end_as_listed', 'appointment_year_as_listed'})
        for index, (kind, value) in enumerate(values):
            explicit_dates = {k: v for k, v in value.items() if k.casefold() in {'from', 'to', 'start', 'end', 'year', 'years', 'date', 'appointed', 'elected'} and v} if isinstance(value, dict) else {}
            if kind == 'structured_service_field_as_reported': explicit_dates[value['field']] = value['value']
            events.append({'event_id': ident('service-', [m['source_observation_id'], index, kind]),
                           'judge_id': m['judge_id'], 'source_observation_id': m['source_observation_id'],
                           'event_kind': kind, 'value_as_reported': value, 'explicit_date_fields_as_reported': explicit_dates,
                           'normalized_event_start': None, 'normalized_event_end': None,
                           'capture_times_not_service_dates': row.get('capture_times', []),
                           'date_interpretation': 'Undated unless the source supplies explicit fields; no year, appointment type, or chronology inferred from narrative.',
                           'current_service_verified': False, 'source_record': m['source_record']})
    return events


def write_rows(out, name, rows):
    path = out / (name + '.jsonl')
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')


def write_csv(out, name, rows, fields):
    with (out / (name + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows:
            values = {}
            for key in fields:
                value = r.get(key)
                if isinstance(value, (dict, list)): value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, str) and value.startswith(('=', '+', '-', '@')): value = "'" + value
                values[key] = value
            w.writerow(values)


def write_json(out, name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def validate(observations, judges, mapping, decisions, events):
    errors = []
    expected = {x['source_observation_id'] for x in observations}
    actual = [x['source_observation_id'] for x in mapping]
    if len(actual) != len(set(actual)) or set(actual) != expected: errors.append('observation_mapping_not_bijective')
    by_id = {x['source_observation_id']: x for x in mapping}
    confirmed_pairs = {tuple(x['source_observation_ids']) for x in decisions if x['decision'] == 'confirmed_rule_link'}
    for judge in judges:
        members = [by_id[x] for x in judge['source_observation_ids']]
        if len(members) > 1:
            if len({(m['normalized_full_name'], m['state_code'], tuple(m['court_keys'])) for m in members}) != 1: errors.append('merged_context_mismatch')
            if any(not m['full_name_eligible'] or not m['explicit_state_consistent'] or not m['specific_court_with_literal_evidence'] for m in members): errors.append('merged_insufficient_evidence')
            for a, b in itertools.combinations(sorted(judge['source_observation_ids']), 2):
                if (a, b) not in confirmed_pairs: errors.append('transitive_merge_without_all_pair_proofs')
        if judge['current_judicial_status'] is not None or judge['unique_real_person_certified']: errors.append('unsupported_current_or_unique_person_claim')
    if any(x['source_observation_id'] not in expected or x['judge_id'] != by_id[x['source_observation_id']]['judge_id'] for x in events): errors.append('timeline_identity_mismatch')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.output.resolve()
    out.relative_to((ROOT / 'delivery/judge_enrichment_20260914').resolve())
    if out.exists(): raise FileExistsError('Choose a new output directory; existing enrichment evidence is not overwritten: ' + str(out))
    snapshot = Snapshot()
    observations, sources, reports = prepare(snapshot)
    judges, mapping, decisions = match(observations)
    events = timelines(mapping, sources)
    errors = validate(observations, judges, mapping, decisions, events)
    if errors: raise ValueError(errors)
    # Recheck every consumed sealed input immediately before producing outputs.
    for path, entry in snapshot.inputs.items():
        if sha(ROOT / path) != entry['sha256']: raise ValueError('Input changed during build: ' + path)
    out.mkdir(parents=True)
    for name, data in [('observation_identity', mapping), ('judges', judges), ('match_decisions', decisions), ('service_timeline', events)]: write_rows(out, name, data)
    write_csv(out, 'observation_identity', mapping, ['source_observation_id', 'judge_id', 'match_status', 'source_class', 'reported_name', 'normalized_full_name', 'state_code', 'reported_courts', 'reported_counties', 'automatic_link_blockers', 'source_url', 'source_record'])
    write_csv(out, 'judges', judges, ['judge_id', 'preferred_display_name', 'names_as_reported', 'state_codes', 'identity_status', 'observation_count', 'source_observation_ids', 'current_judicial_status', 'conflicting_nonempty_status_claims'])
    write_csv(out, 'match_decisions', decisions, ['decision_id', 'source_observation_ids', 'decision', 'certainty_basis', 'reasons', 'normalized_full_name', 'state_codes', 'court_keys'])
    write_csv(out, 'service_timeline', events, ['event_id', 'judge_id', 'source_observation_id', 'event_kind', 'value_as_reported', 'explicit_date_fields_as_reported', 'normalized_event_start', 'normalized_event_end', 'source_record'])
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    summary = {'built_at_utc': now, 'method_version': VERSION, 'source_snapshot': snapshot.pin,
               'source_observations': len(observations), 'observations_by_source_class': dict(Counter(x['source_class'] for x in observations)),
               'identity_groups_including_unresolved_singletons': len(judges),
               'identity_groups_by_status': dict(Counter(x['identity_status'] for x in judges)),
               'linked_observations': sum(x['observation_count'] for x in judges if x['observation_count'] > 1),
               'candidate_pairs': len(decisions), 'confirmed_pairs': sum(x['decision'] == 'confirmed_rule_link' for x in decisions),
               'pair_decisions_by_basis': dict(Counter(x['certainty_basis'] or 'not_linked' for x in decisions)),
               'non_link_reasons': dict(Counter(reason for x in decisions for reason in x['reasons'])),
               'reported_service_evidence_rows': len(events), 'current_status_conflict_groups': sum(x['conflicting_nonempty_status_claims'] for x in judges),
               'unique_judges_count': None, 'unique_current_judges_count': None, 'national_identity_resolution_complete': False,
               'network_requests': 0, 'source_mutations': 0}
    method = {'version': VERSION, 'canonical_id_basis': 'SHA-256 of the sorted exact source observation IDs, truncated to 24 hex; deterministic for fixed membership, not a government person identifier.',
              'automatic_match_requirements': ['Exact NFKC/casefold full-name key with first and last tokens having at least two letters; middle names, middle initials, accents and suffixes retained.', 'Explicit state name and code agree with the sealed state map.', 'One identical specific full court key per observation, supported by its literal sealed field evidence.', 'No competing Trellis profile URL, conflicting current court label, incomplete competing court context, or duplicate official source entry anywhere in the exact name/state group.', 'Trellis-to-Trellis pairs additionally require the identical observed publisher profile URL.', 'Every pair within a linked group must independently pass; no fuzzy or unproved transitive joins.'],
              'court_normalization': 'Unicode/case/whitespace/punctuation normalization; remove only matching state abbreviation/name prefix followed by dash, matching parenthetical abbreviation suffix, or trailing of [the State of] STATE. No numbered-court aliases or city/county inference.',
              'county_only_matching': False,
              'not_linked_semantics': 'Insufficient evidence for automatic consolidation; does not prove two different people.',
              'historical_evidence': 'Retained verbatim as service evidence. No narrative dates, status, court transitions or current service inferred. History cannot bridge incompatible reported court labels.',
              'external_source_matching': 'Not included; root must apply comparable source evidence requirements before adding links.',
              'fresh_hash_validation_scope': 'Every consumed sealed normalized file verified twice. Original file hashes are retained from the sealed package but original bodies are not reread or recertified.',
              'inputs': snapshot.inputs, 'input_snapshot': snapshot.pin,
              'script_path': Path(__file__).resolve().relative_to(ROOT).as_posix(), 'script_sha256': sha(Path(__file__))}
    write_json(out, 'method.json', method); write_json(out, 'summary.json', summary)
    write_json(out, 'validation.json', {'validated': True, 'validated_at_utc': now, 'issues': errors, 'issue_count': len(errors), 'all_source_observations_retained_once': True, 'all_merged_pairs_explicitly_confirmed': True, 'all_current_statuses_unknown': True, 'consumed_sealed_inputs_hash_verified': len(snapshot.inputs), 'source_mutations': 0, 'network_requests': 0, 'output_sha256': {name: sha(out / name) for name in ['observation_identity.jsonl', 'judges.jsonl', 'match_decisions.jsonl', 'service_timeline.jsonl']}})
    text = f'''# Conservative judge identity layer

This offline layer preserves all {len(observations):,} source observations from the pinned sealed judge snapshot. It creates {len(judges):,} identity groups, including unresolved single observations. These counts are not a count of distinct people or current judges.

`observation_identity.jsonl` maps each existing report ID to `judge_id`; `judges.jsonl` summarizes groups. `match_decisions.jsonl` records every exact-name candidate pair and its evidence or reason for no link. Same-publisher profile links and independently corroborated source pairs are distinguished. CSV versions are included.

Links require the exact normalized full name, explicit state, and a specific identical court supported by literal source fields. Initial-only first/last names, name-only matches, fuzzy spellings, omitted middle initials, court aliases, county-only matches, competing publisher URLs, duplicate official entries and conflicting affiliations do not produce automatic links. Every pair in a group must pass independently.

`service_timeline.jsonl` preserves reported affiliations, appointment/service passages, career rows and explicitly labeled service dates. Narrative dates are not interpreted or attached to inferred events; undated rows stay undated. Capture timestamps are not service dates. Current judicial status remains unknown; conflicting source statuses are retained.

The original package and source files are unchanged. `method.json` pins consumed file hashes and normalization rules. Original artifact references and their previously sealed hashes are carried forward; this run verifies the sealed components, not current website truth. Canonical IDs are deterministic for fixed group membership and may change when reviewed new evidence changes that membership. Rebuilding requires a new output directory.
'''
    (out / 'README.md').write_text(text, encoding='utf-8')
    write_json(out, 'files.sha256.json', {p.relative_to(out).as_posix(): {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()})
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
