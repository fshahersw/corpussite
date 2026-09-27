"""Write integration_spec.json with real example responses taken from the adapter (offline)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
import mdl_registry as reg  # noqa: E402

summary = reg.summary()
assert summary['available'], summary
listing = reg.listing({'litigation_type': 'Products Liability', 'limit': 3})
detail = reg.detail(3080)
detail_trim = dict(detail)
detail_trim['edges'] = detail['edges'][:2]
detail_trim['documents'] = detail['documents'][:2]
detail_trim['reports'] = detail['reports'][:2]
judge = reg.for_judge(listing['results'][0]['judge_entity_id']) if listing['results'][0].get('judge_entity_id') else reg.for_judge('none')
person = reg.for_person(listing['results'][0].get('cl_assigned_to_id') or 0)
gate = json.loads((HERE / 'validation.json').read_text(encoding='utf-8'))

spec = {
    'schema_version': '1',
    'supplement': 'sources/jpml_mdl_20260919',
    'adapter': 'delivery/archive-directory/mdl_registry.py',
    'adapter_test': 'delivery/archive-directory/test_mdl_registry.py',
    'build': 'sources/jpml_mdl_20260919/build.py (offline, re-runnable; test_build.py)',
    'gate': 'validation.json status == "passed" and ready == true and a matching SHA-256 for every data_files entry '
            '(mdls.jsonl, documents.jsonl, court_map.json, edges.jsonl, unresolved.jsonl, qa.json); re-verified whenever any of those files changes '
            '(size/mtime); originals are re-hashed on every serve. Any failure -> {available: false, reason} and empty listings; never an exception.',
    'functions': {
        'summary(folder=None)': '{available, reason?, as_of, counts_label, validated_at, qualification, license_ref, counts{mdls_pending, mdls_terminated, actions_pending, total_actions, transferee_districts, judges_resolved_mdls, judges_unresolved_mdls, cl_docket_links, litigation_types{}, documents, edges, unresolved}, judge_join{}, qa{status, checks, mismatches, mismatch_names[]}, snapshots{}}',
        'listing(params, folder=None)': 'params: q (case-insensitive substring over caption, judge names, master docket, MDL number), court (CourtListener court id or JPML district code), circuit (exact, e.g. "Ninth Circuit"), litigation_type (exact, case-insensitive), judge_resolved (true/false), min_pending (int), status (pending default | terminated | all), sort (pending_desc default | mdl_number | title), page (>=1), limit (1..200, default 25). Returns {available, as_of, counts_label, total, page, limit, pages, sort, filters, facets{litigation_type, circuit, court, judge_resolved}, results[summary rows], qualification}.',
        'detail(mdl_number, folder=None)': 'full path-free row: caption, status, district_code, cl_court_id, court_name, circuit, transferee_judge{name_as_printed, title_as_printed, name_by_number_report}, actions_pending, total_actions, counts_label, litigation_type, master_docket, dates, snapshots[] (2026-03-31, 2026-06-30, 2026-09-01), judge_links[{entity_id, display_name, basis: jpml_name_court, match_kind, fjc_nid}], cl_links (from saved connector responses; label says so), local_collections[], reports[], temporal{}, unresolved[], documents[{document_id, title, report_date, url:/mdl-files/<id>,...}], court{}, edges[], provenance{}, summary{}. None when unknown.',
        'for_judge(entity_id, folder=None)': '{available, entity_id, basis: jpml_name_court, total, results[summary rows]} - MDLs whose transferee judge resolved to this Judges-UI entity id',
        'for_person(cl_person_id, folder=None)': '{available, cl_person_id, basis: cl_assigned_to_id, total, results[]} - MDLs whose CourtListener master docket is assigned to this CL person id (only the 18 connector-checked MDLs can match)',
        'documents(folder=None)': 'list of the 16 registered originals (11 PDFs, 4 landing pages, robots.txt), path-free, each with url /mdl-files/<document_id>',
        'original(document_id, folder=None)': '(bytes, mime, filename) only after re-hashing the file under raw/ against documents.jsonl; None for unknown ids, altered bytes or paths outside raw/',
    },
    'routes': [
        {'method': 'GET', 'path': '/api/mdls', 'params': ['q', 'court', 'circuit', 'litigation_type', 'judge_resolved', 'min_pending', 'status', 'sort', 'page', 'limit'],
         'adapter': 'mdl_registry.listing(query_params)', 'example_query': '/api/mdls?litigation_type=Products%20Liability&limit=3', 'example_response': listing},
        {'method': 'GET', 'path': '/api/mdl', 'params': ['number'], 'adapter': 'mdl_registry.detail(number)', 'example_query': '/api/mdl?number=3080',
         'example_response_trimmed': detail_trim, 'not_found': 'HTTP 404 when detail() returns None'},
        {'method': 'GET', 'path': '/api/mdls/summary', 'params': [], 'adapter': 'mdl_registry.summary()', 'example_response': summary},
        {'method': 'GET', 'path': '/api/mdls/for-judge', 'params': ['entity_id'], 'adapter': 'mdl_registry.for_judge(entity_id)', 'example_response': judge,
         'ui_note': 'also usable from a judge profile page: "Transferee judge of N pending MDLs (as listed in the JPML report dated 2026-09-01)"'},
        {'method': 'GET', 'path': '/api/mdls/for-person', 'params': ['cl_person_id'], 'adapter': 'mdl_registry.for_person(cl_person_id)', 'example_response': person},
        {'method': 'GET', 'path': '/mdl-files/<document_id>', 'params': [], 'adapter': 'mdl_registry.original(document_id) -> (bytes, mime, filename); 404 on None',
         'headers': 'Content-Type from mime; Content-Disposition inline; filename=<filename>; X-Content-Type-Options: nosniff', 'example': '/mdl-files/' + detail['documents'][0]['document_id']},
    ],
    'ui_placement': {
        'area': 'MDLs & mass torts',
        'page': 'MDL registry (JPML)',
        'sections': [
            {'name': 'Header strip', 'content': 'N pending MDLs, M actions pending, T total actions, D districts - every figure suffixed with counts_label ("as listed in the JPML report dated 2026-09-01"); link to the by-district and by-MDL-number originals'},
            {'name': 'Filters', 'content': 'litigation type (facet counts), circuit, court/district, judge resolved, min actions pending, status (pending/terminated), search box (q); sort: actions pending desc (default), MDL number, caption'},
            {'name': 'Table', 'columns': ['MDL No.', 'Caption', 'Transferee district (court_name)', 'Transferee judge (as printed; link to Judges UI entity when judge_resolved)', 'Type', 'Actions pending', 'Total actions', 'Transferred']},
            {'name': 'Detail drawer', 'content': 'caption, master docket + dates, judge as printed with title, judge link (entity id, match_kind shown as a small tag: exact / middle initial / middle omitted / first initial), quarterly snapshots table (Mar 31 / Jun 30 / Sep 1 with their own labels), CourtListener docket (docket id, assigned judge person id, agreement flag) when available, local MDL 3080 collection link, originals (report PDFs) via /mdl-files, unresolved reasons in plain words'},
            {'name': 'Judge profile cross-link', 'content': 'on a judge profile: "Transferee judge of: <MDL list>" from for_judge(entity_id); never show a judge as linked when judge_links is empty'},
        ],
        'labels': {'counts_label': 'as listed in the JPML report dated <as of>', 'terminated': 'Terminated (JPML recently-terminated listing, closed <date_closed>)',
                   'connector': 'CourtListener data comes from saved connector responses (not original HTTP bytes); show requested/saved timestamps'},
        'caveats': [
            'Actions pending / total actions are the JPML\'s own CM/ECF counts as of the printed report date; they are not independently verified and change daily.',
            'A judge link exists only when the printed name and transferee district matched exactly one FJC appointment at that court (normalized; match_kind explains any initial/middle-name normalization). 9 printed names remain unresolved and are listed with reasons.',
            'Litigation type is the JPML docket-type category, not a legal classification.',
            'The March 31 and June 30 snapshots come from the quarterly by-circuit reports and are labelled with their own dates; the June report omits one MDL (3074) that reappears in September (listed in qa.json).',
            'CourtListener docket ids/person ids exist only for the 18 largest products-liability MDLs that were cross-checked; absence of a cl_link means "not checked", not "not on CourtListener".',
            'The circuit comes from the district grouping in the JPML by-circuit reports; court ids are a curated crosswalk verified against the local CourtListener courts table.',
        ],
    },
    'edges': {
        'file': 'sources/jpml_mdl_20260919/edges.jsonl',
        'format': '{from:{type,id}, to:{type,id}, relation, basis, evidence}',
        'relations': {
            'mdl -> cl_court (transferee_district)': 'basis jpml_district_code_crosswalk; evidence: district code, report document id, as_of',
            'mdl -> judge_entity (transferee_judge)': 'basis jpml_name_court; evidence: printed name, district, fjc_nid, fjc court, match_kind',
            'mdl -> cl_docket (master_docket)': 'basis cl_docket_search; evidence: connector file + sha256, docket number, court id',
            'mdl -> cl_person (assigned_judge)': 'basis cl_assigned_to_id; evidence: connector file, docket id, agreement_with_name_join',
            'mdl -> local_collection (has_local_documents)': 'basis mdl_number_only; evidence: local file, court labels, sha256',
        },
        'counts': gate['counts'].get('edges_by_basis'),
        'unresolved': 'unresolved.jsonl lists every printed judge name that did not join, with reason and candidate evidence',
    },
    'counts_snapshot': gate['counts'],
}
(HERE / 'integration_spec.json').write_bytes(json.dumps(spec, ensure_ascii=False, indent=1).encode('utf-8'))
print('integration_spec.json written; listing total', listing['total'], 'detail docs', len(detail['documents']))
