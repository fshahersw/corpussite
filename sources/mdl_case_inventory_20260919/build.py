"""Build the MDL member-case inventory and relationship graph (slice 8).

Deterministic, re-runnable, offline. Reads only:
  - SW-BULK AWS release b2b-cdbb8d040b95c7b65cfc (main tree): matters, matter_aliases,
    judicial_assignments, judges, matter_relationships
  - SW-BULK/catalog/matters.json (main tree only; never .worktrees/)
  - SCRAPE sources/mdl_docket_crosswalk_20260919 (slice 1: crosswalk.jsonl + edges.jsonl)
  - SCRAPE sources/jpml_mdl_20260919/mdls.jsonl (JPML pending-action denominators)
  - SCRAPE sources/court_spine_20260919/courts.jsonl (court ids)
  - SCRAPE sources/judge_structured_20260919/overlay.jsonl (cl_person_id -> judge entity)

Writes cases.jsonl, graph_edges.jsonl, unresolved.jsonl, mdl_coverage.json, validation.json.

Join rules (hard):
  * Courts join by exact CourtListener court id only. Never by name.
  * Judges reach an MVP judge entity ONLY via cl_person_id. The AWS release's
    `judges.native_judge_id` IS a CourtListener person id: batch1_registry/bulk_enrichment.py
    selects `p.id AS judge_id` from the CourtListener people table and
    batch1_registry/batch2a.py writes `native_judge_id=str(raw["judge_id"])`, sourcing it as
    "courtlistener_bulk_dockets". Names are never used to make a link.
  * MDL membership comes from slice 1's crosswalk (master matters) and its member_of_mdl
    edges, or from catalog `mdl_master_docket_id` pointing at a resolved master docket id.
  * Every rollup in validation.json is recomputed from the emitted rows.
"""
import collections
import datetime
import gzip
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCRAPE = os.path.abspath(os.path.join(HERE, '..', '..'))
SW_BULK = r'C:\Users\firas\Downloads\SW-BULK'
RELEASE = os.path.join(SW_BULK, 'AWS-BATCH1-DOCKETS', 'releases', 'b2b-cdbb8d040b95c7b65cfc')

IN_MATTERS = os.path.join(RELEASE, 'matters.jsonl.gz')
IN_ALIASES = os.path.join(RELEASE, 'matter_aliases.jsonl.gz')
IN_ASSIGNMENTS = os.path.join(RELEASE, 'judicial_assignments.jsonl.gz')
IN_JUDGES = os.path.join(RELEASE, 'judges.jsonl.gz')
IN_RELATIONSHIPS = os.path.join(RELEASE, 'matter_relationships.jsonl.gz')
IN_MANIFEST = os.path.join(RELEASE, 'manifest.json')
IN_CATALOG_MATTERS = os.path.join(SW_BULK, 'catalog', 'matters.json')
IN_CROSSWALK = os.path.join(SCRAPE, 'sources', 'mdl_docket_crosswalk_20260919', 'crosswalk.jsonl')
IN_CROSSWALK_EDGES = os.path.join(SCRAPE, 'sources', 'mdl_docket_crosswalk_20260919', 'edges.jsonl')
IN_MDLS = os.path.join(SCRAPE, 'sources', 'jpml_mdl_20260919', 'mdls.jsonl')
IN_COURTS = os.path.join(SCRAPE, 'sources', 'court_spine_20260919', 'courts.jsonl')
IN_OVERLAY = os.path.join(SCRAPE, 'sources', 'judge_structured_20260919', 'overlay.jsonl')

OUT_CASES = os.path.join(HERE, 'cases.jsonl')
OUT_EDGES = os.path.join(HERE, 'graph_edges.jsonl')
OUT_UNRESOLVED = os.path.join(HERE, 'unresolved.jsonl')
OUT_COVERAGE = os.path.join(HERE, 'mdl_coverage.json')
OUT_VALIDATION = os.path.join(HERE, 'validation.json')

RELEASE_LABEL = 'SW-BULK AWS release b2b-cdbb8d040b95c7b65cfc (built 2026-08-24)'
CATALOG_LABEL = 'SW-BULK catalog/matters.json (main tree)'

CAPTION_SUPPRESSION_REASON = (
    'caption is not a collective "In re" caption, so it may name a natural-person party; '
    'suppressed per the build contract (party names: natural-person plaintiffs are never listed)')

CAPTION_PARTY_SEPARATOR_REASON = (
    'caption begins with "In re"/"In the Matter of" but contains a " v."/" vs." party separator, '
    'so it is an individual member-case caption naming a party rather than a collective MDL '
    'caption; suppressed per the build contract (party names: natural-person plaintiffs are never '
    'listed)')

_PARTY_SEPARATOR_RE = re.compile(r'(?i)\bv\.?\s|\bvs\.?\s')

NOS_BASIS = (
    'nature of suit is not present in any file of the AWS release or in SW-BULK catalog/matters.json; '
    'it was not inferred')


# ---------------------------------------------------------------- rules

