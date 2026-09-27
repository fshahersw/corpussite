"""Record the latest judge-only collection direction without altering captures."""
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'reports/judges/focus_20260913/scope.json'
stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
scope = {
    'updated_at': stamp,
    'status': 'judge_focus_active',
    'latest_user_direction': 'Fast judge data, profiles, analysis and reports with the newly supplied Firecrawl account',
    'credential_path': '.auth/firecrawl_judge.dpapi',
    'credential_storage': 'Windows user-bound DPAPI; never print or copy the key into reports',
    'credit_preflight': 'reports/judges/focus_20260913/credit_preflight.json',
    'initial_verified_credits': 1400,
    'credit_observation_policy': 'Initial balance is dated; use fresh preflight and cumulative job accounting for current balance',
    'scope': ['exact observed Trellis judge profiles and selected judge directories', 'published judge analytics/report pages accessible through authorized browser access', 'normalize saved official judge rosters and biographies'],
    'excluded': ['bulk cases, dockets, filings, motions or case documents', 'general public URL crawl', 'new law/county collection during judge focus', 'invented metrics or inferred cross-source identities', 'new credit purchases or credential disclosure'],
    'acquisition_base': 'sources/trellis/judge_focus_20260913',
    'worker_state': 'sources/trellis/judge_focus_20260913/worker',
    'current_selection': 'sources/judges/focus_20260913/selection_first300.jsonl',
    'current_selection_url_order_sha256': '6f423d7c9243f78730df2eaf05bfd53d9f28973cac644a7d3e7d37cb29cc18fe',
    'selection_validation': 'sources/judges/focus_20260913/root_review.json',
    'next_staged_selection': 'sources/judges/focus_20260913/selection_next1000.jsonl',
    'next_selection_policy': 'Root review and fresh dedup required; never switch while an unresolved provider job exists',
    'verified_profile_candidates': 10866,
    'baseline_pending_profile_urls': 11099,
    'baseline_downloaded_judge_directories': 46,
    'current_batch_limits': {'seconds': 900, 'batch_size': 50, 'max_concurrency': 2, 'credit_reserve': 100, 'cache_max_age_ms': 86400000},
    'transport_policy': 'Recover the existing provider job with its identical selector and job ID; do not resubmit uncertain work',
    'lock_policy': 'Hold both historical global worker.lock and new isolated worker.lock; original frontier remains untouched',
    'paid_analytics_browser': {'status': 'signed_in_analytics_not_in_current_plan', 'observed_dashboard_url': 'https://trellis.law/judge-dashboard/10848/at-a-glance', 'additional_subscription_as_shown': '80USD/month', 'upgrade_authorized': False, 'login_question_resolved_by_actual_browser_state': True, 'credentials_to_firecrawl': False, 'preview_evidence': 'sources/judges/focus_20260913/report_sample/preview_visible_dom.json'},
    'output': 'delivery/judge_intelligence_20260913',
    'official_normalization': 'delivery/judge_intelligence_20260913/official_profiles',
    'trellis_normalization': 'delivery/judge_intelligence_20260913/trellis_profiles',
    'semantic_audit': 'reports/judges/focus_20260913/official_semantic_audit.json',
    'analytics_policy': 'Only literal source-reported values; preserve label, time period, denominator, publication and capture context. Otherwise null with access/evidence gap.',
    'identity_policy': 'Source-specific observations; no claim that roster/profile totals are unique current judges',
    'finite_collection_target': 'First300 quick package, then reviewed next profiles within available credits while preserving 100-credit reserve',
    'prior_law_county_scope': 'reports/remaining_resume_20260913/scope.json',
    'prior_collectors': 'Already running finite official law jobs may drain naturally; do not restart while judge focus is active',
    'prior_legal_snapshot': 'delivery/focused_legal_corpus/summary.json',
    'automation_id': 'continue-official-legal-corpus',
    'automation_status': 'ACTIVE judge-focus heartbeat every30minutes',
    'focused_package_validated': False,
    'full_underlying_corpus_complete': False,
}
runtime_path = ROOT / 'sources/trellis/judge_focus_20260913/worker/active_run.json'
if runtime_path.exists():
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    active_selector = Path(runtime['selection_file']).resolve().relative_to(ROOT).as_posix()
    scope.update(current_selection=active_selector,
        current_selection_url_order_sha256=runtime['selection_order_sha256'],
        current_selection_size=runtime['selected_urls'],
        current_acquisition_runtime=runtime,
        current_batch_limits={'seconds': runtime['seconds_budget'], 'batch_size': runtime['batch_size'], 'max_concurrency': 2, 'credit_reserve': runtime['credit_reserve'], 'cache_max_age_ms': 86400000})
    if active_selector.endswith('root_selection_next.jsonl'):
        scope['selection_validation'] = 'sources/judges/focus_20260913/root_review_next.json'
        scope['finite_collection_target'] = 'First300 completed; collect999 additional reviewed profiles with100credit reserve, then publish final credit-bounded snapshot and explicit national/analytics gaps'
        scope['no_new_selection_after_current_target_without_user_direction'] = True
