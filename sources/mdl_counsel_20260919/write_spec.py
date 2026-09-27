"""Write integration_spec.json with real example responses from the adapter (offline)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'delivery' / 'archive-directory'))
import mdl_counsel  # noqa: E402

example_listing = mdl_counsel.listing({'kind': 'attorney', 'mdl': '2666', 'q': 'zimmerman', 'limit': '2'})
example_detail = mdl_counsel.detail('clatt-7167601-mdl2666')
firm = mdl_counsel.listing({'kind': 'firm', 'mdl': '2666', 'limit': '1'})
spec = {
    'adapter': 'delivery/archive-directory/mdl_counsel.py',
    'contract': 'ROUND 3 generic view contract',
    'functions': {
        'listing': 'listing(params: dict, source_dir=None) -> dict   # params: q, kind (firm|attorney|party, default firm), mdl, page, limit<=100',
        'detail': 'detail(item_id: str, source_dir=None) -> dict | None   # ids: firm-<hash>, clatt-<cl id>-mdl<n>, clattname-mdl<n>-<hash>, clparty-<cl id>-mdl<n>, clpartyname-mdl<n>-<hash>',
        'original': 'not provided (no originals are served; receipts contain contact details and are never exposed)',
    },
    'proposed_routes': [
        {'method': 'GET', 'path': '/api/areas/mdl-counsel', 'params': ['q', 'kind', 'mdl', 'page', 'limit'], 'calls': 'mdl_counsel.listing(query_params)'},
        {'method': 'GET', 'path': '/api/areas/mdl-counsel/item', 'params': ['id'], 'calls': 'mdl_counsel.detail(id)'},
    ],
    'ui_placement': {
        'area': 'MDLs', 'page': 'MDL counsel (firms, attorneys, parties)',
        'also': 'on an MDL page (#mdl/<number>) for 2666, 2738, 2789, 2846, 2873, 3060: a "Counsel on the master docket" section = listing({"mdl": <number>, "kind": ...})',
        'filters': ['Search', 'Show (Firms / Attorneys / Parties)', 'MDL'],
        'columns': ['Name', 'MDL', 'Role or Type', 'Count'],
        'caveat_sentences': [
            'Partial coverage: CourtListener connector responses captured 2026-09-19, master dockets only; not original court records.',
            'Only MDL 2666 has attorney records with roles and a firm line; other MDLs list names only.',
            'Firm rows are firm text as recorded and are never merged, so one firm can appear under several spellings.',
            'A bar-admission note about an individual attorney that the source glued to a firm text is removed by rule; firm-field texts that were only that note, or address lines of litigants, are not shown.',
            'Contact details are not published. Party names are withheld for dockets with thousands of individual parties.',
        ],
    },
    'edges': 'edges.jsonl: attorney:cl_attorney:<CourtListener attorney id> -> mdl:<number> (counsel_of_record_in_master_docket) and -> firm:<slug of exact firm text> (firm_line_in_contact_block); 27 attorney records only; evidence = receipt file + sha256',
    'example_listing_request': {'kind': 'attorney', 'mdl': '2666', 'q': 'zimmerman', 'limit': '2'},
    'example_listing_response': example_listing,
    'example_detail_request': {'id': 'clatt-7167601-mdl2666'},
    'example_detail_response': example_detail,
    'example_firm_row': (firm.get('results') or [None])[0],
}
(HERE / 'integration_spec.json').write_text(json.dumps(spec, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
cov = json.loads((HERE / 'coverage.json').read_text(encoding='utf-8'))['mdls']
for k, v in cov.items():
    print(k, v['attorney_records_retrieved'], v['attorney_name_only_rows'], v['attorney_ids_reported_by_search_index'],
          v['firm_names_reported_by_search_index'], v['firm_ids_reported_by_search_index'], v['party_ids_reported_by_search_index'],
          v['party_records_retrieved'], v['party_name_only_rows'], v['party_names_withheld'])
for kind in ('firm', 'attorney', 'party'):
    print(kind, mdl_counsel.listing({'kind': kind})['total'])
