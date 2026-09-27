"""Record validated delivery counts and keep national/access gaps explicit."""
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + '\n', encoding='utf-8')

latest = read(ROOT / 'delivery/judge_enrichment_20260914/latest.json')
snapshot = (ROOT / latest['snapshot_path']).resolve()
snapshot.relative_to(ROOT / 'delivery/judge_enrichment_20260914/snapshots')
if sha(snapshot / 'files.sha256.json') != latest['manifest_sha256']:
    raise ValueError('Delivered snapshot manifest changed')
summary = read(snapshot / 'summary.json')
scope_path = ROOT / 'reports/judges/focus_20260913/scope.json'
scope = read(scope_path)
stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
runtime = read(ROOT / 'sources/trellis/judge_focus_20260913/worker/active_run.json')
scope.update(updated_at=stamp, latest_enrichment_package=latest,
    current_acquisition_runtime=runtime,
    enrichment_acquisition_status='Public FJC bulk and complete Colorado2024 ballot cycle acquired and validated; no further source collection in this finite target',
    enrichment_delivery_counts={k: summary[k] for k in ['source_observations','identity_groups','fact_claims','analysis_records','separate_aggregate_context_records']},
    full_underlying_corpus_complete=False,
    standardized_package_current_base=summary['base_snapshot'])
write(scope_path, scope)
checkpoint = {'recorded_at_utc':stamp, 'latest':latest, 'summary':summary,
    'remaining_gaps':['National judge population and all profiles are not complete.',
        'Official bios/rosters are available only in collected source coverage.',
        'Colorado performance analysis covers116 judges on the2024 ballot, not all evaluated officers or all years/states.',
        'New external source identities remain separate until court/jurisdiction matches are independently reviewed.',
        'No full commercial litigation analytics feed acquired; full exports/storage require applicable access.',
        'No independent win/motion/timing statistics computed.'],
    'search_smoke_tests':['Roff/CO/analysis filter', 'Abelson/federal/FJC source filter','Evidence-linked Roff report UTF8 export'],
    'encoding_note':'For redirected CLI JSON on Windows use python -X utf8 scripts/search_judge_enrichment.py ...',
    'current_trellis_selector':runtime['selection_file'], 'current_trellis_selection_status':runtime['status']}
write(ROOT / 'reports/judges/enrichment_20260914/delivery_checkpoint.json', checkpoint)
print(json.dumps({'checkpoint_recorded':True, 'source_observations':summary['source_observations'], 'trellis_selector_status':runtime['status']}))
