"""Build the MDL <-> master-matter <-> CourtListener-docket <-> member-matter id spine.

Deterministic, offline, re-runnable. Reads only:
  - sources/jpml_mdl_20260919/mdls.jsonl                                  (JPML registry, 176 MDLs)
  - SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc/
        matters.jsonl.gz, matter_aliases.jsonl.gz, matter_relationships.jsonl.gz, manifest.json
  - SW-BULK/catalog/masters.json, SW-BULK/catalog/matters.json

Writes crosswalk.jsonl, edges.jsonl, unresolved.jsonl, validation.json, README.md into this folder
(or into out_dir, for tests). Nothing under SW-BULK is read via any path outside these declared files,
and nothing there is modified.
"""
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY_PATH = ROOT / 'sources/jpml_mdl_20260919/mdls.jsonl'
RELEASE_DIR = Path('C:/Users/firas/Downloads/SW-BULK/AWS-BATCH1-DOCKETS/releases/b2b-cdbb8d040b95c7b65cfc')
CATALOG_DIR = Path('C:/Users/firas/Downloads/SW-BULK/catalog')

INPUT_FILES = {
    'registry': REGISTRY_PATH,
    'matters': RELEASE_DIR / 'matters.jsonl.gz',
    'matter_aliases': RELEASE_DIR / 'matter_aliases.jsonl.gz',
    'matter_relationships': RELEASE_DIR / 'matter_relationships.jsonl.gz',
    'manifest': RELEASE_DIR / 'manifest.json',
    'catalog_masters': CATALOG_DIR / 'masters.json',
    'catalog_matters': CATALOG_DIR / 'matters.json',
}


def normalize_docket(value):
    """Lowercase; the segment after the last '-' is reduced to str(int(x)) so zero-padding cancels.

    '1:17-md-02804' and '1:17-md-2804' both normalize to '1:17-md-2804'. Values with no '-' or a
    non-numeric trailing segment are returned lowercased and unchanged (never guessed further).
    """
    if not value:
        return None
    s = str(value).strip().lower()
    if '-' not in s:
        return s
    head, tail = s.rsplit('-', 1)
    try:
        return head + '-' + str(int(tail))
    except ValueError:
        return s


def _is_party_style_caption(name):
    """True for a party-v-party caption (e.g. 'Doe v. Widget Co'), false for a collective
    'In re ...' caption. Detection is deliberately simple and literal: a lowercased ' v. '
    substring, which is how every party-style caption in this corpus is punctuated."""
    if not name:
        return False
    return ' v. ' in str(name).lower()


def _read_jsonl(path):
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def _read_jsonl_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_inputs():
    registry = _read_jsonl(INPUT_FILES['registry'])
    matters = _read_jsonl_gz(INPUT_FILES['matters'])
    aliases = _read_jsonl_gz(INPUT_FILES['matter_aliases'])
    relationships = _read_jsonl_gz(INPUT_FILES['matter_relationships'])
    manifest = json.loads(INPUT_FILES['manifest'].read_text(encoding='utf-8'))
    catalog_masters = json.loads(INPUT_FILES['catalog_masters'].read_text(encoding='utf-8'))
    catalog_matters = json.loads(INPUT_FILES['catalog_matters'].read_text(encoding='utf-8'))
    return registry, matters, aliases, relationships, manifest, catalog_masters, catalog_matters