latest = ROOT / 'delivery/judge_intelligence_20260913/latest.json'
if latest.exists():
    scope['latest_package'] = json.loads(latest.read_text(encoding='utf-8'))
    scope['focused_package_validated'] = True
    scope['first300_selection_status'] = 'all300selected_urls_saved; pilot adds301st distinct profile'
(ROOT / TARGET).parent.mkdir(parents=True, exist_ok=True)
(ROOT / TARGET).write_text(json.dumps(scope, indent=2) + '\n', encoding='utf-8')
old = ROOT / 'reports/remaining_resume_20260913/scope.json'
history = json.loads(old.read_text(encoding='utf-8-sig'))
history.update(status='paused_for_judge_focus', superseded_at=stamp, superseded_by=TARGET,
    latest_user_direction='Judge focus using the newly authorized Firecrawl account',
    automation='same heartbeat now follows judge focus; no law/county restart',
    collector_restart_authorized_during_judge_focus=False)
old.write_text(json.dumps(history, indent=2) + '\n', encoding='utf-8')
book = ROOT / 'RUNBOOK.md'
prefix = '''# Active priority — fast judge profiles and reports

The latest user instruction authorizes the privately configured Firecrawl account for judge data. Current scope is `reports/judges/focus_20260913/scope.json`. Judge collection uses the isolated `sources/trellis/judge_focus_20260913/` frontier and holds both worker locks. Source selections are frozen and replayed against literal saved links; do not crawl cases, dockets, filings or general URLs.

Recover an unresolved remote batch using its identical selection and job ID before any new submission. Authentication, credit and access failures are terminal until their actual condition changes. Inspect fresh process/OS-lock evidence and the job manifest; a historical run row alone does not prove active collection. Use basic proxy, verified TLS, concurrency two, a finite budget and a 100-credit reserve. Never print the key or send browser cookies to Firecrawl.

The first 300 profiles and the next 1,000 are staged under `sources/judges/focus_20260913/`. Validate and deduplicate the next selector again before launch. Normalize newly saved originals and existing official rosters into `delivery/judge_intelligence_20260913/`; retain raw hashes, literal field evidence, historical uncertainty and source-specific identities. Numeric analytics require actual source values, labels and period/denominator context. The browser is now signed in: the current plan requires an additional80USD/month subscription for full Judge Analytics. No upgrade is authorized. Public preview evidence and the separate12page marketing sample PDF are under `sources/judges/focus_20260913/report_sample/`; no sample values are promoted to actual judge metrics.

Existing finite official law collectors may finish naturally. The prior law/county direction below is dated history and is superseded during judge focus; do not restart those collectors or use their earlier progress percentages for judges. The prior legal package remains its dated snapshot.

---

'''
text = book.read_text(encoding='utf-8-sig')
if text.startswith('# Active priority — fast judge profiles and reports'):
    text = text.split('\n---\n', 1)[1].lstrip('\n')
book.write_text(prefix + text, encoding='utf-8')
print(json.dumps({'scope_updated': TARGET, 'prior_scope_paused': True, 'runbook_current': True}))
