"""Publish an immutable, searchable snapshot of validated judge observations."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

if not __debug__:
    raise RuntimeError('Publication requires validation assertions; do not run with -O')

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'delivery/judge_intelligence_20260913'
AUDIT = ROOT / 'reports/judges/focus_20260913'
STATE = ROOT / 'sources/trellis/judge_focus_20260913/worker'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def rows(path):
    with path.open(encoding='utf-8-sig') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def rel(path):
    return path.relative_to(ROOT).as_posix()

def component_files():
    result = []
    for name, source in [('official_profiles', BASE / 'official_profiles'), ('trellis_profiles', BASE / 'trellis_profiles'), ('trellis_directories', AUDIT)]:
        filenames = ([p.name for p in source.iterdir() if p.is_file() and p.suffix in {'.jsonl', '.csv', '.json', '.md'}]
                     if name != 'trellis_directories' else ['trellis_directory_rows.jsonl', 'trellis_directory_rows.csv', 'judge_reports.jsonl', 'trellis_source_audit.jsonl', 'schema_audit.json', 'coverage.json', 'validation.json', 'summary.json'])
        for filename in sorted(filenames):
            if filename not in {'files.sha256.json', 'scope.json', 'credit_preflight.json', 'root_package_approval.json'}:
                result.append((name, source / filename))
    sample = ROOT / 'sources/judges/focus_20260913/report_sample'
    for filename in ['preview_visible_dom.json', 'capture.json', 'trellis_judge_report_sample.pdf', 'trellis_judge_report_sample.txt']:
        if (sample / filename).exists():
            result.append(('report_sample', sample / filename))
    # OCR is included only after its independently reviewed completion receipt.
    accepted_ocr = AUDIT / 'sample_ocr_root_validation.json'
    if accepted_ocr.exists() and read(accepted_ocr).get('validated') is True:
        for path in sorted((sample / 'offline_ocr').rglob('*')):
            if path.is_file():
                result.append(('report_sample', path))
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--semantic-audit', type=Path, default=AUDIT / 'official_semantic_audit.json')
    args = parser.parse_args()
    official = BASE / 'official_profiles'
    trellis = BASE / 'trellis_profiles'
    ov, tv, dv = [read(p) for p in [official / 'validation.json', trellis / 'validation.json', AUDIT / 'validation.json']]
    assert ov['issues'] == 0 and ov['all_fact_values_reproduced_from_cited_evidence']
    assert tv['failure_count'] == 0 and tv['originals_unchanged'] and tv['all_independent_statistics_null']
    assert dv['issue_count'] == 0
    assert args.semantic_audit.exists(), 'Independent semantic audit is required before publication'
    semantic = read(args.semantic_audit)
    # The report is preserved in full; root review must explicitly approve the
    # final corrected dataset, never silently treat sampled checks as a census.
    approval = read(AUDIT / 'root_package_approval.json')
    assert approval['approved'] is True
    assert approval['official_profiles_sha256'] == sha(official / 'profiles.jsonl')
    assert approval['semantic_audit_sha256'] == sha(args.semantic_audit)
    files = component_files()
    assert {rel(path): sha(path) for _, path in files} == approval['component_sha256'], 'Derived component changed after root validation'
    stamp = datetime.datetime.now(datetime.timezone.utc)
    snapshot = BASE / 'snapshots' / stamp.strftime('%Y%m%dT%H%M%S%fZ')
    snapshot.mkdir(parents=True, exist_ok=False)
    inputs = {}
    for name, path in files:
        target = snapshot / name
        target.mkdir(exist_ok=True)
        digest = approval['component_sha256'][rel(path)]
        destination = target / (path.relative_to(ROOT / 'sources/judges/focus_20260913/report_sample') if name == 'report_sample' else path.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        assert sha(path) == digest == sha(destination), 'Component changed during snapshot'
        inputs[rel(path)] = {'sha256': digest, 'snapshot_path': rel(destination)}
    shutil.copyfile(args.semantic_audit, snapshot / 'official_semantic_audit.json')
    shutil.copyfile(AUDIT / 'root_package_approval.json', snapshot / 'root_package_approval.json')
    assert sha(snapshot / 'official_semantic_audit.json') == approval['semantic_audit_sha256']
    assert sha(snapshot / 'root_package_approval.json') == sha(AUDIT / 'root_package_approval.json')
    originals = {}
    def verify(path, digest):
        if not path or not digest:
            raise ValueError('A cited original must have both a path and SHA-256')
        assert '..' not in Path(path).parts and not Path(path).is_absolute()
        if path in originals:
            assert originals[path] == digest
            return
        assert sha(ROOT / path) == digest, 'Source original changed: ' + path
        originals[path] = digest

    reports = []
    counts = Counter()
    states = Counter()
    narratives = Counter()
    op = snapshot / 'official_profiles/profiles.jsonl'
    tp = snapshot / 'trellis_profiles/profiles.jsonl'
    dp = snapshot / 'trellis_directories/trellis_directory_rows.jsonl'
    for source in rows(snapshot / 'official_profiles/source_coverage.jsonl'):
        verify(source['raw_path'], source['raw_sha256'])
        if source.get('text_path'):
            verify(source['text_path'], source['text_sha256'])
    for source in rows(snapshot / 'trellis_profiles/input_manifest.jsonl'):
        verify(source['path'], source['sha256'])
    for source in rows(snapshot / 'trellis_directories/trellis_source_audit.jsonl'):
        verify(source['source_path'], source['source_sha256'])
    contacts = {}
    for fact in rows(snapshot / 'trellis_profiles/facts.jsonl'):
        if fact['field'] == 'public_office_contact':
            contacts.setdefault(fact['capture_id'], []).append({'field': fact['field'], 'value': fact['value'], 'fact_id': fact['fact_id'], 'evidence_file': rel(snapshot / 'trellis_profiles/facts.jsonl')})
    for line, p in enumerate(rows(op), 1):
        for fact in p['facts']:
            for ev in fact['evidence']:
                verify(ev['artifact_path'], ev['artifact_sha256'])
        record = {'record_id': 'official:' + p['profile_id'], 'source_class': 'official_observation',
            'name': p['observed_name'], 'state': p['state'], 'state_code': p['state_code'],
            'courts': p['courts'], 'counties': p['counties'], 'roles': p['roles'],
            'education': p['education_facts'], 'appointments': p['appointment_facts'],
            'service': p.get('service_facts', []), 'biography': p['biography_facts'],
            'professional_contacts': p['professional_contacts'], 'status': p['current_historical_status'],
            'source_url': p['source_url'], 'capture_times': p['capture_times'],
            'evidence_file': rel(op), 'evidence_line': line, 'fact_count': len(p['facts']),
            'numeric_analytics': None, 'independently_computed_statistics': None,
            'limitations': p['detail_limitations'] + ['Source-specific observation; cross-source person identity is unresolved.']}
        record['search_text'] = '\n'.join(str(f['value']) for f in p['facts'])
        reports.append(record)
    for line, p in enumerate(rows(tp), 1):
        verify(p['source_path'], p['source_sha256'])
        record = {'record_id': 'trellis-profile:' + p['capture_id'], 'source_class': 'trellis_profile',
            'name': p['reported_name'], 'state': p['reported_state'], 'state_code': None,
            'courts': [p['reported_court']] if p['reported_court'] else [],
            'counties': [p['reported_county']] if p['reported_county'] else [],
            'roles': list(dict.fromkeys(h['Role'] for h in p['career_history'] if h.get('Role'))),
            'current_appointment_label_as_reported': p['current_appointment_label'],
            'career_history_as_reported': p['career_history'],
            'education': p['education'], 'appointments': p['appointment_passages'],
            'service': p['professional_service_passages'], 'biography': p['biography'],
            'professional_contacts': contacts.get(p['capture_id'], []), 'status': None,
            'parse_status': p['status'], 'current_service_verified': False, 'source_url': p['source_url'],
            'source_path': p['source_path'], 'source_sha256': p['source_sha256'],
            'capture_times': [], 'evidence_file': rel(tp), 'evidence_line': line,
            'fact_count': p['fact_count'], 'dashboard_report_links': p['dashboard_report_links'],
            'numeric_analytics': p['directly_reported_analytics'], 'independently_computed_statistics': None,
            'limitations': p['gaps'] + ['Publisher claims; current service and cross-source identity are unverified.', 'Appointment passages include prior nonjudicial appointments; not a bench-only event list.', 'Career roles include historical and nonjudicial roles; current appointment is a separate publisher label.']}
        record['search_text'] = '\n'.join([p['reported_name'] or '', p['reported_state'] or '', p['reported_court'] or '', p['reported_county'] or '', p['biography'] or '', *p['education'], json.dumps(p['career_history'], ensure_ascii=False)])
        reports.append(record)
    for line, p in enumerate(rows(dp), 1):
        verify(p['source_path'], p['source_sha256'])
        record = {'record_id': 'trellis-directory:' + p['row_id'], 'source_class': 'trellis_directory',
            'name': p['reported_name'], 'state': p['state'], 'state_code': p['state_code'],
            'courts': [p['reported_court_county_combined']] if p['reported_court_county_combined'] else [],
            'counties': [p['reported_county']] if p['reported_county'] else [], 'roles': [],
            'education': [], 'appointments': [], 'service': [], 'biography': None,
            'professional_contacts': [], 'status': p['reported_status'],
            'court_county_label_as_reported': p['reported_court_county_combined'],
            'source_url': p['source_url'], 'profile_url': p['profile_url'],
            'source_path': p['source_path'], 'source_sha256': p['source_sha256'],
            'evidence_file': rel(dp), 'evidence_line': line, 'fact_count': None,
            'numeric_analytics': None, 'independently_computed_statistics': None,
            'limitations': [p['identity_note'], p['county_assignment_note'], 'Directory row; full individual biography is separate.']}
        record['search_text'] = '\n'.join(x for x in [p['reported_name'], p['state'], p['reported_status'], p['reported_court_county_combined']] if x)
        reports.append(record)
    state_codes = {p['state']: p['state_code'] for p in rows(snapshot / 'official_profiles/state_coverage.jsonl')}
    db = sqlite3.connect(snapshot / 'judge_search.sqlite3')
    db.execute('CREATE TABLE observations(record_id TEXT PRIMARY KEY, source_class TEXT, name TEXT, state TEXT, state_code TEXT, courts TEXT, counties TEXT, status TEXT, report_json TEXT)')
    db.execute('CREATE VIRTUAL TABLE search USING fts5(record_id UNINDEXED, name, state, courts, counties, detail, tokenize="unicode61")')
    output = snapshot / 'reports.jsonl'
    with output.open('w', encoding='utf-8') as f:
        for p in reports:
            p['state_code'] = p['state_code'] or state_codes.get(p['state'])
            text = p.pop('search_text')
            j = json.dumps(p, ensure_ascii=False)
            f.write(j + '\n')
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)', (p['record_id'], p['source_class'], p['name'], p['state'], p['state_code'], json.dumps(p['courts']), json.dumps(p['counties']), p['status'], j))
            db.execute('INSERT INTO search VALUES(?,?,?,?,?,?)', (p['record_id'], p['name'], p['state'], '\n'.join(p['courts']), '\n'.join(p['counties']), text))
            counts[p['source_class']] += 1
            states[(p['state_code'], p['source_class'])] += 1
            if p['biography'] or p['education'] or p['appointments'] or p['service']:
                narratives[p['source_class']] += 1
    db.commit()
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert db.execute('SELECT count(*) FROM observations').fetchone()[0] == len(reports)
    assert db.execute('SELECT count(*) FROM search').fetchone()[0] == len(reports)
    assert db.execute('SELECT count(*) FROM search WHERE search MATCH ?', ('"Bannon"',)).fetchone()[0] > 0
    assert db.execute('SELECT count(*) FROM observations WHERE source_class=? AND state_code=?', ('official_observation', 'TX')).fetchone()[0] == 363
    db.close()
    coverage = [{'state': name, 'state_code': code,
        'official_observations': states[(code, 'official_observation')],
        'trellis_profiles': states[(code, 'trellis_profile')],
        'trellis_directory_rows': states[(code, 'trellis_directory')],
        'unique_current_judges': None, 'complete': False} for name, code in state_codes.items()]
    write(snapshot / 'state_coverage.json', coverage)
    with (snapshot / 'state_coverage.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(coverage[0])); w.writeheader(); w.writerows(coverage)
    summary = {'built_at_utc': stamp.isoformat(), 'snapshot_path': rel(snapshot),
        'observation_reports': len(reports), 'reports_by_source_class': dict(counts),
        'observations_with_narrative_details': dict(narratives),
        'trellis_distinct_profile_urls': read(snapshot / 'trellis_profiles/summary.json')['distinct_publisher_profile_urls'],
        'official_fact_records': read(snapshot / 'official_profiles/summary.json')['facts'],
        'trellis_fact_records': read(snapshot / 'trellis_profiles/summary.json')['fact_records'],
        'directly_reported_numeric_analytics': read(snapshot / 'trellis_profiles/summary.json')['directly_reported_numeric_analytics'],
        'dashboard_report_link_records': read(snapshot / 'trellis_profiles/summary.json')['dashboard_report_link_records'],
        'freshly_verified_original_artifacts': len(originals),
        'unique_current_judges': None, 'cross_source_identity_merges': 0,
        'full_underlying_corpus_complete': False, 'focused_snapshot_validated': True,
        'semantic_review': 'Independent stratified sample plus specific regression checks; not full semantic certification',
        'acquisition_status_at_snapshot': read(STATE / 'active_run.json'),
        'search_database': rel(snapshot / 'judge_search.sqlite3'),
        'search_script': 'scripts/search_judge_intelligence.py'}
    write(snapshot / 'summary.json', summary)
    write(snapshot / 'source_hashes.json', originals)
    write(snapshot / 'component_inputs.json', inputs)
    write(snapshot / 'validation.json', {'validated_at_utc': stamp.isoformat(), 'issues': 0,
        'source_artifact_hashes_verified': len(originals), 'component_copies_hash_verified': len(inputs),
        'unique_record_ids': len({p['record_id'] for p in reports}), 'sqlite_integrity': 'ok',
        'fts_rows_match_reports': True, 'bannon_search_passed': True,
        'texas_source_filter_preserves_all363_assignments': True,
        'independent_statistics_all_null': all(p['independently_computed_statistics'] is None for p in reports),
        'raw_source_mutations': 0, 'prior_legal_package_mutations': 0})
    text = f'''# Judge data snapshot

Built {stamp.isoformat()}. This snapshot has {counts['official_observation']:,} official source-specific judge observations, {counts['trellis_profile']:,} Trellis profile captures and {counts['trellis_directory']:,} Trellis directory rows. These are separate observations, with repeated and historical people possible; they are not a unique current-judge count.

Biographies, education, appointment/service history, court/county labels and professional contacts are available where the saved source supplies them. `reports.jsonl` contains evidence-linked individual reports. Component JSONL/CSV files retain full literal fact evidence, original paths and SHA-256 hashes. `state_coverage.csv` shows the actual coverage and gaps.

Search from the workspace with `python scripts/search_judge_intelligence.py Bannon --state AZ --source trellis_profile`. Optional filters include state, source class, court and county. `judge_search.sqlite3` provides SQLite full-text search; it is an index of source observations and does not merge identities.

Observed dashboard/report links are in `trellis_profiles/dashboard_report_links.jsonl`. Verified numeric analytics rows: {summary['directly_reported_numeric_analytics']}. Missing metrics stay null. Saved biographies and recent-case snippets do not establish win rates, motion grant rates, decision timing or predictions. Paid dashboard access remains dependent on the authorized browser session.

The signed-in browser confirms full Judge Analytics is an additional subscription on the current account. `report_sample/` preserves the publisher's 12-page marketing sample PDF and preview evidence separately; its sample content is not attributed to the profiled judge. The PDF has negligible embedded text; accepted offline OCR derivatives, when included, are under `report_sample/offline_ocr/` with their extraction limits.

Originals stay in their cited workspace locations; this snapshot freezes normalized evidence and manifests. Fresh source hashes, component validation and the independent semantic sample are included. Acquisition can continue outside this dated snapshot. The full national judge corpus remains incomplete.
'''
    (snapshot / 'README.md').write_text(text, encoding='utf-8')
    manifest = {rel(p): {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(snapshot.rglob('*')) if p.is_file()}
    write(snapshot / 'files.sha256.json', manifest)
    write(BASE / 'latest.json', {'snapshot_path': rel(snapshot), 'summary_path': rel(snapshot / 'summary.json'), 'manifest_sha256': sha(snapshot / 'files.sha256.json')})
    write(BASE / 'summary.json', summary)
    (BASE / 'README.md').write_text(text + '\nCurrent immutable snapshot: `' + rel(snapshot) + '`\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True, indent=2))

if __name__ == '__main__':
    main()