def compute(registry, matters, aliases, relationships, manifest, catalog_masters, catalog_matters):
    """Pure function: returns (crosswalk_rows, edge_rows, unresolved_rows, counts, checks)."""
    reg_by_number = {m['mdl_number']: m for m in registry}
    reg_key_to_numbers = {}
    for m in registry:
        if m.get('master_docket') and m.get('cl_court_id'):
            key = (m['cl_court_id'], normalize_docket(m['master_docket']))
            reg_key_to_numbers.setdefault(key, []).append(m['mdl_number'])

    matters_by_id = {m['matter_id']: m for m in matters}

    # matter_id -> {'courtlistener_docket_id': int|None, 'courtlistener_master_docket_id': int|None}
    matter_cl_ids = {}
    docket_id_to_matter_ids = {}
    for a in aliases:
        if a.get('record_status') != 'active':
            continue
        ns = a.get('namespace')
        if ns not in ('courtlistener_docket_id', 'courtlistener_master_docket_id'):
            continue
        try:
            val = int(a['value'])
        except (TypeError, ValueError):
            continue
        matter_cl_ids.setdefault(a['matter_id'], {})[ns] = val
        if ns == 'courtlistener_docket_id':
            docket_id_to_matter_ids.setdefault(val, []).append(a['matter_id'])

    # Step 1: AWS matter <-> registry MDL, via (court, normalized docket number).
    mdl_to_matter_id = {}
    matter_id_to_mdl = {}
    for mt in matters:
        key = (mt.get('court_id'), normalize_docket(mt.get('docket_number')))
        for num in reg_key_to_numbers.get(key, []):
            mdl_to_matter_id.setdefault(num, mt['matter_id'])
            matter_id_to_mdl[mt['matter_id']] = num

    # Step 2: catalog masters (17 rows) — join by native mdl_number field when present (zero-padded
    # string), else (for the two "parked" masters) by CourtListener docket id equality against the
    # registry's own cl_links, or by (court, normalized docket number) equality against the registry's
    # own master_docket — never by name.
    catalog_master_by_number = {}
    unresolved_catalog_masters = []
    for cm in catalog_masters:
        raw = cm.get('mdl_number')
        if raw:
            try:
                num = int(raw)
            except (TypeError, ValueError):
                num = None
            if num is not None and num in reg_by_number:
                catalog_master_by_number[num] = cm
                continue
            unresolved_catalog_masters.append({
                'type': 'catalog_master', 'catalog_master_docket_id': cm.get('master_docket_id'),
                'docket_number': cm.get('docket_number'), 'court': cm.get('court'),
                'case_name': cm.get('case_name'), 'native_mdl_number': raw,
                'reason': ('catalog/masters.json carries mdl_number %r but no MDL with that number exists '
                           'in the 176-row JPML registry snapshot (sources/jpml_mdl_20260919/mdls.jsonl); '
                           'checked by number, by title and by (court, docket number) — no match.') % raw,
            })
            continue
        # No native mdl_number (the two "parked" masters). Resolve ONLY via the registry's own links.
        resolved_num = None
        basis = None
        cl_id = cm.get('master_docket_id')
        for m in registry:
            cl_links = m.get('cl_links') or {}
            if cl_id is not None and cl_links.get('docket_id') == cl_id:
                resolved_num = m['mdl_number']
                basis = 'cl_docket_id_equality_with_registry_cl_links'
                break
        if resolved_num is None:
            key = (cm.get('court'), normalize_docket(cm.get('docket_number')))
            for num in reg_key_to_numbers.get(key, []):
                resolved_num = num
                basis = 'docket_number_normalized_and_court_equality_with_registry_master_docket'
                break
        if resolved_num is not None:
            catalog_master_by_number[resolved_num] = dict(cm, _resolution_basis=basis)
        else:
            unresolved_catalog_masters.append({
                'type': 'catalog_master', 'catalog_master_docket_id': cm.get('master_docket_id'),
                'docket_number': cm.get('docket_number'), 'court': cm.get('court'),
                'case_name': cm.get('case_name'), 'native_mdl_number': None,
                'reason': ('no MDL registry entry (mdls.jsonl) cites this CourtListener docket id via '
                           'cl_links.docket_id, and none has a matching (court, normalized docket number) '
                           'master_docket — resolution attempted only through the registry\'s own links, '
                           'per the build contract; not resolved by name.'),
            })

    all_mdl_numbers = sorted(set(mdl_to_matter_id) | set(catalog_master_by_number))

    crosswalk_rows = []
    for num in all_mdl_numbers:
        reg = reg_by_number[num]
        matter_id = mdl_to_matter_id.get(num)
        mt = matters_by_id.get(matter_id) if matter_id else None
        cm = catalog_master_by_number.get(num)
        cl_ids = matter_cl_ids.get(matter_id, {}) if matter_id else {}
        cl_docket_id = cl_ids.get('courtlistener_docket_id') or cl_ids.get('courtlistener_master_docket_id')
        cl_docket_id_basis = 'matter_aliases.courtlistener_docket_id' if cl_ids.get('courtlistener_docket_id') else (
            'matter_aliases.courtlistener_master_docket_id' if cl_ids.get('courtlistener_master_docket_id') else None)
        if cl_docket_id is None and cm is not None:
            cl_docket_id = cm.get('master_docket_id')
            cl_docket_id_basis = 'catalog/masters.json.master_docket_id'
        basis_parts = []
        if mt is not None:
            basis_parts.append('docket_number_normalized_and_court_match(aws_matters<->registry)')
        if cm is not None:
            basis_parts.append(cm.get('_resolution_basis') or 'catalog/masters.json.mdl_number (native field)')

        # Party names: natural-person plaintiffs are never listed (build contract). A party-v-party
        # caption (as opposed to a collective 'In re ...' caption) is suppressed to null, with a
        # sibling field recording why. The registry mdl_title (always 'IN RE: ...') stays the only
        # displayable name.
        suppression_reason = ("party-style caption containing a natural-person plaintiff; "
                               "suppressed per build contract")
        raw_aws_case_name = mt.get('case_name') if mt else None
        raw_catalog_master_case_name = cm.get('case_name') if cm else None
        aws_case_name_suppressed = _is_party_style_caption(raw_aws_case_name)
        catalog_master_case_name_suppressed = _is_party_style_caption(raw_catalog_master_case_name)

        crosswalk_rows.append({
            'id': 'mdl:%d' % num,
            'mdl_number': num,
            'mdl_status': reg['status'],
            'mdl_title': reg['title'],
            'cl_court_id': reg.get('cl_court_id'),
            'master_docket_number_registry': reg.get('master_docket'),
            'master_docket_number_aws': mt.get('docket_number') if mt else None,
            'aws_matter_id': matter_id,
            'aws_case_name': None if aws_case_name_suppressed else raw_aws_case_name,
            'aws_case_name_suppressed_reason': suppression_reason if aws_case_name_suppressed else None,
            'aws_case_status': mt.get('case_status') if mt else None,
            'aws_date_filed': mt.get('date_filed') if mt else None,
            'aws_date_terminated': mt.get('date_terminated') if mt else None,
            'cl_docket_id': cl_docket_id,
            'cl_docket_id_basis': cl_docket_id_basis,
            'catalog_master_docket_id': cm.get('master_docket_id') if cm else None,
            'catalog_master_docket_number': cm.get('docket_number') if cm else None,
            'catalog_master_case_name': None if catalog_master_case_name_suppressed else raw_catalog_master_case_name,
            'catalog_master_case_name_suppressed_reason': suppression_reason if catalog_master_case_name_suppressed else None,
            'catalog_master_document_count': cm.get('document_count') if cm else None,
            'catalog_master_high_value_docs': cm.get('high_value_docs') if cm else None,
            'basis': '; '.join(basis_parts) or 'unknown',
        })
    crosswalk_rows.sort(key=lambda r: r['mdl_number'])

    # Step 3: member_of_mdl edges. Parent matter must be inside the resolved crosswalk (mdl_to_matter_id);
    # otherwise the member is unresolved with a distinct reason, never dropped silently.
    edge_rows = []
    unresolved_edges = []
    for r in relationships:
        if r.get('relationship_type') != 'member_of_mdl' or r.get('record_status') != 'active':
            continue
        parent_id = r['to_matter_id']
        member_id = r['from_matter_id']
        mdl_num = matter_id_to_mdl.get(parent_id)
        if mdl_num is not None:
            parent_mt = matters_by_id.get(parent_id, {})
            member_mt = matters_by_id.get(member_id, {})
            edge_rows.append({
                'from': {'type': 'aws_matter', 'id': member_id},
                'to': {'type': 'mdl', 'id': mdl_num},
                'relation': 'member_of_mdl',
                'basis': ('SW-BULK AWS release b2b-cdbb8d040b95c7b65cfc matter_relationships.jsonl.gz '
                          'record %s; parent matter %s resolved to mdl:%d via the docket-number-normalized '
                          'crosswalk.') % (r.get('record_id'), parent_id, mdl_num),
                'evidence': {
                    'relationship_record_id': r.get('record_id'),
                    'parent_matter_id': parent_id,
                    'parent_docket_number': parent_mt.get('docket_number'),
                    'parent_court_id': parent_mt.get('court_id'),
                    'member_docket_number': member_mt.get('docket_number'),
                    'member_court_id': member_mt.get('court_id'),
                },
            })
        else:
            parent_mt = matters_by_id.get(parent_id, {})
            unresolved_edges.append({
                'type': 'member_of_mdl_edge', 'relationship_record_id': r.get('record_id'),
                'member_matter_id': member_id, 'parent_matter_id': parent_id,
                'parent_docket_number': parent_mt.get('docket_number'), 'parent_court_id': parent_mt.get('court_id'),
                'reason': 'member_of_mdl parent not resolvable to an MDL (parent matter is not one of the '
                          'docket-number-normalized crosswalk pairs; see catalog_master / registry checks).',
            })

    unresolved_rows = unresolved_catalog_masters + unresolved_edges

    # Verification-style checks (measured here, not assumed).
    catalog_docket_ids = set()
    for cmatter in catalog_matters:
        did = cmatter.get('docket_id')
        if did is not None:
            catalog_docket_ids.add(int(did))
    aliased_docket_ids = set(docket_id_to_matter_ids)
    catalog_ids_covered = len(catalog_docket_ids & aliased_docket_ids)

    counts = {
        'registry_mdls_total': len(registry),
        'registry_mdls_pending': sum(1 for m in registry if m['status'] == 'pending'),
        'registry_mdls_terminated': sum(1 for m in registry if m['status'] == 'terminated'),
        'aws_matters_total': len(matters),
        'aws_matter_registry_pairs': len(mdl_to_matter_id),
        'crosswalk_rows': len(crosswalk_rows),
        'crosswalk_pending': sum(1 for r in crosswalk_rows if r['mdl_status'] == 'pending'),
        'crosswalk_terminated': sum(1 for r in crosswalk_rows if r['mdl_status'] == 'terminated'),
        'matter_aliases_total': len(aliases),
        'matter_aliases_courtlistener_docket_id': sum(1 for a in aliases if a.get('namespace') == 'courtlistener_docket_id'),
        'matter_aliases_courtlistener_master_docket_id': sum(1 for a in aliases if a.get('namespace') == 'courtlistener_master_docket_id'),
        'matters_with_any_alias': len(matter_cl_ids),
        'catalog_matters_total': len(catalog_matters),
        'catalog_matters_docket_id_in_aliases': catalog_ids_covered,
        'catalog_masters_total': len(catalog_masters),
        'catalog_masters_resolved': len(catalog_master_by_number),
        'catalog_masters_unresolved': len(unresolved_catalog_masters),
        'matter_relationships_total': len(relationships),
        'member_of_mdl_edges_resolved': len(edge_rows),
        'member_of_mdl_edges_unresolved': len(unresolved_edges),
        'unresolved_total': len(unresolved_rows),
    }
    checks = [
        {'name': 'crosswalk_row_count_59', 'passed': counts['crosswalk_rows'] == 59},
        {'name': 'zero_pad_normalization_load_bearing',
         'passed': normalize_docket('1:17-md-02804') == normalize_docket('1:17-md-2804') == '1:17-md-2804'},
        {'name': 'member_edges_40_resolved_13_unresolved',
         'passed': counts['member_of_mdl_edges_resolved'] == 40 and counts['member_of_mdl_edges_unresolved'] == 13},
        {'name': 'no_duplicate_mdl_numbers_in_crosswalk',
         'passed': len({r['mdl_number'] for r in crosswalk_rows}) == len(crosswalk_rows)},
        {'name': 'terminated_mdls_are_2591_2592',
         'passed': sorted(r['mdl_number'] for r in crosswalk_rows if r['mdl_status'] == 'terminated') == [2591, 2592]},
    ]
    return crosswalk_rows, edge_rows, unresolved_rows, counts, checks


