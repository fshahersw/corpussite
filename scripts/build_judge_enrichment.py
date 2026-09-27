"""Seal a standardized judge corpus with source-specific facts and analysis."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'delivery/judge_enrichment_20260914'

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()

def rel(path):
    return path.resolve().relative_to(ROOT).as_posix()

def safe_path(value):
    path = (ROOT / value).resolve()
    path.relative_to(ROOT)
    return path

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def rows(path):
    with path.open(encoding='utf-8-sig') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def write_rows(path, objects):
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for obj in objects:
            f.write(json.dumps(obj, ensure_ascii=False) + '\n')

def write_csv(path, objects, fields):
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for obj in objects:
            writer.writerow({k: compact(obj.get(k)) if isinstance(obj.get(k), (list,dict)) else obj.get(k) for k in fields})

def compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))

def ident(prefix, value):
    return prefix + hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]

def flatten(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ' '.join(flatten(v) for v in value.values())
    if isinstance(value, list):
        return ' '.join(flatten(v) for v in value)
    return str(value)

def reported_system(p):
    labels = p.get('courts') or []
    text = ' '.join(labels).casefold()
    if any(t in text for t in ['immigration court','immigration appeals','patent trial and appeal','administrative patent']):
        return 'administrative', 'Explicit administrative court label as reported'
    if any(t in text for t in ['u.s.','united states','federal']):
        return 'federal', 'Explicit federal court label as reported'
    if p.get('state_code') and re.search(r'\b(?:county|municipal|superior|circuit|district|supreme|magistrate|justice|probate|family|juvenile)\s+court\b|\bcourt of (?:appeals|common pleas|chancery)\b', text):
        return 'state', 'State/local court label and explicit source jurisdiction as reported; not independent current-office verification'
    return 'unknown', 'Insufficient explicit court/system evidence'

def bound_outputs(folder, validation, required):
    bindings = validation.get('output_sha256')
    if not isinstance(bindings, dict):
        raise ValueError('Validation requires semantic output digest bindings: ' + str(folder))
    for name in required:
        if not bindings.get(name) or digest(folder / name) != bindings[name]:
            raise ValueError('Post-validation semantic output changed: ' + str(folder / name))

def publish_staged(value):
    snap = value.resolve()
    snap.relative_to((BASE / 'snapshots').resolve())
    receipt_path = ROOT / 'reports/judges/enrichment_20260914/independent_review.json'
    receipt = read(receipt_path)
    manifest_path = snap / 'files.sha256.json'
    if receipt.get('validated') is not True or receipt.get('unresolved_material_findings') != 0:
        raise ValueError('Independent enrichment review has not passed')
    if receipt['snapshot_path'] != rel(snap) or receipt['manifest_sha256'] != digest(manifest_path):
        raise ValueError('Independent review does not bind this exact snapshot')
    for name, item in read(manifest_path).items():
        path = safe_path(name)
        path.relative_to(snap)
        if digest(path) != item['sha256']:
            raise ValueError('Reviewed snapshot changed: ' + name)
    for name, sha in read(snap / 'verified_originals.json').items():
        if digest(safe_path(name)) != sha:
            raise ValueError('Reviewed original changed: ' + name)
    for code in [Path(__file__), ROOT / 'scripts/search_judge_enrichment.py']:
        if digest(code) != digest(snap / 'pipeline' / code.name):
            raise ValueError('Pipeline changed after snapshot review')
    review_digest = digest(receipt_path)
    sealed_review = BASE / 'reviews' / (snap.name + '.json')
    sealed_review.parent.mkdir(parents=True, exist_ok=True)
    if sealed_review.exists():
        if digest(sealed_review) != review_digest:
            raise ValueError('A different immutable review already exists for this snapshot')
    else:
        shutil.copyfile(receipt_path, sealed_review)
    if digest(receipt_path) != review_digest or digest(sealed_review) != review_digest:
        raise ValueError('Review changed during publication')
    write(BASE / 'latest.json', {'snapshot_path': rel(snap), 'manifest_sha256': digest(manifest_path),
        'independent_review_path': rel(sealed_review), 'independent_review_sha256': review_digest})
    write(BASE / 'summary.json', read(snap / 'summary.json'))
    (BASE / 'README.md').write_text('# Judge enrichment\n\nLatest validated snapshot: [' + snap.name + '](snapshots/' + snap.name + '/README.md).\n\nProfessional biographies, evidence-linked facts, conservative identity groups and official performance evaluations are standardized in JSONL, CSV and searchable SQLite. See [SCHEMA.md](SCHEMA.md), the snapshot README and summary for scope, counts and limitations.\n', encoding='utf-8')
    print(json.dumps({'published': rel(snap), 'independent_review_verified': True}))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-snapshot', type=Path)
    parser.add_argument('--publish-staged', type=Path, help='Publish an exact staged snapshot after independent review')
    args = parser.parse_args()
    if args.publish_staged:
        publish_staged(args.publish_staged)
        return
    pipeline_digests = {path: digest(path) for path in [Path(__file__), ROOT / 'scripts/search_judge_enrichment.py']}
    pointer = read(ROOT / 'delivery/judge_intelligence_20260913/latest.json')
    old = args.base_snapshot.resolve() if args.base_snapshot else safe_path(pointer['snapshot_path'])
    old.relative_to(ROOT / 'delivery/judge_intelligence_20260913/snapshots')
    if args.base_snapshot is None and digest(old / 'files.sha256.json') != pointer['manifest_sha256']:
        raise ValueError('Base snapshot manifest differs from sealed pointer')
    old_manifest = read(old / 'files.sha256.json')
    for name, item in old_manifest.items():
        path = safe_path(name)
        path.relative_to(old)
        if digest(path) != item['sha256']:
            raise ValueError('Base snapshot changed: ' + name)
    stamp = dt.datetime.now(dt.timezone.utc)
    snap = BASE / 'snapshots' / stamp.strftime('%Y%m%dT%H%M%S%fZ')
    snap.mkdir(parents=True, exist_ok=False)
    inputs = {}
    verified_originals = {}

    def verify_original(path, sha):
        if not path or not sha:
            raise ValueError('Original citation requires path and digest')
        if path in verified_originals:
            if verified_originals[path] != sha:
                raise ValueError('Conflicting digest for ' + path)
            return
        if digest(safe_path(path)) != sha:
            raise ValueError('Original source changed: ' + path)
        verified_originals[path] = sha

    def scan_originals(value):
        if isinstance(value, list):
            for v in value:
                scan_originals(v)
        if not isinstance(value, dict):
            return
        for path_key, sha_key in [('source_path','source_sha256'), ('artifact_path','artifact_sha256'), ('raw_path','raw_sha256'), ('text_path','text_sha256'), ('narrative_path','narrative_sha256')]:
            if bool(value.get(path_key)) != bool(value.get(sha_key)):
                raise ValueError('One-sided original citation: ' + path_key)
            if value.get(path_key) and value.get(sha_key):
                verify_original(value[path_key], value[sha_key])
        for v in value.values():
            if isinstance(v, (dict, list)):
                scan_originals(v)

    def freeze(path, target):
        before = digest(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if before != digest(path) or before != digest(target):
            raise ValueError('Input changed during copy: ' + rel(path))
        inputs[rel(path)] = {'sha256': before, 'snapshot_path': rel(target)}
        return target

    # These immutable observation reports preserve all fields and claims from
    # the prior independently reviewed package without selecting a single truth.
    old_reports = freeze(old / 'reports.jsonl', snap / 'components/base/reports.jsonl')
    raw_observations = list(rows(old_reports))
    observations = []
    obs_ids = set()
    for p in raw_observations:
        oid = p['record_id']
        if oid in obs_ids:
            raise ValueError('Duplicate source observation: ' + oid)
        obs_ids.add(oid)
        scan_originals(p)
        system, system_basis = reported_system(p)
        observations.append({'source_observation_id': oid, 'publisher': 'Trellis' if p['source_class'].startswith('trellis') else 'Official court source',
            'source_class': p['source_class'], 'name': p['name'], 'state_code': p.get('state_code'), 'judge_system': system, 'judge_system_basis':system_basis,
            'courts': p.get('courts') or [], 'counties': p.get('counties') or [],
            'source_url': p['source_url'], 'captured_at': p.get('capture_times'),
            'current_service_verified': False, 'native_record': p,
            'evidence': {'file': rel(old_reports), 'record_id': oid}})

    facts = []
    for group, prefix, id_key, field_key in [('official_profiles','official:','profile_id','category'), ('trellis_profiles','trellis-profile:','capture_id','field')]:
        path = freeze(old / group / 'facts.jsonl', snap / 'components/base' / group / 'facts.jsonl')
        for line, p in enumerate(rows(path), 1):
            oid = prefix + p[id_key]
            # Prefix is resolved against the report itself; no person matching.
            if oid not in obs_ids and group == 'trellis_profiles':
                matching = [o['source_observation_id'] for o in observations if o['source_class']=='trellis_profile' and o['native_record'].get('capture_id') == p[id_key]]
                if len(matching) == 1:
                    oid = matching[0]
            if oid not in obs_ids:
                raise ValueError('Fact has no observation: ' + oid)
            scan_originals(p)
            facts.append({'fact_id': group + ':' + p['fact_id'], 'source_observation_id': oid, 'field': p[field_key], 'value': p.get('value'),
                'claim_scope': 'source_reported', 'native_record': p, 'evidence': {'file': rel(path), 'line': line}})

    components = []
    analyses = []
    context_analyses = []
    for component in ['state_evaluations', 'federal_biographies']:
        folder = BASE / component
        if not (folder / 'observations.jsonl').exists():
            raise ValueError('Required enrichment component missing: ' + component)
        if not (folder / 'validation.json').exists():
            raise ValueError('Component validation missing: ' + component)
        validation = read(folder / 'validation.json')
        if validation.get('validated') is not True:
            raise ValueError('Component has not passed validation: ' + component)
        semantic = [name for name in ['observations.jsonl','facts.jsonl','analyses.jsonl','context_analyses.jsonl'] if (folder / name).exists()]
        bound_outputs(folder, validation, semantic)
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.suffix in {'.json', '.jsonl', '.csv', '.md', '.txt'}:
                freeze(path, snap / 'components' / component / path.relative_to(folder))
        target = snap / 'components' / component
        bound_outputs(target, read(target / 'validation.json'), semantic)
        count = 0
        component_ids = set()
        for line, p in enumerate(rows(target / 'observations.jsonl'), 1):
            oid = p['source_observation_id']
            if oid in obs_ids:
                raise ValueError('Duplicate external observation ID')
            obs_ids.add(oid)
            component_ids.add(oid)
            count += 1
            scan_originals(p)
            external_system = p['judge_system']
            if external_system not in {'state','federal','administrative','unknown'}:
                external_system, _ = reported_system(p)
                if external_system == 'unknown':
                    raise ValueError('Unsupported external judge system')
            observations.append({'source_observation_id': oid, 'publisher': p.get('publisher', {'federal_biographies':'Federal Judicial Center','state_evaluations':'Colorado judicial performance commissions'}[component]), 'source_class': component,
                'name': p['name'], 'state_code': p.get('state_code'), 'judge_system': external_system, 'courts': p.get('courts') or [],
                'counties': p.get('counties') or [], 'source_url': p['source_url'], 'captured_at': p.get('captured_at'),
                'evaluation_cycle': p.get('evaluation_cycle'), 'current_service_verified': False, 'native_record': p,
                'evidence': {'file': rel(target / 'observations.jsonl'), 'line': line}})
        if (target / 'facts.jsonl').exists():
            for line, p in enumerate(rows(target / 'facts.jsonl'), 1):
                scan_originals(p)
                oid = p['source_observation_id']
                if oid not in component_ids:
                    raise ValueError('External fact has no observation')
                field = p.get('field') or p.get('predicate')
                if not field:
                    raise ValueError('External professional fact missing predicate')
                facts.append({'fact_id': component + ':' + p['fact_id'], 'source_observation_id': oid,
                    'field': field, 'value': p.get('value'), 'claim_scope': 'source_reported', 'native_record': p,
                    'evidence': {'file': rel(target / 'facts.jsonl'), 'line': line}})
        if (target / 'analyses.jsonl').exists():
            for line, p in enumerate(rows(target / 'analyses.jsonl'), 1):
                scan_originals(p)
                oid = p['source_observation_id']
                if oid not in component_ids:
                    raise ValueError('Analysis has no observation')
                if p.get('analysis_type') not in {'performance_evaluation', 'survey', 'retention_recommendation', 'performance_survey', 'official_performance_evaluation', 'commission_performance_evaluation', 'commission_vote', 'survey_overall_rating'}:
                    raise ValueError('Unreviewed analysis type: ' + str(p.get('analysis_type')))
                analyses.append({'analysis_id': component + ':' + p['analysis_id'], 'source_observation_id': oid,
                    'analysis_type': p['analysis_type'], 'metric': p['metric'], 'label': p['label'], 'value': p.get('value'),
                    'unit': p.get('unit'), 'numerator': p.get('numerator'), 'denominator': p.get('denominator'),
                    'scale_minimum': p.get('scale_minimum'), 'scale_maximum': p.get('scale_maximum'),
                    'period': p.get('period'), 'cohort': p.get('cohort'), 'methodology_url': p.get('methodology_url'),
                    'interpretation': 'Source-reported official performance evaluation; not a litigation outcome or causal judge-quality score.',
                    'native_record': p, 'evidence': {'file': rel(target / 'analyses.jsonl'), 'line': line}})
        if (target / 'context_analyses.jsonl').exists():
            for line, p in enumerate(rows(target / 'context_analyses.jsonl'), 1):
                if p.get('source_observation_id') is not None or p.get('analysis_type') != 'statewide_cycle_statistics':
                    raise ValueError('Aggregate context must not have a judge observation')
                scan_originals(p)
                context_analyses.append({'analysis_id': component + ':' + p['analysis_id'], 'aggregation_scope':'statewide_cycle',
                    'source_observation_id':None, 'judge_id':None, 'native_record':p,
                    'evidence':{'file':rel(target / 'context_analyses.jsonl'),'line':line}})
        components.append({'component': component, 'observations': count, 'validation': validation})

    # Keep the reviewed base crosswalk separate from external observations. No
    # new name-based link is made here; authoritative external IDs remain intact.
    identity_dir = BASE / 'identity'
    mapping = {}
    if not (identity_dir / 'observation_identity.jsonl').exists():
        raise ValueError('Reviewed identity layer missing')
    iv = read(identity_dir / 'validation.json')
    if iv.get('validated') is not True:
        raise ValueError('Identity validation failed')
    identity_semantic = ['observation_identity.jsonl','judges.jsonl','match_decisions.jsonl','service_timeline.jsonl']
    bound_outputs(identity_dir, iv, identity_semantic)
    identity_summary = read(identity_dir / 'summary.json')
    identity_source = identity_summary['source_snapshot']
    if identity_source['snapshot_path'] != rel(old) or identity_source['manifest_sha256'] != digest(old / 'files.sha256.json'):
        raise ValueError('Identity crosswalk is bound to a different base snapshot')
    for path in sorted(identity_dir.glob('*')):
        if path.is_file() and path.suffix in {'.json','.jsonl','.csv','.md'}:
            freeze(path, snap / 'components/identity' / path.name)
    bound_outputs(snap / 'components/identity', read(snap / 'components/identity/validation.json'), identity_semantic)
    for p in rows(snap / 'components/identity/observation_identity.jsonl'):
        oid = p['source_observation_id']
        if oid in mapping or oid not in obs_ids:
            raise ValueError('Invalid identity crosswalk observation')
        mapping[oid] = p['judge_id']
    if set(mapping) != {p['record_id'] for p in raw_observations}:
        raise ValueError('Base identity map must preserve each base observation exactly once')

    groups = {}
    for o in observations:
        oid = o['source_observation_id']
        native_id = o['native_record'].get('source_record_id') if o['source_class']=='federal_biographies' else None
        jid = mapping.get(oid, ident('source-judge:', native_id or oid))
        o['judge_id'] = jid
        group = groups.setdefault(jid, {'judge_id': jid, 'display_name': o['name'], 'source_observation_ids': [],
            'reported_names': [], 'state_codes': [], 'judge_systems': [], 'courts_as_reported': [], 'counties_as_reported': [],
            'identity_status': 'source_identity_only', 'current_service_verified': False})
        group['source_observation_ids'].append(oid)
        for key, vals in [('reported_names',[o['name']]), ('state_codes',[o.get('state_code')]), ('judge_systems',[o['judge_system']]), ('courts_as_reported',o['courts']), ('counties_as_reported',o['counties'])]:
            for v in vals:
                if v and v not in group[key]:
                    group[key].append(v)
    for g in groups.values():
        if len(g['source_observation_ids']) > 1:
            g['identity_status'] = 'linked_source_identity_see_reviewed_crosswalk'
    observation_judges = {o['source_observation_id']: o['judge_id'] for o in observations}
    fact_ids = set()
    for f in facts + analyses:
        key = f.get('fact_id', f.get('analysis_id'))
        if key in fact_ids:
            raise ValueError('Duplicate claim ID')
        fact_ids.add(key)
        f['judge_id'] = observation_judges[f['source_observation_id']]

    write_rows(snap / 'judges.jsonl', groups.values())
    write_rows(snap / 'source_observations.jsonl', observations)
    write_rows(snap / 'facts.jsonl', facts)
    write_rows(snap / 'analyses.jsonl', analyses)
    write_rows(snap / 'context_analyses.jsonl', context_analyses)
    write_csv(snap / 'facts.csv', facts, ['fact_id','judge_id','source_observation_id','field','value','claim_scope','evidence'])
    write_csv(snap / 'analyses.csv', analyses, ['analysis_id','judge_id','source_observation_id','analysis_type','metric','label','value','unit','scale_minimum','scale_maximum','numerator','denominator','period','cohort','methodology_url','evidence'])
    grouped_facts = {}
    grouped_analysis = {}
    for f in facts:
        grouped_facts.setdefault(f['judge_id'], []).append(f['fact_id'])
    for a in analyses:
        grouped_analysis.setdefault(a['judge_id'], []).append(a['analysis_id'])
    write_rows(snap / 'judge_reports.jsonl', [{**g, 'professional_fact_ids': grouped_facts.get(g['judge_id'], []),
        'analysis_ids': grouped_analysis.get(g['judge_id'], []), 'report_command': 'python scripts/search_judge_enrichment.py --report ' + g['judge_id']} for g in groups.values()])
    with (snap / 'judge_index.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(next(iter(groups.values())).keys()))
        writer.writeheader()
        for g in groups.values():
            writer.writerow({k: compact(v) if isinstance(v, (list,dict)) else v for k,v in g.items()})

    db = sqlite3.connect(snap / 'judge_corpus.sqlite3')
    db.executescript('''CREATE TABLE judges(judge_id TEXT PRIMARY KEY,name TEXT,payload TEXT);
        CREATE TABLE observations(observation_id TEXT PRIMARY KEY,judge_id TEXT,source_class TEXT,judge_system TEXT,state_code TEXT,name TEXT,courts TEXT,counties TEXT,payload TEXT);
        CREATE TABLE facts(fact_id TEXT PRIMARY KEY,judge_id TEXT,observation_id TEXT,field TEXT,value TEXT,payload TEXT);
        CREATE TABLE analyses(analysis_id TEXT PRIMARY KEY,judge_id TEXT,observation_id TEXT,analysis_type TEXT,metric TEXT,value TEXT,unit TEXT,period TEXT,payload TEXT);
        CREATE TABLE context_analyses(analysis_id TEXT PRIMARY KEY,aggregation_scope TEXT,payload TEXT);
        CREATE VIRTUAL TABLE observation_search USING fts5(observation_id UNINDEXED,name,details);
        CREATE INDEX observation_judge_idx ON observations(judge_id);
        CREATE INDEX observation_filters_idx ON observations(source_class,judge_system,state_code);
        CREATE INDEX analysis_judge_idx ON analyses(judge_id);
        CREATE INDEX facts_judge_idx ON facts(judge_id);''')
    db.executemany('INSERT INTO judges VALUES(?,?,?)', [(g['judge_id'],g['display_name'],compact(g)) for g in groups.values()])
    db.executemany('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)', [(o['source_observation_id'],o['judge_id'],o['source_class'],o['judge_system'],o['state_code'],o['name'],compact(o['courts']),compact(o['counties']),compact(o)) for o in observations])
    db.executemany('INSERT INTO observation_search VALUES(?,?,?)', [(o['source_observation_id'],o['name'],flatten(o['native_record'])) for o in observations])
    db.executemany('INSERT INTO facts VALUES(?,?,?,?,?,?)', [(f['fact_id'],f['judge_id'],f['source_observation_id'],f['field'],compact(f['value']),compact(f)) for f in facts])
    db.executemany('INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?)', [(a['analysis_id'],a['judge_id'],a['source_observation_id'],a['analysis_type'],a['metric'],compact(a['value']),a['unit'],compact(a['period']),compact(a)) for a in analyses])
    db.executemany('INSERT INTO context_analyses VALUES(?,?,?)', [(a['analysis_id'],a['aggregation_scope'],compact(a)) for a in context_analyses])
    db.commit()
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('SQLite integrity failure')
    if db.execute('SELECT COUNT(*) FROM analyses a LEFT JOIN observations o ON a.observation_id=o.observation_id WHERE o.observation_id IS NULL').fetchone()[0]:
        raise ValueError('Orphaned analysis')
    db.close()
    if (BASE / 'source_registry.json').exists():
        freeze(BASE / 'source_registry.json', snap / 'source_registry.json')
    freeze(BASE / 'SCHEMA.md', snap / 'SCHEMA.md')
    for path in [Path(__file__), ROOT / 'scripts/search_judge_enrichment.py']:
        if digest(path) != pipeline_digests[path]:
            raise ValueError('Pipeline changed while staging')
        freeze(path, snap / 'pipeline' / path.name)
    freeze(old / 'files.sha256.json', snap / 'components/base/original_manifest.json')
    write(snap / 'component_inputs.json', inputs)
    write(snap / 'verified_originals.json', verified_originals)
    summary = {'built_at_utc': stamp.isoformat(), 'base_snapshot': rel(old), 'source_observations': len(observations),
        'source_observations_by_class': dict(Counter(o['source_class'] for o in observations)),
        'identity_groups': len(groups), 'unique_current_judges': None, 'fact_claims': len(facts), 'analysis_records': len(analyses), 'separate_aggregate_context_records':len(context_analyses),
        'analysis_records_by_type': dict(Counter(a['analysis_type'] for a in analyses)), 'verified_original_artifacts': len(verified_originals),
        'external_analysis_identity_policy': 'External judge observations remain separate until independently matched with compatible court/jurisdiction evidence.',
        'components': components, 'full_national_corpus_complete': False}
    write(snap / 'summary.json', summary)
    write(snap / 'validation.json', {'validated': True, 'sealed_base_manifest_verified': True, 'original_artifacts_verified': len(verified_originals),
        'input_copy_digests_verified': True, 'claim_ids_unique': True, 'analysis_foreign_keys_valid': True, 'sqlite_integrity': 'ok',
        'no_new_external_name_only_identity_merges': True, 'no_independent_outcome_statistics_computed': True})
    readme = '''# Standardized judge corpus

This immutable snapshot combines the previously reviewed judge corpus with authoritative public professional biographies and official judge-performance evaluations. See summary.json for exact counts and acquisition dates.

judges.jsonl and judge_index.csv contain conservative identity groups, not a verified census of unique current judges. source_observations.jsonl preserves each publisher's record, dates, court labels and native payload. facts.jsonl retains professional facts as evidence-linked claims. analyses.jsonl retains source-reported survey/evaluation measurements with their cycle, unit, available denominator, cohort and methodology. Unknown contexts remain null. Source conflicts and historical information are retained without silently choosing one value.

The observations table and FTS index in judge_corpus.sqlite3 support name, professional text, state, source, court and judge-system filtering. Analyses and facts link through observation and judge IDs. Use scripts/search_judge_enrichment.py for search and full evidence-linked reports.

An official retention/performance evaluation is not a motion-grant rate, litigation win rate or causal quality score. Comparisons require compatible evaluation programs, cycle, respondent cohort and metric definition. Federal biographies do not supply litigation outcome statistics. Court-level values are not assigned to individual judges. Historical marketing reports remain separate from actual judge analysis. External observations stay separate until a court/jurisdiction identity match is independently verified.

components/ preserves frozen inputs and identity decisions; component_inputs.json and verified_originals.json record their SHA-256 evidence. validation.json documents structural checks, while individual source components document extraction checks and gaps. Full nationwide collection remains incomplete.
'''
    (snap / 'README.md').write_text(readme, encoding='utf-8')
    manifest = {rel(p): {'sha256': digest(p), 'bytes': p.stat().st_size} for p in sorted(snap.rglob('*')) if p.is_file()}
    write(snap / 'files.sha256.json', manifest)
    for name, item in manifest.items():
        if digest(safe_path(name)) != item['sha256']:
            raise ValueError('Snapshot changed before publication')
    print(json.dumps({'staged_snapshot': rel(snap), **{k:summary[k] for k in ['source_observations','identity_groups','fact_claims','analysis_records','verified_original_artifacts']}}))

if __name__ == '__main__':
    main()
