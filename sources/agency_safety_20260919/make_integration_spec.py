"""Write integration_spec.json with real example responses taken from the adapter over the built supplement.

    python make_integration_spec.py        (run after build.py; offline)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
import agency_safety as safety  # noqa: E402


def trim(value, depth=0):
    """Shorten long strings and long lists so the spec stays readable; shapes are unchanged."""
    if isinstance(value, str):
        return value if len(value) <= 220 else value[:200] + '... [%d chars]' % len(value)
    if isinstance(value, list):
        return [trim(v, depth + 1) for v in value[:3]] + (['... %d more' % (len(value) - 3)] if len(value) > 3 else [])
    if isinstance(value, dict):
        return {k: trim(v, depth + 1) for k, v in value.items()}
    return value


def main():
    if not safety.status()['available']:
        raise SystemExit('supplement not available: ' + str(safety.status()))
    datasets = safety.datasets()
    enforcement = safety.search(dataset='enforcement', classification='Class I', date_type='recall_initiation_date', dfrom='2024-01-01', dto='2024-12-31', limit=2)
    first = enforcement['results'][0] if enforcement['results'] else None
    record = safety.record(first['dataset'], first['native_id']) if first else None
    application = safety.search(dataset='openfda_drugsfda', q='aripiprazole', limit=1)
    application_record = safety.record('openfda_drugsfda', application['results'][0]['native_id']) if application['results'] else None
    firm_name = first['firm'] if first else 'Pfizer'
    firm = safety.firm(firm_name, limit=2)
    cfr = safety.for_cfr('21 CFR 870.3610', limit=2)
    letters = safety.search(dataset='fda_warning_letters', date_type='letter_issue_date', dfrom='2025-01', dto='2025-03', limit=1)
    cpsc = safety.search(dataset='cpsc_recalls_local', q='crib', limit=1)
    spec = {
        'schema_version': '1',
        'supplement': 'sources/agency_safety_20260919',
        'adapter': 'delivery/archive-directory/agency_safety.py',
        'adapter_test': 'delivery/archive-directory/test_agency_safety.py',
        'gate': 'validation.json status == "passed" and ready == true and every data_files sha256 (sqlite, firm_names.jsonl, edges.jsonl, unresolved.jsonl, files.json); '
                're-verified whenever validation.json or any gated file changes (size/mtime), and originals are re-hashed on every serve.',
        'functions': {
            'status(folder=None)': 'availability report: {available, reason, validated_at, datasets, records, qualification, license_ref, deviations, counts}',
            'datasets(folder=None)': 'list of 12 dataset dicts (label, publisher, group, endpoint, id_prefix, rows, captured_at(+basis), source_as_of(+basis), '
                                     'published_at_basis, publisher_last_updated, date_types, qualification, original_files[{file_id, filename, sha256, bytes, mime, '
                                     'publisher_url, captured_at, url}]); [] when the gate is closed',
            'search(dataset=None, q=None, firm=None, classification=None, status=None, product_code=None, date_type=None, dfrom=None, dto=None, page=1, limit=25, folder=None)':
                '{available, total, page, limit (<=100), pages, results[summary], filters}. dataset = dataset id or group (enforcement, applications, devices, supply, '
                'warning_letters, press_recalls, cpsc). q = full text, every word a prefix, malformed input returns 0 rows. firm = case-insensitive substring of the '
                'normalised firm string. classification/status = case-insensitive exact. product_code exact. date_type + dfrom/dto = inclusive range on that '
                'publisher date (YYYY, YYYY-MM or YYYY-MM-DD); without date_type the range applies to sort_date; unknown date_type matches nothing.',
            'record(dataset, id, folder=None)': 'summary + fields (normalised table row) + publisher_record (verbatim) + dates (one key per publisher date type) + '
                                                'temporal block + links {firm_id, cfr_id} + original_file; products/submissions for openfda_drugsfda; None when absent. '
                                                'id may be the native id or the full prefixed id.',
            'firm(name, folder=None, page=1, limit=25)': 'exact normalised-string match: {firm_norm, firm_id, names[{name,count}], datasets{dataset:count}, total, results[summary]}. '
                                                         'No entity resolution: "ACME CORP" and "ACME CORPORATION" are different firms.',
            'for_cfr(citation, folder=None, page=1, limit=100)': 'device product codes the publisher classifies under a 21 CFR section or part: accepts cfr:21:870.3610, '
                                                                 '21 CFR 870.3610, 870.3610, cfr:21:870; {citation, part, section, total, classifications[], product_codes, related_pma_rows}',
            'original(file_id, folder=None)': '(bytes, mime, filename) of a registered original re-hashed at serve time, else None. The device-enforcement zip is 94.6 MB.',
            'parse_cfr(citation)': "('21', part, section|None) or None (only Title 21 is meaningful here)",
        },
        'summary_fields': ['id', 'dataset', 'group', 'native_id', 'title', 'firm', 'firm_norm', 'firm_id', 'classification', 'status', 'product_code', 'state',
                           'country', 'published_at', 'sort_date', 'snippet', 'dates'],
        'routes': [
            {'method': 'GET', 'path': '/agency-safety/status', 'adapter': 'status()', 'example': safety.status() | {'counts': '... (validation.json counts)'}},
            {'method': 'GET', 'path': '/agency-safety/datasets', 'adapter': 'datasets()', 'example': trim(datasets[:2]) + ['... 10 more']},
            {'method': 'GET', 'path': '/agency-safety/search', 'params': ['dataset', 'q', 'firm', 'classification', 'status', 'product_code', 'date_type', 'dfrom', 'dto', 'page', 'limit'],
             'adapter': 'search(**params)',
             'example_request': '/agency-safety/search?dataset=enforcement&classification=Class%20I&date_type=recall_initiation_date&dfrom=2024-01-01&dto=2024-12-31&limit=2',
             'example': trim(enforcement)},
            {'method': 'GET', 'path': '/agency-safety/records/<dataset>/<id>', 'adapter': 'record(dataset, id)', 'example': trim(record)},
            {'method': 'GET', 'path': '/agency-safety/records/openfda_drugsfda/<application_number>', 'adapter': 'record("openfda_drugsfda", id)', 'example': trim(application_record)},
            {'method': 'GET', 'path': '/agency-safety/firms/<name>', 'params': ['page', 'limit'], 'adapter': 'firm(name, page, limit)', 'example': trim(firm)},
            {'method': 'GET', 'path': '/agency-safety/cfr/<citation>', 'params': ['page', 'limit'], 'adapter': 'for_cfr(citation, page, limit)', 'example': trim(cfr)},
            {'method': 'GET', 'path': '/agency-safety/search?dataset=fda_warning_letters&date_type=letter_issue_date&dfrom=2025-01&dto=2025-03&limit=1', 'adapter': 'search(...)', 'example': trim(letters)},
            {'method': 'GET', 'path': '/agency-safety/search?dataset=cpsc_recalls_local&q=crib&limit=1', 'adapter': 'search(...)', 'example': trim(cpsc)},
            {'method': 'GET', 'path': '/agency-safety/files/<file_id>', 'adapter': 'original(file_id) -> (bytes, mime, filename); 404 when None',
             'headers': {'Content-Type': 'from adapter (zip, xlsx, json, csv; otherwise application/octet-stream)', 'Content-Disposition': 'attachment; filename=<filename>',
                         'X-Content-Sha256': 'sha256 from datasets[].original_files'},
             'note': 'Stream or send whole; the largest original is 94,586,009 bytes and is fully re-hashed before it is served.'},
        ],
        'ui_placement': {
            'area': 'Agency safety & enforcement (new window.ARCHIVE_AREAS entry; rendered by areas.js)',
            'pages': [
                {'page': 'Overview', 'section': 'Datasets', 'source': '/agency-safety/datasets',
                 'columns': ['label', 'publisher', 'rows', 'source_as_of', 'captured_at', 'original file (download link + sha256 + bytes)'],
                 'caveat': 'Each dataset card shows its qualification sentence(s); the openFDA disclaimer is part of it.'},
                {'page': 'Search', 'section': 'Records', 'source': '/agency-safety/search',
                 'filters': [{'name': 'dataset', 'control': 'select of groups then datasets'}, {'name': 'q', 'control': 'text'}, {'name': 'firm', 'control': 'text (substring of normalised firm)'},
                             {'name': 'classification', 'control': 'select: Class I / Class II / Class III / 1 / 2 / 3 / U / N'}, {'name': 'status', 'control': 'select from facet of the current dataset'},
                             {'name': 'product_code', 'control': 'text (3 letters)'}, {'name': 'date_type', 'control': 'select limited to the chosen dataset\'s date_types'},
                             {'name': 'dfrom/dto', 'control': 'date inputs'}],
                 'result_row': ['sort_date', 'dataset label', 'title', 'firm', 'classification', 'status', 'state', 'snippet'],
                 'caveat': 'Dates are the publisher\'s own fields and are never merged: pick which date the range applies to.'},
                {'page': 'Record', 'section': 'Detail', 'source': '/agency-safety/records/<dataset>/<id>',
                 'blocks': ['normalised fields table', 'dates table (one row per publisher date type, empty = not stated by publisher)', 'temporal block with bases',
                            'links: firm page, CFR section (when a regulation number exists)', 'products and submissions tables (Drugs@FDA)',
                            'verbatim publisher record (collapsed JSON)', 'original file download with sha256'],
                 'caveat': 'An agency record about a product or firm is not proof of defect, causation or liability.'},
                {'page': 'Firm', 'section': 'Records for one normalised firm string', 'source': '/agency-safety/firms/<name>',
                 'blocks': ['verbatim spellings with counts', 'per-dataset counts', 'record list'],
                 'caveat': 'Grouping is exact normalised string equality only; other spellings of the same company are separate pages, and unrelated companies can share a string.'},
                {'page': 'CFR viewer (existing regulations area)', 'section': 'Device product codes classified under this section', 'source': '/agency-safety/cfr/<citation>',
                 'blocks': ['product code, device name, device class, related PMA row count'],
                 'caveat': 'From the openFDA device classification file as of its export date; the list of PMA rows is a product-code join, not a legal determination.'},
            ],
            'labels': {'openfda_*': 'FDA (openFDA bulk export, as of <source_as_of>)', 'fda_*': 'FDA.gov list export (captured <captured_at>)',
                       'cpsc_recalls_local': 'CPSC recalls - local file of unknown provenance, fields as found'},
        },
        'edges': {
            'file': 'edges.jsonl',
            'shape': {'from': {'type': '...', 'id': '...'}, 'to': {'type': '...', 'id': '...'}, 'relation': '...', 'basis': '...', 'evidence': {'field': '...', 'value': '...', 'dataset': '...'}},
            'relations': {
                'recalling_firm': 'fda_enforcement_recall openfda:<endpoint>:<recall_number> -> firm:<slug>; basis: publisher field recalling_firm',
                'application_sponsor': 'fda_drug_application openfda:drug/drugsfda:<application_number> -> firm:<slug>; basis: publisher field sponsor_name',
                'classified_under_regulation': 'fda_device_classification openfda:device/classification:<product_code> -> cfr:21:<part>.<section>; basis: publisher field '
                                               'regulation_number; evidence also records whether the section exists in the eCFR Title 21 structure snapshot (2026-09-16)',
            },
            'unresolved': 'unresolved.jsonl lists records whose join field is empty or malformed with the reason (no edge is guessed).',
            'firm_ids': 'firm:<normalised string, lower-case, spaces as hyphens>; the same grammar as firm_names.jsonl; no entity resolution.',
        },
        'caveats': [
            'openFDA: "Do not rely on openFDA to make decisions regarding medical care ... assume all results are unvalidated" (publisher disclaimer, stored per dataset).',
            'The FDA.gov warning-letters export is capped at 1,000 rows by the publisher and is not the complete list.',
            'The CPSC dataset is a local file of unknown provenance; cpsc.gov and saferproducts.gov were not contacted (robots.txt 403).',
            'Acquisition deviation pending user decision: robots.txt on download.open.fda.gov answered 403 before the bulk files were fetched (gaps.json).',
        ],
    }
    (HERE / 'integration_spec.json').write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding='utf-8')
    print('integration_spec.json written; routes', len(spec['routes']))


if __name__ == '__main__':
    main()