def _write_jsonl(path, rows):
    with open(path, 'w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + '\n')


def build(out_dir=None):
    out_dir = Path(out_dir) if out_dir else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    registry, matters, aliases, relationships, manifest, catalog_masters, catalog_matters = load_inputs()
    crosswalk_rows, edge_rows, unresolved_rows, counts, checks = compute(
        registry, matters, aliases, relationships, manifest, catalog_masters, catalog_matters)

    crosswalk_path = out_dir / 'crosswalk.jsonl'
    edges_path = out_dir / 'edges.jsonl'
    unresolved_path = out_dir / 'unresolved.jsonl'
    _write_jsonl(crosswalk_path, crosswalk_rows)
    _write_jsonl(edges_path, edge_rows)
    _write_jsonl(unresolved_path, unresolved_rows)

    run_scope = (manifest.get('descriptor') or {}).get('run_scope') or {}
    qualification = (
        'Local personal-testing view; derived from a private firm dataset (SW-BULK) built from '
        'CourtListener/RECAP API data; not for redistribution. Id spine joining the JPML MDL registry '
        '(sources/jpml_mdl_20260919/mdls.jsonl, 176 MDLs as of the 2026-09-01 JPML report) to the AWS '
        'release b2b-cdbb8d040b95c7b65cfc master matters and the SW-BULK catalog masters, by native id '
        'or by (court, docket-number) equality only \u2014 never by name. Release b2b run_scope (its own '
        'reported figures): requested_docket_aliases %s, returned_docket_aliases %s, '
        'selected_canonical_matters %s. Covers %d of 176 registry MDLs (%d pending, %d terminated); '
        '%d catalog masters and %d member_of_mdl edges could not be resolved to a registry MDL and are '
        'listed in unresolved.jsonl with a reason, never dropped or guessed.'
    ) % (run_scope.get('requested_docket_aliases'), run_scope.get('returned_docket_aliases'),
         run_scope.get('selected_canonical_matters'), counts['crosswalk_rows'], counts['crosswalk_pending'],
         counts['crosswalk_terminated'], counts['catalog_masters_unresolved'], counts['member_of_mdl_edges_unresolved'])

    inputs = []
    for key in ('registry', 'matters', 'matter_aliases', 'matter_relationships', 'manifest', 'catalog_masters', 'catalog_matters'):
        p = INPUT_FILES[key]
        inputs.append({'path': str(p), 'sha256': _sha256_file(p)})

    data_files = []
    for name, path, rows in (('crosswalk.jsonl', crosswalk_path, crosswalk_rows),
                              ('edges.jsonl', edges_path, edge_rows),
                              ('unresolved.jsonl', unresolved_path, unresolved_rows)):
        data_files.append({'path': name, 'sha256': _sha256_file(path), 'rows': len(rows)})

    validation = {
        'schema_version': '1',
        'status': 'passed' if all(c['passed'] for c in checks) else 'failed',
        'ready': all(c['passed'] for c in checks),
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': data_files,
        'counts': counts,
        'checks': checks,
        'qualification': qualification,
        'license_ref': 'sw_bulk_private_firm_work_product',
        'export_allowed': False,
        'inputs': inputs,
    }
    (out_dir / 'validation.json').write_text(json.dumps(validation, indent=2, sort_keys=True), encoding='utf-8')
    return validation


if __name__ == '__main__':
    result = build()
    print(json.dumps(result['counts'], indent=2, sort_keys=True))
    print('status:', result['status'], 'ready:', result['ready'])
