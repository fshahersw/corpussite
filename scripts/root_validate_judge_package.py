"""Root's fresh file/evidence checks after the independent semantic review."""
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_judge_intelligence_package import AUDIT, BASE, component_files, read, rows, sha, write, rel

def main():
    audit = read(AUDIT / 'official_semantic_audit.json')
    code_review = read(AUDIT / 'package_code_review.json')
    assert not code_review['remaining_material_findings']
    for filename in ['scripts/build_judge_intelligence_package.py', 'scripts/search_judge_intelligence.py', 'delivery/judge_intelligence_20260913/trellis_profiles/normalize.py']:
        assert sha(ROOT / filename) == code_review['reviewed_files_sha256'][filename], 'Publication code changed after review'
    assert audit['status'] == 'sample_pass_after_resolved_findings'
    assert audit['unresolved_findings_in_reviewed_scope'] == 0
    official = BASE / 'official_profiles'
    for name in ['profiles.jsonl', 'facts.jsonl', 'summary.json', 'semantic_regressions.json']:
        path = official / name
        receipt = next(r for r in audit['final_input_receipts'] if r['path'] == rel(path))
        assert sha(path) == receipt['sha256'], 'Official dataset changed after semantic QA'
    ov = read(official / 'validation_independent.json')
    assert ov['issues'] == 0 and ov['profiles_sha256'] == sha(official / 'profiles.jsonl') and ov['facts_sha256'] == sha(official / 'facts.jsonl')
    normalizer = BASE / 'trellis_profiles/normalize.py'
    spec = importlib.util.spec_from_file_location('reviewed_profile_normalizer', normalizer)
    n = importlib.util.module_from_spec(spec); spec.loader.exec_module(n)
    profiles = list(rows(BASE / 'trellis_profiles/profiles.jsonl'))
    facts = list(rows(BASE / 'trellis_profiles/facts.jsonl'))
    by_id = {p['capture_id']: p for p in profiles}
    assert len(by_id) == len(profiles)
    trellis_audit = read(AUDIT / 'trellis_semantic_audit.json')
    sampled_fields = ['reported_name', 'reported_state', 'reported_court', 'reported_county', 'reported_county_basis']
    for sampled in trellis_audit['sample']:
        current = by_id[sampled['capture_id']]
        for field in sampled_fields:
            assert current[field] == sampled[field], 'Sampled Trellis attribution changed after review'
    assert len({f['fact_id'] for f in facts}) == len(facts)
    assert read(BASE / 'trellis_profiles/summary.json')['fact_records'] == len(facts)
    originals = {}
    decoded = {}
    for record in rows(BASE / 'trellis_profiles/input_manifest.jsonl'):
        path = record['path']; raw = (ROOT / path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        originals[path] = record['sha256']
        obj = json.loads(raw); decoded[path] = obj.get('data', obj)
    checks = 0
    for fact in facts:
        p = by_id[fact['capture_id']]
        ev = fact['evidence']; path = fact['source_path']
        assert p['source_path'] == path and p['source_sha256'] == fact['source_sha256'] == originals[path]
        html = decoded[path].get('html') or ''
        fragment = html[ev['html_character_start']:ev['html_character_end']]
        assert hashlib.sha256(fragment.encode()).hexdigest() == ev['html_fragment_sha256']
        assert n.plain(fragment) == ev['literal_text']
        assert hashlib.sha256(ev['literal_text'].encode()).hexdigest() == ev['literal_text_sha256']
        assert p['source_url'] == fact['source_url']
        checks += 1
    assert all(p['independently_computed_statistics'] is None and p['current_service_verified'] is False for p in profiles)
    sample = ROOT / 'sources/judges/focus_20260913/report_sample'
    capture = read(sample / 'capture.json')
    assert sha(sample / 'trellis_judge_report_sample.pdf') == capture['raw_sha256']
    # The original capture recorded the UTF-8 digest before Windows newline
    # translation. Preserve the file and record both actual and decoded bases.
    sample_text = sample / 'trellis_judge_report_sample.txt'
    assert hashlib.sha256(sample_text.read_text(encoding='utf-8').encode()).hexdigest() == capture['text_sha256']
    approval = {'reviewed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'approved': True,
        'review_kind': 'Internal dataset validation, not a request for user permission',
        'official_profiles_sha256': sha(official / 'profiles.jsonl'),
        'semantic_audit_sha256': sha(AUDIT / 'official_semantic_audit.json'),
        'package_code_review_sha256': sha(AUDIT / 'package_code_review.json'),
        'trellis_semantic_audit_sha256': sha(AUDIT / 'trellis_semantic_audit.json'),
        'trellis_semantic_sample_attribution_fields_rechecked': len(trellis_audit['sample']),
        'sample_original_text_file_sha256': sha(sample_text),
        'sample_capture_text_digest_basis': 'UTF-8 decoded text before Windows newline translation; actual file hash retained separately',
        'component_sha256': {rel(path): sha(path) for _, path in component_files()},
        'trellis_profile_rows': len(profiles), 'trellis_fact_rows': len(facts),
        'trellis_fact_evidence_slices_reobserved': checks, 'trellis_originals_rehashed': len(originals),
        'official_export_validator_rechecked': True, 'source_mutations': 0,
        'semantic_limit': 'Official40 plus Trellis20 purposive samples; not national or exhaustive semantic certification',
        'numeric_analysis_limit': 'Publisher sample report kept separate; no unsupported independent outcome statistics'}
    write(AUDIT / 'root_package_approval.json', approval)
    print(json.dumps({key: value for key, value in approval.items() if key != 'component_sha256'}))

if __name__ == '__main__':
    if not __debug__:
        raise RuntimeError('Root validation must run with assertions enabled')
    main()