def is_displayable_caption(name):
    """True only for a collective 'In re ...'/'In the Matter of ...' caption that carries no
    ' v.'/' vs.' party separator. A caption that merely starts with 'In re' but contains a party
    separator is an individual member-case caption (e.g. 'In re: Samuel Keller v. Electronic Arts
    Inc.') and still names a natural person, so it is not displayable either."""
    if not name:
        return False
    s = str(name).strip().lower()
    starts_collective = (s.startswith('in re:') or s.startswith('in re ')
                          or s.startswith('in the matter of'))
    if not starts_collective:
        return False
    return _PARTY_SEPARATOR_RE.search(str(name)) is None


def resolve_court(native_court_id, spine_by_id):
    """Exact CourtListener court id match only. Returns (court_row|None, reason|None)."""
    if not native_court_id:
        return None, 'no court id on the source row'
    row = spine_by_id.get(str(native_court_id))
    if row is None:
        return None, ('court id %r is not present in sources/court_spine_20260919/courts.jsonl; '
                      'not resolved by name' % str(native_court_id))
    return row, None


def resolve_judge_entity(cl_person_id, entity_by_cl_person):
    """Judge entity join via cl_person_id only. Returns (entity_id|None, reason|None)."""
    if cl_person_id in (None, ''):
        return None, ('no native judge id on the AWS judges row, so no CourtListener person id '
                      'to bridge on; never matched by name')
    ent = entity_by_cl_person.get(str(cl_person_id))
    if ent is None:
        return None, ('CourtListener person %s has no bridged entity in '
                      'sources/judge_structured_20260919/overlay.jsonl (ids.cl_person_id); '
                      'never matched by name' % str(cl_person_id))
    return ent, None


def classify_mdl_membership(matter_id, cl_docket_id, master_by_matter, member_by_matter,
                            catalog_master_link):
    """Returns (membership_kind|None, mdl_number|None, evidence_or_reason)."""
    if matter_id in master_by_matter:
        num = master_by_matter[matter_id]
        return ('master_docket_of_mdl', num,
                'this matter is the MDL master docket for mdl:%d in '
                'sources/mdl_docket_crosswalk_20260919/crosswalk.jsonl (aws_matter_id match)' % num)
    if matter_id in member_by_matter:
        num = member_by_matter[matter_id]
        return ('member_of_mdl', num,
                'member_of_mdl edge in sources/mdl_docket_crosswalk_20260919/edges.jsonl '
                '(from aws_matter %s) resolves to mdl:%d' % (matter_id, num))
    if catalog_master_link is not None:
        num = catalog_master_link
        return ('catalog_mdl_master_docket_id', num,
                'SW-BULK catalog/matters.json mdl_master_docket_id points at a CourtListener '
                'master docket id that the slice-1 crosswalk resolves to mdl:%d' % num)
    return (None, None,
            'no member_of_mdl relationship in the AWS release and no mdl_master_docket_id in '
            'catalog/matters.json resolves to a JPML registry MDL; not guessed')


# ---------------------------------------------------------------- io helpers

def _read_jsonl(path):
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _read_jsonl_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _write_jsonl(path, rows):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')


def _year(d):
    if not d or len(str(d)) < 4:
        return None
    try:
        return int(str(d)[:4])
    except ValueError:
        return None


# ---------------------------------------------------------------- build

