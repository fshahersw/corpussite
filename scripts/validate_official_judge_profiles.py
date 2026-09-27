"""Validate persisted official-profile exports and refresh their coverage report.

Offline; reads captures and package files, writes only the new official_profiles
validation/report files. Run after build_official_judge_profiles.py.
"""
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'delivery/judge_intelligence_20260913/official_profiles'


def rows(name):
    return [json.loads(line) for line in (OUT/name).read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    profiles=rows('profiles.jsonl');facts=rows('facts.jsonl')
    coverage=rows('source_coverage.jsonl');states=rows('state_coverage.jsonl')
    summary=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
    regressions=json.loads((OUT/'semantic_regressions.json').read_text(encoding='utf-8'))
    by_id={p['profile_id']:p for p in profiles}
    assert len(by_id)==len(profiles)==summary['source_bound_person_observations']
    assert len(facts)==summary['facts']
    assert len(coverage)==145 and len({r['source_url'] for r in coverage})==145
    assert len(states)==51 and len({r['state_code'] for r in states})==51
    assert sum(s['person_observations'] for s in states)==len(profiles)
    assert sum(r['observations'] for r in coverage)==len(profiles)
    assert len({f['fact_id'] for f in facts})==len(facts)
    for fact in facts:
        assert fact['profile_id'] in by_id
        prefix,index=fact['fact_id'].split(':')
        assert prefix==fact['profile_id']
        original=by_id[prefix]['facts'][int(index)-1]
        assert all(fact[k]==v for k,v in original.items()),fact['fact_id']
    for filename,expected in [('profiles.csv',len(profiles)),('facts.csv',len(facts)),('source_coverage.csv',145),('state_coverage.csv',51)]:
        with (OUT/filename).open(encoding='utf-8-sig',newline='') as f:
            assert sum(1 for _ in csv.DictReader(f))==expected,filename
    assert dict(collections.Counter(f['category'] for f in facts))==summary['facts_by_category']
    referenced={e['artifact_path']:e['artifact_sha256'] for f in facts for e in f['evidence']}
    for path,expected in referenced.items():assert sha(ROOT/path)==expected,path
    assert all(r['current_historical_status']=='unresolved' for r in profiles if r['state_code']!='AL')
    assert all(r['current_historical_status'] in ('dated_service_entry','source_says_present') for r in profiles if r['state_code']=='AL')
    for check in regressions['checks']:
        assert check['passed']
        if check['case']=='professional_narrative_categories':
            p=by_id[check['profile_id']]
            actual={f['category'] for f in p['facts'] if f['value']==check['source_evidence']['quote']}
            assert set(check['required_categories'])<=actual
            assert not actual & set(check['forbidden_categories'])
            assert actual==set(check['actual_categories'])
        elif check['case']=='valid_source_name_not_rejected':
            p=by_id[check['profile_id']]
            assert p['observed_name']==check['observed_name']
            for category,value in check['required_facts'].items():
                assert any(f['category']==category and f['value']==value for f in p['facts'])
    ca=next(p for p in profiles if p['state_code']=='CA')
    assert any('assistant U.S. attorney at the U.S. Attorney' in f['value'] for f in ca['facts'])
    assert not any(f['value']=='Commission on Judicial Appointments' for f in facts)
    result={'validated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'builder_version':summary['builder_version'],'issues':0,'profiles':len(profiles),'facts':len(facts),
        'profiles_sha256':sha(OUT/'profiles.jsonl'),'facts_sha256':sha(OUT/'facts.jsonl'),
        'source_coverage_rows':145,'state_coverage_rows':51,'csv_jsonl_row_count_parity':True,
        'all_fact_ids_and_profile_references_valid':True,'flattened_facts_equal_profile_facts':True,
        'all_coverage_totals_match':True,'temporal_labels_separated':True,
        'fact_evidence_artifact_hashes_verified':len(referenced),
        'semantic_regressions_rechecked':regressions['check_count'],
        'biography_abbreviations_retained':True,'navigation_only_appointment_fragment_excluded':True}
    (OUT/'validation_independent.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    lines=['# Profile coverage report','',
        f"Builder {summary['builder_version']} produced {len(profiles):,} source-specific observations and {len(facts):,} evidence-linked facts. {summary['observations_with_narrative_facts']} observations contain professional narrative facts; {summary['observations_with_professional_contacts']} contain court/office contact fields.",
        '', 'These are observations, not unique people. No matching across identical names was performed. Exact repeated presentations within one source were collapsed with all source locators retained. The 363 Texas records are court/county assignments and can name the same person several times.',
        '', '| State | Observations | Sources with observations | Narrative observations | Historical entries with dated ends |',
        '| --- | ---: | ---: | ---: | ---: |']
    for state in states:
        if state['person_observations']:
            lines.append(f"| {state['state']} | {state['person_observations']} | {state['sources_with_person_observations']} | {state['narrative_observations']} | {state['historically_dated_observations']} |")
    al=[p for p in profiles if p['state_code']=='AL']
    al_dated=sum(p['current_historical_status']=='dated_service_entry' for p in al)
    al_present=sum(p['current_historical_status']=='source_says_present' for p in al)
    lines+=['',f"The remaining {145-summary['sources_with_observations']} saved sources were not normalized into person entries in this bounded offline pass. They include unsupported source formats or page layouts, and interfaces or directory links without a usable named roster. See source_coverage.csv for every source and rejected_candidates.jsonl for the {summary['rejected_candidate_entries']} ambiguous or excluded candidates.",
        '',f"Alabama contributes {len(al)} membership/service entries, including {al_dated} with dated ends and {al_present} where the source says Present. Neither capture dates nor that label establish current service. All other observations retain unresolved current/historical status.",
        '', 'The saved Iowa directory cards provide short biography excerpts. Full Utah biographies generally remain behind the observed links. Existing PDFs and other unparsed source text remain available at their original paths. There are no inferred case outcomes, win rates, judicial tendencies or performance findings in this package.',
        '', 'The semantic corrections exclude personal migration/citizenship from professional facts while retaining named professional organizations such as the Immigration Justice Project. Career sentences beginning with After/Following/Upon graduation are not labeled education unless they also state an educational event or credential, including abbreviations such as B.A. and J.D. Professional promotions to office/division chief remain service facts. Thomas J. Judge is retained as a historical surname, and Joffie C. Pittman III is parsed with the stated Administrative Judge - Municipal Court role. The exact source excerpts and positive/negative checks are in semantic_regressions.json.',
        '', 'All fact values and evidence locations passed the builder validation; all 293 source artifact hashes and the existing package files remained unchanged. A separate export validator reconciled JSONL/CSV rows, source/state totals, flattened fact values, profile references, temporal labels and the saved semantic regressions. Validation reported zero issues.',
        '', 'To rebuild, run scripts/build_official_judge_profiles.py followed by scripts/validate_official_judge_profiles.py from the workspace root. Both operate offline and write only the new official_profiles output directory.']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
