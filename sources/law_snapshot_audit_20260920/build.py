"""Publisher's completeness audit of the saved statute snapshot, per jurisdiction (2026-09-20).

Source: coverage.yml in the Open US Law repository (Apache-2.0 code, CC BY 4.0 data), pinned in
sources/upstream_open_data_20260920. It is the publisher's own statement of what each state's statutes contain: a verdict
(complete / partial / withdrawn), the number of top-level containers, the official source it was built from, the citation scheme
and notes such as "unconsolidated statutes missing" or "withdrawn: section bodies carried site navigation".

This build copies those statements, one row per jurisdiction, so the State law page can say plainly what is and is not in the
saved code. Nothing is re-measured here except the saved row count, which is read from the law catalog's own file table.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UPSTREAM = ROOT / 'sources/upstream_open_data_20260920/repos/Vaquill-AI__open-us-law'
CATALOG = ROOT / 'sources/open_us_law_20260918/catalog.sqlite3'
OUT = HERE / 'statute_audit.json'
VERDICTS = {'complete': 'Complete (publisher audit)', 'partial': 'Partial (publisher audit)', 'withdrawn': 'Withdrawn from this snapshot by the publisher', 'thin': 'Thin (publisher audit)',
            'broken': 'Known defects (publisher audit)'}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plain(text):
    return ' '.join(str(text or '').replace('Vaquill', 'the publisher').split())


def main():
    manifest = json.loads((UPSTREAM / 'manifest.json').read_text(encoding='utf-8'))
    source = UPSTREAM / 'files/coverage.yml'
    if sha256(source) != manifest['files']['coverage.yml']['sha256']:
        raise SystemExit('coverage.yml changed since it was fetched')
    audit = yaml.safe_load(source.read_text(encoding='utf-8'))
    catalog = sqlite3.connect(CATALOG.as_uri() + '?mode=ro', uri=True)
    saved = {row[0]: row[1] for row in catalog.execute("SELECT state, sum(rows) FROM files WHERE kind='statutes' GROUP BY state")}
    catalog.close()
    rows = {}
    for item in audit['jurisdictions']:
        usps = 'FEDERAL' if item['code'] == 'federal' else str(item['code']).upper()
        rows[usps] = {'usps': usps, 'name': item.get('name'), 'verdict': item.get('coverage_status'), 'verdict_label': VERDICTS.get(item.get('coverage_status'), str(item.get('coverage_status'))),
                      'verified_by_publisher': bool(item.get('coverage_verified')), 'containers': plain(item.get('containers')), 'official_source': item.get('official_source') or '',
                      'citation_scheme': plain(item.get('citation_scheme')), 'notes': plain(item.get('notes')), 'publisher_section_count': item.get('section_count'),
                      'saved_statute_rows': saved.get(usps, 0)}
    checks = {'every_audited_jurisdiction_is_a_row': len(rows) == len(audit['jurisdictions']),
              'withdrawn_states_have_no_saved_statutes': all(rows[k]['saved_statute_rows'] == 0 for k in rows if rows[k]['verdict'] == 'withdrawn'),
              'every_saved_state_is_in_the_audit': all(state in rows for state in saved)}
    OUT.write_text(json.dumps({'snapshot': 'v2026.08', 'audit_reconciled': '2026-07-22', 'jurisdictions': rows}, indent=1, ensure_ascii=False), encoding='utf-8')
    validation = {'schema_version': 1, 'status': 'passed' if all(checks.values()) else 'failed', 'ready': all(checks.values()), 'validated_at': datetime.now(timezone.utc).isoformat(),
                  'data_files': [{'path': OUT.name, 'sha256': sha256(OUT), 'rows': len(rows)}],
                  'counts': {'jurisdictions': len(rows), 'complete': sum(1 for r in rows.values() if r['verdict'] == 'complete'), 'partial': sum(1 for r in rows.values() if r['verdict'] == 'partial'),
                             'withdrawn': sum(1 for r in rows.values() if r['verdict'] == 'withdrawn')},
                  'checks': checks, 'qualification': 'The publisher\'s own completeness statements for its statute snapshot, reconciled by the publisher on 2026-07-22; not re-audited here.',
                  'license_ref': 'open_us_law_cc_by_4_0_compilation', 'inputs': [{'repository': 'Vaquill-AI/open-us-law', 'commit': manifest['commit'], 'path': 'coverage.yml', 'sha256': sha256(source)}]}
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': validation['counts'], 'checks': checks}, indent=1))


if __name__ == '__main__':
    main()