def build():
    unresolved = []

    # --- reference layers
    spine_by_id = {}
    for c in _read_jsonl(IN_COURTS):
        spine_by_id[str(c['id'])] = c

    entity_by_cl_person = {}
    for e in _read_jsonl(IN_OVERLAY):
        cp = (e.get('ids') or {}).get('cl_person_id')
        if cp not in (None, ''):
            entity_by_cl_person.setdefault(str(cp), e['entity_id'])

    mdl_by_number = {}
    for m in _read_jsonl(IN_MDLS):
        mdl_by_number[int(m['mdl_number'])] = m

    crosswalk = list(_read_jsonl(IN_CROSSWALK))
    master_by_matter = {}
    mdl_by_master_cl_docket = {}
    for r in crosswalk:
        if r.get('aws_matter_id'):
            master_by_matter[r['aws_matter_id']] = int(r['mdl_number'])
        for key in ('cl_docket_id', 'catalog_master_docket_id'):
            if r.get(key) is not None:
                mdl_by_master_cl_docket[int(r[key])] = int(r['mdl_number'])

    member_by_matter = {}
    member_edge_evidence = {}
    for e in _read_jsonl(IN_CROSSWALK_EDGES):
        if e.get('relation') == 'member_of_mdl' and e['from']['type'] == 'aws_matter':
            member_by_matter[e['from']['id']] = int(e['to']['id'])
            member_edge_evidence[e['from']['id']] = e.get('evidence') or {}

    # Raw member_of_mdl relationships straight from the AWS release, before slice 1's crosswalk
    # tries to resolve the parent matter to a JPML registry MDL. Some parents do not resolve (the
    # parent itself has no crosswalk entry), so their member matters are missing from
    # member_by_matter above even though the release does record a real relationship for them;
    # those matters must get a distinct reason, never the generic "no relationship" one.
    member_parent_by_matter = {}
    for rel in _read_jsonl_gz(IN_RELATIONSHIPS):
        if rel.get('relationship_type') == 'member_of_mdl' and rel.get('record_status') == 'active':
            member_parent_by_matter[rel['from_matter_id']] = rel['to_matter_id']

    # --- AWS release
    matters = list(_read_jsonl_gz(IN_MATTERS))
    alias_by_matter = collections.defaultdict(dict)
    # Every active courtlistener_docket_id / courtlistener_master_docket_id value seen for a
    # matter (a matter can carry more than one alias in either namespace). Used only to build the
    # catalog join index; the single "primary" id used for the record identity, court joins etc.
    # still comes from alias_by_matter above (last-active-alias-wins, unchanged).
    alias_ids_by_matter = collections.defaultdict(set)
    for a in _read_jsonl_gz(IN_ALIASES):
        if a.get('record_status') != 'active':
            continue
        alias_by_matter[a['matter_id']][a.get('namespace')] = a.get('value')
        if a.get('namespace') in ('courtlistener_docket_id', 'courtlistener_master_docket_id'):
            value = a.get('value')
            if value not in (None, ''):
                alias_ids_by_matter[a['matter_id']].add(int(value))

    judges_by_id = {j['judge_id']: j for j in _read_jsonl_gz(IN_JUDGES)}
    assignments_by_matter = collections.defaultdict(list)
    for a in _read_jsonl_gz(IN_ASSIGNMENTS):
        if a.get('record_status') == 'active':
            assignments_by_matter[a['matter_id']].append(a)

    # --- SW catalog
    catalog = json.load(open(IN_CATALOG_MATTERS, encoding='utf-8'))
    catalog_by_docket = {}
    for c in catalog:
        catalog_by_docket[int(c['docket_id'])] = c

    # Catalog join index: every courtlistener_docket_id/courtlistener_master_docket_id alias of a
    # matter (not just the single chosen primary id) maps to that matter, so a catalog docket that
    # matches ANY alias of a matter still joins to it. 18 matters in this release carry more than
    # one courtlistener_docket_id alias; keying the join on the one chosen cl_docket_id silently
    # dropped catalog rows whose docket id matched only the other alias.
    matter_id_by_alias_docket = {}
    for mid, ids in alias_ids_by_matter.items():
        for docket_id in ids:
            matter_id_by_alias_docket.setdefault(docket_id, mid)

    # --- cases
    cases = []
    edges = []
    seen_ids = set()
    catalog_used = set()

    for mt in matters:
        matter_id = mt['matter_id']
        aliases = alias_by_matter.get(matter_id, {})
        cl_docket_id = aliases.get('courtlistener_docket_id') or aliases.get('courtlistener_master_docket_id')
        cl_docket_basis = None
        if aliases.get('courtlistener_docket_id'):
            cl_docket_basis = 'matter_aliases.courtlistener_docket_id'
        elif aliases.get('courtlistener_master_docket_id'):
            cl_docket_basis = 'matter_aliases.courtlistener_master_docket_id'
        cl_docket_id = int(cl_docket_id) if cl_docket_id not in (None, '') else None

        if cl_docket_id is not None:
            rec_id = 'cl_docket:%d' % cl_docket_id
        else:
            rec_id = 'aws_matter:%s' % matter_id
            unresolved.append({
                'type': 'matter_without_cl_docket_id',
                'aws_matter_id': matter_id,
                'court_id': mt.get('court_id'),
                'docket_number': mt.get('docket_number'),
                'reason': 'no courtlistener_docket_id or courtlistener_master_docket_id alias in '
                          'matter_aliases.jsonl.gz; the record keeps its aws_matter id and joins '
                          'nothing on the CourtListener docket spine',
            })
        if rec_id in seen_ids:
            unresolved.append({
                'type': 'duplicate_record_id',
                'id': rec_id,
                'aws_matter_id': matter_id,
                'reason': 'two AWS matters resolve to the same CourtListener docket id; the second '
                          'is kept under its aws_matter id instead',
            })
            rec_id = 'aws_matter:%s' % matter_id
        seen_ids.add(rec_id)

        # Catalog join: try the primary chosen cl_docket_id first; if that does not match a
        # catalog row, try this matter's other courtlistener_docket_id/courtlistener_master_
        # docket_id aliases (sorted, for determinism) so a catalog docket that only matches an
        # alternate alias still joins instead of being reported as unmatched.
        all_alias_ids = alias_ids_by_matter.get(matter_id, set())
        cat = catalog_by_docket.get(cl_docket_id) if cl_docket_id is not None else None
        if cat is None:
            for other_id in sorted(all_alias_ids):
                if other_id == cl_docket_id:
                    continue
                if other_id in catalog_by_docket:
                    cat = catalog_by_docket[other_id]
                    break
        # Every catalog docket that matches ANY alias of this matter (not just the one chosen for
        # enrichment above) counts as joined: a matter can carry more than one
        # courtlistener_docket_id alias, and the catalog can carry a row under either one.
        for docket_id in all_alias_ids | ({cl_docket_id} if cl_docket_id is not None else set()):
            if docket_id in catalog_by_docket:
                catalog_used.add(docket_id)
        cl_docket_id_aliases = sorted(i for i in all_alias_ids if i != cl_docket_id)

        # court (exact CourtListener court id only)
        court_row, court_reason = resolve_court(mt.get('court_id'), spine_by_id)
        if court_row is None:
            unresolved.append({
                'type': 'court_not_in_spine', 'id': rec_id,
                'native_court_id': mt.get('court_id'), 'reason': court_reason,
            })
        court = {
            'native_court_id': mt.get('court_id'),
            'native_court_id_basis': '%s matters.court_id (CourtListener court id)' % RELEASE_LABEL,
            'court_spine_id': court_row['id'] if court_row else None,
            'court_spine_basis': ('exact CourtListener court id match against '
                                  'sources/court_spine_20260919/courts.jsonl') if court_row else None,
            'court_unlinked_reason': court_reason,
            'court_name': court_row['name'] if court_row else None,
            'court_short_name': court_row.get('short_name') if court_row else None,
            'state': court_row.get('state') if court_row else None,
            'system': court_row.get('system') if court_row else None,
            'jurisdiction_type': court_row.get('jurisdiction_type') if court_row else None,
        }
        if court_row is not None:
            edges.append({
                'from': {'type': 'cl_docket' if cl_docket_id else 'aws_matter',
                         'id': cl_docket_id if cl_docket_id else matter_id},
                'to': {'type': 'cl_court', 'id': court_row['id']},
                'relation': 'filed_in_court',
                'basis': '%s matters.court_id == court_spine cl_court id (exact native id match)' % RELEASE_LABEL,
                'evidence': {'aws_matter_id': matter_id, 'docket_number': mt.get('docket_number'),
                             'native_court_id': mt.get('court_id')},
            })

        # judges (assigned / referred), entity only via cl_person_id
        judges = []
        for a in sorted(assignments_by_matter.get(matter_id, []), key=lambda r: (r['role'], r['assignment_id'])):
            jrow = judges_by_id.get(a['judge_id'])
            native = jrow.get('native_judge_id') if jrow else None
            entity_id, ent_reason = resolve_judge_entity(native, entity_by_cl_person)
            if entity_id is None:
                unresolved.append({
                    'type': 'judge_entity_unbridged', 'id': rec_id,
                    'aws_judge_id': a['judge_id'], 'cl_person_id': native,
                    'reason': ent_reason,
                })
            judges.append({
                'role': a['role'],
                'aws_judge_id': a['judge_id'],
                'aws_assignment_id': a['assignment_id'],
                'cl_person_id': str(native) if native not in (None, '') else None,
                'cl_person_id_basis': ('AWS release judges.native_judge_id, written from the '
                                       'CourtListener people id (batch1_registry sources it as '
                                       'courtlistener_bulk_dockets)') if native else None,
                'name_as_recorded_by_source': jrow.get('name') if jrow else None,
                'name_note': 'display only; the name was never used to make any link',
                'judge_entity_id': entity_id,
                'judge_entity_basis': ('bridged on cl_person_id in '
                                       'sources/judge_structured_20260919/overlay.jsonl') if entity_id else None,
                'judge_entity_unlinked_reason': ent_reason,
            })
            if native:
                edges.append({
                    'from': {'type': 'cl_docket' if cl_docket_id else 'aws_matter',
                             'id': cl_docket_id if cl_docket_id else matter_id},
                    'to': {'type': 'cl_person', 'id': int(native) if str(native).isdigit() else str(native)},
                    'relation': a['role'],
                    'basis': '%s judicial_assignments.jsonl.gz -> judges.native_judge_id '
                             '(CourtListener person id)' % RELEASE_LABEL,
                    'evidence': {'assignment_id': a['assignment_id'], 'aws_judge_id': a['judge_id'],
                                 'aws_matter_id': matter_id},
                })
            if entity_id:
                edges.append({
                    'from': {'type': 'cl_docket' if cl_docket_id else 'aws_matter',
                             'id': cl_docket_id if cl_docket_id else matter_id},
                    'to': {'type': 'judge_entity', 'id': entity_id},
                    'relation': a['role'],
                    'basis': 'CourtListener person %s bridged to the MVP judge entity via '
                             'sources/judge_structured_20260919 overlay ids.cl_person_id' % native,
                    'evidence': {'assignment_id': a['assignment_id'], 'cl_person_id': str(native),
                                 'aws_matter_id': matter_id},
                })

        # MDL membership
        catalog_link = None
        if cat is not None and cat.get('mdl_master_docket_id') is not None:
            catalog_link = mdl_by_master_cl_docket.get(int(cat['mdl_master_docket_id']))
            if catalog_link is None:
                unresolved.append({
                    'type': 'catalog_mdl_master_not_resolvable', 'id': rec_id,
                    'mdl_master_docket_id': int(cat['mdl_master_docket_id']),
                    'reason': 'catalog/matters.json mdl_master_docket_id is not a master docket id '
                              'the slice-1 crosswalk resolves to a JPML registry MDL',
                })
        kind, mdl_number, evidence = classify_mdl_membership(
            matter_id, cl_docket_id, master_by_matter, member_by_matter, catalog_link)
        mdl_block = None
        mdl_unlinked_reason = None
        if kind is not None:
            reg = mdl_by_number.get(mdl_number) or {}
            mdl_block = {
                'mdl_number': mdl_number,
                'mdl_title': reg.get('title'),
                'mdl_status': reg.get('status'),
                'membership_kind': kind,
                'evidence': evidence,
            }
            if kind == 'member_of_mdl':
                mdl_block['member_edge_evidence'] = member_edge_evidence.get(matter_id)
            edges.append({
                'from': {'type': 'cl_docket' if cl_docket_id else 'aws_matter',
                         'id': cl_docket_id if cl_docket_id else matter_id},
                'to': {'type': 'mdl', 'id': mdl_number},
                'relation': kind,
                'basis': evidence,
                'evidence': {'aws_matter_id': matter_id, 'court_id': mt.get('court_id'),
                             'docket_number': mt.get('docket_number')},
            })
        else:
            parent_matter_id = member_parent_by_matter.get(matter_id)
            if parent_matter_id is not None:
                mdl_unlinked_reason = (
                    'the matter has a member_of_mdl relationship in the AWS release, but its '
                    'parent matter (%s) is not one the slice-1 crosswalk resolves to a JPML '
                    'registry MDL; not guessed' % parent_matter_id)
                unresolved.append({
                    'type': 'member_of_mdl_parent_not_resolvable', 'id': rec_id,
                    'aws_matter_id': matter_id, 'parent_aws_matter_id': parent_matter_id,
                    'court_id': mt.get('court_id'), 'docket_number': mt.get('docket_number'),
                    'reason': mdl_unlinked_reason,
                })
            else:
                mdl_unlinked_reason = evidence
                unresolved.append({
                    'type': 'no_mdl_membership', 'id': rec_id, 'aws_matter_id': matter_id,
                    'court_id': mt.get('court_id'), 'docket_number': mt.get('docket_number'),
                    'reason': evidence,
                })

        raw_caption = mt.get('case_name')
        displayable = is_displayable_caption(raw_caption)
        if not displayable and raw_caption and _PARTY_SEPARATOR_RE.search(str(raw_caption)):
            caption_reason = CAPTION_PARTY_SEPARATOR_REASON
        else:
            caption_reason = CAPTION_SUPPRESSION_REASON

        cases.append({
            'id': rec_id,
            'aws_matter_id': matter_id,
            'aws_matter_key': mt.get('matter_key'),
            'node_role': mt.get('node_role'),
            'record_status': mt.get('record_status'),
            'cl_docket_id': cl_docket_id,
            'cl_docket_id_basis': cl_docket_basis,
            'cl_docket_id_aliases': cl_docket_id_aliases,
            'cl_docket_id_aliases_basis': (
                'other active courtlistener_docket_id/courtlistener_master_docket_id values in '
                'matter_aliases.jsonl.gz for this same aws_matter_id, beyond the single id chosen '
                'above for the record identity; used only to join SW-BULK catalog/matters.json '
                'dockets that match an alternate alias'
                if cl_docket_id_aliases else None),
            'sw_catalog_docket_id': int(cat['docket_id']) if cat else None,
            # Built from the native docket id only. The slug form recorded by
            # catalog/matters.json spells the caption ("smith-v-acme-llc") and would republish a
            # natural-person party name that the caption rule suppresses, so it is never emitted.
            'courtlistener_docket_url': ('https://www.courtlistener.com/docket/%d/' % cl_docket_id
                                         if cl_docket_id else None),
            'courtlistener_docket_url_basis': ('constructed from the CourtListener docket id; the '
                                               'caption slug is deliberately omitted'),
            'docket_number': mt.get('docket_number'),
            'docket_number_catalog': cat.get('docket_number') if cat else None,
            'case_name': raw_caption if displayable else None,
            'case_name_suppressed_reason': None if displayable else caption_reason,
            'case_name_source': RELEASE_LABEL,
            'court': court,
            'dates': {
                'aws_date_filed': mt.get('date_filed'),
                'aws_date_filed_basis': '%s matters.date_filed (docket filing date)' % RELEASE_LABEL,
                'aws_date_terminated': mt.get('date_terminated'),
                'aws_date_terminated_basis': '%s matters.date_terminated' % RELEASE_LABEL,
                'catalog_date_filed': cat.get('date_filed') if cat else None,
                'catalog_date_terminated': cat.get('date_terminated') if cat else None,
                'catalog_dates_basis': CATALOG_LABEL if cat else None,
            },
            'filed_year': _year(mt.get('date_filed')),
            'status': {
                'aws_case_status': mt.get('case_status'),
                'catalog_status': cat.get('status') if cat else None,
                'basis': 'status labels as recorded by each source; not derived',
            },
            'nature_of_suit': None,
            'nature_of_suit_basis': NOS_BASIS,
            'judges': judges,
            'mdl': mdl_block,
            'mdl_unlinked_reason': mdl_unlinked_reason,
            'catalog_defendant': cat.get('defendant') if cat else None,
            'catalog_firms_text': (cat.get('firms') or []) if cat else [],
            'catalog_firms_note': ('free-text firm names from catalog/matters.json with no ids; '
                                   'never reconciled to canonical firm ids by name'),
            'catalog_judge_text': cat.get('judge') if cat else None,
            'catalog_judge_text_note': ('free text recorded by catalog/matters.json; displayed as '
                                        'text only and never used to create a judge link'),
            'catalog_roles': (cat.get('roles') or []) if cat else [],
            'temporal': {
                'captured_at': None,
                'captured_at_basis': 'the AWS release records no per-row capture timestamp',
                'source_as_of': '2026-08-24',
                'source_as_of_basis': 'release manifest built_at for b2b-cdbb8d040b95c7b65cfc',
                'published_at': None, 'published_at_basis': None,
                'effective_from': None, 'effective_from_basis': None,
                'effective_to': None, 'effective_to_basis': None,
            },
        })

    # catalog dockets that never reached an AWS matter
    for docket_id, cat in sorted(catalog_by_docket.items()):
        if docket_id in catalog_used:
            continue
        unresolved.append({
            'type': 'catalog_docket_without_aws_matter',
            'sw_catalog_docket_id': docket_id,
            'court': cat.get('court'),
            'docket_number': cat.get('docket_number'),
            'reason': 'catalog/matters.json docket_id has no matching courtlistener_docket_id alias '
                      'in the AWS release, so it carries no matter row here',
        })

    cases.sort(key=lambda r: (r['court']['native_court_id'] or '', r['docket_number'] or '', r['id']))
    edges.sort(key=lambda e: (e['relation'], str(e['from']['id']), e['to']['type'], str(e['to']['id'])))
    unresolved.sort(key=lambda r: (r['type'], str(r.get('id') or r.get('sw_catalog_docket_id') or '')))

    # --- per-MDL coverage, recomputed from the emitted rows
    per_mdl = collections.defaultdict(lambda: {'cases': 0, 'by_court': collections.Counter(),
                                               'by_year': collections.Counter(),
                                               'by_status': collections.Counter()})
    for r in cases:
        if not r['mdl']:
            continue
        b = per_mdl[r['mdl']['mdl_number']]
        b['cases'] += 1
        b['by_court'][r['court']['court_spine_id'] or 'unlinked'] += 1
        b['by_year'][str(r['filed_year']) if r['filed_year'] else 'unknown'] += 1
        b['by_status'][r['status']['aws_case_status'] or 'unknown'] += 1

    coverage = {}
    for num, b in sorted(per_mdl.items()):
        reg = mdl_by_number.get(num) or {}
        pending = reg.get('actions_pending')
        total = reg.get('total_actions')
        coverage['mdl:%d' % num] = {
            'mdl_number': num,
            'mdl_title': reg.get('title'),
            'mdl_status': reg.get('status'),
            'cases_in_this_corpus': b['cases'],
            'jpml_actions_pending': pending,
            'jpml_total_actions': total,
            'jpml_counts_label': reg.get('counts_label'),
            'by_court': dict(sorted(b['by_court'].items())),
            'by_filed_year': dict(sorted(b['by_year'].items())),
            'by_status': dict(sorted(b['by_status'].items())),
            'note': ('Cases held in this corpus are a firm-focused docket sample, not the MDL\'s '
                     'full member-case list. The JPML counts are the JPML report\'s own figures.'),
        }

    _write_jsonl(OUT_CASES, cases)
    _write_jsonl(OUT_EDGES, edges)
    _write_jsonl(OUT_UNRESOLVED, unresolved)
    with open(OUT_COVERAGE, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(coverage, f, ensure_ascii=False, indent=1, sort_keys=True)

    # --- counts recomputed from rows
    node_roles = collections.Counter(r['node_role'] for r in cases)
    rel_counts = collections.Counter(e['relation'] for e in edges)
    to_types = collections.Counter(e['to']['type'] for e in edges)
    courts_linked = {r['court']['court_spine_id'] for r in cases if r['court']['court_spine_id']}
    membership = collections.Counter(r['mdl']['membership_kind'] for r in cases if r['mdl'])
    cat_with_master = sum(1 for c in catalog if c.get('mdl_master_docket_id') is not None)
    unresolved_types = collections.Counter(u['type'] for u in unresolved)
    member_parent_unresolvable_parents = {u['parent_aws_matter_id'] for u in unresolved
                                          if u['type'] == 'member_of_mdl_parent_not_resolvable'}
    member_parent_unresolvable_matters = {u['aws_matter_id'] for u in unresolved
                                          if u['type'] == 'member_of_mdl_parent_not_resolvable'}

    counts = {
        'cases_rows': len(cases),
        'aws_matters_total': len(matters),
        'node_role_public_docket': node_roles.get('public_docket', 0),
        'node_role_target_matter': node_roles.get('target_matter', 0),
        'node_role_support_master': node_roles.get('support_master', 0),
        'cases_with_cl_docket_id': sum(1 for r in cases if r['cl_docket_id']),
        'cases_without_cl_docket_id': sum(1 for r in cases if not r['cl_docket_id']),
        'catalog_dockets_total': len(catalog),
        'catalog_dockets_with_mdl_master_docket_id': cat_with_master,
        'catalog_dockets_joined_to_a_matter': len(catalog_used),
        'catalog_dockets_without_a_matter': len(catalog) - len(catalog_used),
        'cases_with_catalog_row': sum(1 for r in cases if r['sw_catalog_docket_id']),
        'cases_with_mdl': sum(1 for r in cases if r['mdl']),
        'cases_without_mdl': sum(1 for r in cases if not r['mdl']),
        'mdl_membership_master_docket_of_mdl': membership.get('master_docket_of_mdl', 0),
        'mdl_membership_member_of_mdl': membership.get('member_of_mdl', 0),
        'mdl_membership_catalog_mdl_master_docket_id': membership.get('catalog_mdl_master_docket_id', 0),
        'distinct_mdls_covered': len(per_mdl),
        'distinct_courts_linked': len(courts_linked),
        'cases_with_unlinked_court': sum(1 for r in cases if not r['court']['court_spine_id']),
        'judicial_assignment_rows': sum(len(r['judges']) for r in cases),
        'judicial_assignments_source_rows': sum(len(v) for v in assignments_by_matter.values()),
        'aws_judges_total': len(judges_by_id),
        'cases_with_at_least_one_judge': sum(1 for r in cases if r['judges']),
        'judge_links_bridged_to_entity': sum(1 for r in cases for j in r['judges'] if j['judge_entity_id']),
        'judge_links_unbridged': sum(1 for r in cases for j in r['judges'] if not j['judge_entity_id']),
        'distinct_cl_person_ids': len({j['cl_person_id'] for r in cases for j in r['judges']
                                       if j['cl_person_id']}),
        'distinct_judge_entities': len({j['judge_entity_id'] for r in cases for j in r['judges']
                                        if j['judge_entity_id']}),
        'graph_edges_rows': len(edges),
        'edges_filed_in_court': rel_counts.get('filed_in_court', 0),
        'edges_assigned_judge': rel_counts.get('assigned_judge', 0),
        'edges_referred_judge': rel_counts.get('referred_judge', 0),
        'edges_master_docket_of_mdl': rel_counts.get('master_docket_of_mdl', 0),
        'edges_member_of_mdl': rel_counts.get('member_of_mdl', 0),
        'edges_catalog_mdl_master_docket_id': rel_counts.get('catalog_mdl_master_docket_id', 0),
        'edges_to_cl_court': to_types.get('cl_court', 0),
        'edges_to_cl_person': to_types.get('cl_person', 0),
        'edges_to_judge_entity': to_types.get('judge_entity', 0),
        'edges_to_mdl': to_types.get('mdl', 0),
        'unresolved_rows': len(unresolved),
        'unresolved_no_mdl_membership': unresolved_types.get('no_mdl_membership', 0),
        'unresolved_member_of_mdl_parent_not_resolvable':
            unresolved_types.get('member_of_mdl_parent_not_resolvable', 0),
        'member_of_mdl_parent_not_resolvable_distinct_parents': len(member_parent_unresolvable_parents),
        'crosswalk_master_matters': len(master_by_matter),
        'crosswalk_member_edges': len(member_by_matter),
    }

    checks = [
        {'name': 'aws_matters_total_4159', 'passed': len(matters) == 4159},
        {'name': 'exactly_three_node_roles',
         'passed': set(node_roles) == {'public_docket', 'target_matter', 'support_master'}},
        {'name': 'node_role_counts_4086_57_16',
         'passed': (node_roles.get('public_docket'), node_roles.get('target_matter'),
                    node_roles.get('support_master')) == (4086, 57, 16)},
        {'name': 'catalog_matters_2122_with_977_mdl_master_links',
         'passed': len(catalog) == 2122 and cat_with_master == 977},
        {'name': 'catalog_matters_2122_all_joined_to_a_matter',
         'passed': (counts['catalog_dockets_joined_to_a_matter'] == 2122
                    and counts['catalog_dockets_without_a_matter'] == 0)},
        {'name': 'record_ids_unique', 'passed': len({r['id'] for r in cases}) == len(cases)},
        {'name': 'no_party_style_caption_published',
         'passed': all(_PARTY_SEPARATOR_RE.search(r['case_name']) is None for r in cases
                       if r['case_name'] is not None)},
        {'name': 'every_mdl_link_has_evidence',
         'passed': all(r['mdl']['evidence'] for r in cases if r['mdl'])},
        {'name': 'judge_entity_only_via_cl_person_id',
         'passed': all(j['cl_person_id'] for r in cases for j in r['judges'] if j['judge_entity_id'])},
        {'name': 'rollups_recomputed_from_rows',
         'passed': counts['cases_with_mdl'] == sum(v['cases_in_this_corpus'] for v in coverage.values())},
        {'name': 'no_fraction_or_rate_key_emitted',
         'passed': not any('fraction' in k.lower() or 'rate' in k.lower()
                           for v in coverage.values() for k in v)},
        {'name': 'member_of_mdl_relationships_all_accounted_for',
         'passed': all(mid in member_by_matter or mid in member_parent_unresolvable_matters
                       for mid in member_parent_by_matter)},
    ]

    sample_lines = []
    for key in sorted(coverage, key=lambda k: -coverage[k]['cases_in_this_corpus'])[:5]:
        v = coverage[key]
        if v['jpml_actions_pending']:
            sample_lines.append('MDL %d: %d of %d actions pending per the JPML report'
                                % (v['mdl_number'], v['cases_in_this_corpus'], v['jpml_actions_pending']))

    qualification = (
        'Local personal-testing view; derived from a private firm dataset (SW-BULK) built from '
        'CourtListener/RECAP API data; not for redistribution. Link out to CourtListener for dockets; '
        'no RECAP bytes are re-hosted. '
        'THIS IS A FIRM-FOCUSED DOCKET SAMPLE, NOT AN MDL MEMBER-CASE LIST: the %d matters are the '
        'dockets the firm\'s own collection covers, so a per-MDL count here is a count of what this '
        'corpus holds as of the 2026-08-24 release build, never the MDL\'s size. Against the JPML '
        'report dated 2026-09-01 the sample is small — e.g. %s. %d of %d matters carry an MDL link '
        '(%d master dockets, %d member_of_mdl edges, %d via a catalog mdl_master_docket_id); the other '
        '%d have none and are listed with a reason, never guessed. Courts join by exact CourtListener '
        'court id; judges reach an MVP judge entity only via cl_person_id (%d of %d assignment rows '
        'bridge). Nature of suit is absent from both sources and is left null. Every rollup here is '
        'recomputed from the emitted rows; no matter_graph or firms.csv rollup was reused.'
        % (len(cases), '; '.join(sample_lines) or 'no JPML denominators available',
           counts['cases_with_mdl'], len(cases),
           counts['mdl_membership_master_docket_of_mdl'],
           counts['mdl_membership_member_of_mdl'],
           counts['mdl_membership_catalog_mdl_master_docket_id'],
           counts['cases_without_mdl'],
           counts['judge_links_bridged_to_entity'], counts['judicial_assignment_rows']))

    qualification_short = (
        'Firm-focused docket sample from a private firm dataset (SW-BULK) built from '
        'CourtListener/RECAP data, as of the 2026-08-24 release build; not for redistribution. '
        'Counts here are what this corpus holds, never the MDL\'s size -- the JPML report\'s own '
        'counts are shown beside them. Links go out to CourtListener; no RECAP bytes are re-hosted. '
        'Rows that do not join stay listed with their reason.')
    assert len(qualification_short) <= 500, len(qualification_short)

    data_files = []
    for path, rows in ((OUT_CASES, len(cases)), (OUT_EDGES, len(edges)),
                       (OUT_UNRESOLVED, len(unresolved)), (OUT_COVERAGE, len(coverage))):
        data_files.append({'path': os.path.basename(path), 'sha256': _sha256_file(path), 'rows': rows})

    inputs = []
    for p in (IN_MATTERS, IN_ALIASES, IN_ASSIGNMENTS, IN_JUDGES, IN_RELATIONSHIPS, IN_MANIFEST,
              IN_CATALOG_MATTERS, IN_CROSSWALK, IN_CROSSWALK_EDGES, IN_MDLS, IN_COURTS, IN_OVERLAY):
        inputs.append({'path': p, 'sha256': _sha256_file(p)})

    validation = {
        'schema_version': '1',
        'status': 'passed' if all(c['passed'] for c in checks) else 'failed',
        'ready': all(c['passed'] for c in checks),
        'validated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'data_files': data_files,
        'counts': counts,
        'checks': checks,
        'qualification': qualification,
        'qualification_short': qualification_short,
        'license_ref': 'sw_bulk_private_firm_work_product',
        'export_allowed': False,
        'inputs': inputs,
    }
    with open(OUT_VALIDATION, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(validation, f, ensure_ascii=False, indent=2, sort_keys=True)
    return validation


if __name__ == '__main__':
    v = build()
    print(json.dumps({'status': v['status'], 'counts': v['counts'],
                      'checks': [c for c in v['checks'] if not c['passed']]}, indent=1))
