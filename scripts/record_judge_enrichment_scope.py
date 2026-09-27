"""Record newly authorized professional judge enrichment, preserving live collection."""
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
scope_path = ROOT / 'reports/judges/focus_20260913/scope.json'
scope = json.loads(scope_path.read_text(encoding='utf-8-sig'))
scope.update(updated_at=stamp, latest_user_direction='Collect reputable alternative professional judge data and analysis; standardize and enrich the judge corpus accurately',
    enrichment_output='delivery/judge_enrichment_20260914',
    enrichment_sources='sources/judges/enrichment_20260914',
    identity_policy='Retain every source observation; confirmed links require exact name and explicit compatible jurisdiction/court evidence. Ambiguities remain separate.',
    analytics_policy='Separate official performance evaluations, publisher litigation outcome metrics, and illustrative samples. Preserve original labels, evaluation cycles, units, denominators, cohorts, methodology and evidence. Unknown contexts remain null.',
    enrichment_target='Finite public official evaluation cycle and public authoritative federal professional biographies; preserve existing finite Trellis selector without duplication.')
scope['scope'] += [s for s in ['public official judge performance evaluations and methodology', 'public authoritative federal professional biographies', 'conservative judge identity crosswalk and standardized evidence-linked analysis records'] if s not in scope['scope']]
scope_path.write_text(json.dumps(scope, indent=2) + '\n', encoding='utf-8')
prefix = '''# Active priority — standardized judge enrichment

The latest user request authorizes acquiring reputable alternative judge analysis and professional data, adding it to the existing corpus and standardizing judge records accurately. Current scope is `reports/judges/focus_20260913/scope.json`. New public sources and outputs are isolated under `sources/judges/enrichment_20260914/` and `delivery/judge_enrichment_20260914/`.

Collect a finite official judicial-performance evaluation cycle with its methodology and a public authoritative federal professional-biography dataset where accessible. Keep state, federal and administrative judge systems explicit. Official retention/survey evaluations are different measurements from motion outcomes; do not combine their percentages or invent missing denominators. Preserve source labels, dates, original artifacts, SHA-256 hashes and evidence for each fact. Retain individual source observations even after a confirmed identity link. Name-only and ambiguous matches remain unresolved; current service is not inferred from a historical profile.

The existing finite Trellis profile selector continues unchanged. Inspect fresh worker status, process/OS locks and remote job markers before any action; recover the same job instead of duplicating submission. Do not restart cases, filings, law/county collectors or broad URL discovery. The existing sealed package remains immutable. Commercial vendor documentation is useful for planning imports; no additional account purchase or product upgrade is authorized. Preserve the historical sample-report partition. The notes below are dated history when they conflict with this priority.

---

'''
for filename in ['REQUEST.md', 'RUNBOOK.md']:
    path = ROOT / filename
    old = path.read_text(encoding='utf-8-sig')
    if old.startswith('# Active priority — standardized judge enrichment'):
        old = old.split('\n---\n', 1)[1].lstrip('\n')
    path.write_text(prefix + old, encoding='utf-8')
print(json.dumps({'scope': str(scope_path.relative_to(ROOT)), 'current_selector_preserved': scope['current_selection']}))
