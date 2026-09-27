"""Offline builder: law-tier classification, rule-set typing, mass-tort topic index and jurisdiction coverage.

Re-runnable, read-only against every existing database (SQLite opened with mode=ro), no network.
Usage: python build.py [labels] [rules] [topics] [coverage] [finalize]   (no argument = all stages, in order)
Outputs (this folder): record_labels.jsonl, oul_rule_sets.jsonl, topics.json, topic_index.jsonl, coverage.json,
edges.jsonl, unresolved.jsonl, validation.json, build_report.json.
"""
from __future__ import annotations

import collections
import datetime
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import classify as cl  # noqa: E402

DIRECTORY_DB = ROOT / 'delivery/archive-directory/directory.sqlite3'
OUL_DB = ROOT / 'sources/open_us_law_20260918/catalog.sqlite3'
TRELLIS_DB = ROOT / 'sources/trellis/catalog/catalog.sqlite3'
MATRIX = ROOT / 'reports/corpus_upgrade_20260919/understand/coverage_matrix.json'
TOPIC_LABEL = 'search-derived candidates, not legal advice or a complete survey'
REVIEW_KINDS = ('needs_content_review', 'law_document_title_evidence_needs_review')
BODY_KINDS = {'law_chapter_body', 'verified_law_document_derivative', 'legal_text_fragment', 'captured_law_representation',
              'law_text_fragment', 'observed_comprehensive_constitution'}
NAV_KINDS = {'legal_inventory_navigation'}
BODY_CLASSES = ('statute_body', 'constitution_body', 'regulation_body', 'court_rule_body')
FAMILIES = ('statutes', 'constitution', 'regulations', 'court_rules')
OUL_FAMILY = {'statutes': 'statutes', 'constitutions': 'constitution', 'regulations': 'regulations', 'court_rules': 'court_rules'}

STATES = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT',
    'Delaware': 'DE', 'District of Columbia': 'DC', 'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID', 'Illinois': 'IL',
    'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
    'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS', 'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE',
    'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC',
    'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK', 'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI',
    'South Carolina': 'SC', 'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT', 'Virginia': 'VA',
    'Washington': 'WA', 'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY'}
TERRITORIES = {'Puerto Rico': 'PR', 'Guam': 'GU', 'Virgin Islands': 'VI', 'Northern Mariana Islands': 'MP', 'American Samoa': 'AS'}
NAME2AB = dict(STATES, **TERRITORIES)
AB2NAME = {v: k for k, v in NAME2AB.items()}

# Topic catalogue. Queries marked saved=True are copied verbatim from coverage_matrix.json -> topic_gaps_open_us_law.
TOPICS = collections.OrderedDict([
    ('sol', {'label': 'Statute of limitations', 'group': 'statute_of_limitations', 'queries': ['sol_any', 'sol_personal_injury']}),
    ('repose', {'label': 'Statute of repose', 'group': 'statute_of_repose', 'queries': ['repose']}),
    ('product_liability', {'label': 'Product liability', 'group': 'product_liability', 'queries': ['product_liability']}),
    ('comparative_fault', {'label': 'Comparative / contributory fault', 'group': 'comparative_fault_joint_several', 'queries': ['comparative_fault']}),
    ('joint_several', {'label': 'Joint and several liability', 'group': 'comparative_fault_joint_several', 'queries': ['joint_several']}),
    ('damages_cap', {'label': 'Damages caps (noneconomic / punitive limits)', 'group': 'damages_caps_punitive', 'queries': ['damages_caps']}),
    ('punitive', {'label': 'Punitive / exemplary damages', 'group': 'damages_caps_punitive', 'queries': ['punitive_derived']}),
    ('udap', {'label': 'UDAP / consumer protection', 'group': 'udap_consumer_protection', 'queries': ['udap']}),
    ('wrongful_death_survival', {'label': 'Wrongful death and survival', 'group': 'wrongful_death_survival', 'queries': ['wrongful_death_survival']}),
    ('class_action', {'label': 'Class actions', 'group': 'class_action', 'queries': ['class_action']}),
    ('complex_coordination', {'label': 'Complex / coordinated litigation', 'group': 'complex_coordinated_litigation', 'queries': ['complex_coordination']}),
    ('medical_monitoring', {'label': 'Medical monitoring', 'group': 'medical_monitoring', 'queries': ['medical_monitoring']}),
    ('expert_evidence', {'label': 'Expert evidence', 'group': 'expert_evidence', 'queries': ['expert_evidence']}),
])
DERIVED_QUERIES = {'punitive_derived': '"punitive damages" OR "exemplary damages"'}  # subset of the saved damages_any query


def ro(path):
    return sqlite3.connect('file:' + Path(path).as_posix() + '?mode=ro', uri=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl(path, rows):
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')


def read_jsonl(path):
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def temporal(captured_at=None, captured_basis=None, as_of=None, as_of_basis=None):
    return {'captured_at': captured_at, 'captured_at_basis': captured_basis if captured_at else None,
            'source_as_of': as_of, 'source_as_of_basis': as_of_basis if as_of else None,
            'published_at': None, 'published_at_basis': None, 'effective_from': None, 'effective_from_basis': None,
            'effective_to': None, 'effective_to_basis': None}


def category_tokens(category):
    return [t.strip() for t in (category or '').split(';') if t.strip()]


def families_of(tokens):
    out = []
    if 'statutes' in tokens: out.append('statutes')
    if 'constitution' in tokens: out.append('constitution')
    if 'administrative_code' in tokens: out.append('regulations')
    if 'court_rules' in tokens: out.append('court_rules')
    return out


REV_STAMP = re.compile(r'\[Rev\.\s+(\d{1,2})/(\d{1,2})/(\d{4})')
WI_STAMP = re.compile(r'Published and certified under s\.\s*35\.18\.\s+([A-Z][a-z]+) (\d{1,2}), (\d{4})')
MONTHS = {m: i + 1 for i, m in enumerate('January February March April May June July August September October November December'.split())}


def publisher_stamp(text):
    """Publisher revision stamp printed on the page (never an effective date)."""
    head = text[:800]
    hit = REV_STAMP.search(head)
    if hit:
        try: return datetime.date(int(hit.group(3)), int(hit.group(1)), int(hit.group(2))).isoformat(), 'publisher revision stamp "[Rev. ...]" printed on the saved page; not an effective date'
        except ValueError: return None, None
    hit = WI_STAMP.search(head)
    if hit and hit.group(1) in MONTHS:
        try: return datetime.date(int(hit.group(3)), MONTHS[hit.group(1)], int(hit.group(2))).isoformat(), 'publisher "Published and certified under s. 35.18" date printed on the saved page; not an effective date'
        except ValueError: return None, None
    return None, None


# --------------------------------------------------------------------------------------------- stage: labels
def stage_labels():
    db = ro(DIRECTORY_DB)
    sql = ("select id,title,state,kind,source_url,payload from records where dataset='focused' and group_name='laws' and "
           "(kind like '%needs_content_review%' or kind like '%law_document_title_evidence%') order by id")
    rows, unresolved, missing = [], [], 0
    for rid, title, state, kind, url, payload in db.execute(sql):
        p = json.loads(payload)
        tokens = category_tokens(p.get('category'))
        raw_path = p.get('raw_path') or ''
        ext = raw_path.rsplit('.', 1)[-1].lower() if '.' in raw_path else ''
        fmt = 'html' if ext in ('html', 'htm') else ('pdf' if ext == 'pdf' else (ext or 'unknown'))
        text = ''
        text_path = p.get('text_path')
        if text_path and (ROOT / text_path).is_file():
            text = (ROOT / text_path).read_text(encoding='utf-8', errors='replace')
        else:
            missing += 1
        feats = cl.text_features(text)
        if fmt == 'html' and raw_path and (ROOT / raw_path).is_file():
            feats.update(cl.html_link_features((ROOT / raw_path).read_bytes()))
        label = cl.classify(feats, text, tokens, title, url, fmt)
        rule_set = rule_detail = rule_basis = None
        if 'court_rules' in tokens:
            heading = ' '.join(cl.PAGE_MARK.sub(' ', text)[:300].split())
            rule_set, rule_detail, rule_basis = cl.rule_set_type([('title', title), ('source_url', url), ('text_heading', heading)])
        abbr = NAME2AB.get(state or '')
        as_of, as_of_basis = publisher_stamp(text)
        row = {
            'record_id': rid, 'state': abbr, 'state_name': state or None, 'title': title, 'source_url': url,
            'categories': tokens, 'families': families_of(tokens), 'format': fmt, 'prior_kind': kind,
            'ocr_text': 'ocr_text' in tokens or '/ocr' in (p.get('collection') or ''),
            **label, 'rule_set': rule_set, 'rule_set_detail': rule_detail, 'rule_set_basis': rule_basis,
            'features': {k: feats.get(k) for k in ('text_chars', 'function_ratio', 'modal_per_k', 'section_markers', 'subsection_markers',
                                                   'toc_entries', 'history_notes', 'nav_tokens', 'blank_runs', 'links', 'link_ratio',
                                                   'nonlink_chars', 'nonlink_function_ratio')},
            'text_sha256': p.get('text_file_sha256'), 'original_sha256': p.get('raw_sha256'),
            'temporal': temporal(p.get('retrieved_at'), 'collector retrieved_at (%s)' % (p.get('retrieval_time_basis') or 'basis not recorded'),
                                 as_of, as_of_basis),
        }
        rows.append(row)
        if abbr is None:
            unresolved.append({'from': {'type': 'record', 'id': rid}, 'wanted': 'state',
                               'reason': 'directory record has no single recognised state in its explicit state field (%r); jurisdiction is never inferred from the URL' % (state or '')})
    write_jsonl(HERE / 'record_labels.jsonl', rows)
    write_jsonl(HERE / '_unresolved_labels.jsonl', unresolved)
    counts = collections.Counter(r['law_body_class'] for r in rows)
    print('labels', len(rows), dict(counts), 'text missing', missing)
    return rows


# --------------------------------------------------------------------------------------------- stage: rules
def oul_value(value):
    """Open US Law payload scalar with the exporter's textual nulls ('None', 'null', 'nan', '') removed."""
    if value is None: return None
    text = str(value).strip()
    return None if text in ('', 'None', 'null', 'nan', 'NaN') else text


def stage_rules():
    db = ro(OUL_DB)
    files = [r[0] for r in db.execute("select id from files where kind='court_rules' order by id")]
    out, untyped, total = [], collections.Counter(), 0
    for file_id in files:
        for rid, state, title, citation, payload in db.execute('select id,state,title,citation,payload from records where file_id=? order by row_index', (file_id,)):
            total += 1
            p = json.loads(payload)
            crumbs = p.get('breadcrumb')
            try: crumbs = json.loads(crumbs) if isinstance(crumbs, str) else (crumbs or [])
            except ValueError: crumbs = []
            if not isinstance(crumbs, list): crumbs = []
            # Open US Law exports missing values as the literal strings 'None' / 'null'; treat them as absent.
            fields = [('chapter_name', oul_value(p.get('chapter_name'))), ('title_name', oul_value(p.get('title_name'))),
                      ('breadcrumb', ' / '.join(str(c) for c in crumbs[:-1] if oul_value(c))), ('citation', oul_value(citation))]
            rule_set, detail, basis = cl.rule_set_type(fields)
            if rule_set is None:
                untyped[(state, p.get('title_name') or '', p.get('chapter_name') or '')] += 1
                continue
            out.append({'id': rid, 'state': state, 'rule_set': rule_set, 'detail': detail, 'basis': basis})
    write_jsonl(HERE / 'oul_rule_sets.jsonl', out)
    report = {'court_rule_rows': total, 'typed_rows': len(out), 'untyped_rows': total - len(out),
              'untyped_sets': [{'state': k[0], 'title_name': k[1], 'chapter_name': k[2], 'rows': v} for k, v in untyped.most_common()]}
    (HERE / '_rules_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('rules', total, 'typed', len(out), collections.Counter(r['rule_set'] for r in out))
    return out


# --------------------------------------------------------------------------------------------- stage: topics
def phrases_of(query):
    return [p.lower() for p in re.findall(r'"([^"]+)"', query)]


def stage_topics():
    matrix = json.loads(MATRIX.read_text(encoding='utf-8'))
    saved = {k: v['query'] for k, v in matrix['topic_gaps_open_us_law'].items()}
    queries = dict(saved, **DERIVED_QUERIES)
    labels = {r['record_id']: r for r in read_jsonl(HERE / 'record_labels.jsonl')}
    rule_sets = {r['id']: r['rule_set'] for r in read_jsonl(HERE / 'oul_rule_sets.jsonl')}
    oul = ro(OUL_DB)
    meta = {k: json.loads(v) for k, v in oul.execute('select key,value from import_meta')}
    oul_temporal = temporal(meta.get('completed_at'), 'local import of the Open US Law snapshot completed (not a retrieval from the official publisher)',
                            meta.get('snapshot_date'), 'Open US Law publisher snapshot date, release %s' % meta.get('snapshot'))
    provisions, timings = {}, {}

    def tag(row, topic, query_id, title):
        phrases = phrases_of(queries[query_id])
        match = 'title' if any(p in (title or '').lower() for p in phrases) else 'text'
        for existing in row['topics']:
            if existing['topic'] == topic:
                if match == 'title': existing['match'] = 'title'
                existing['query_ids'] = sorted(set(existing['query_ids']) | {query_id})
                return
        row['topics'].append({'topic': topic, 'match': match, 'query_ids': [query_id]})

    for topic, spec in TOPICS.items():
        for query_id in spec['queries']:
            started = time.time()
            sql = ('select r.id,r.state,r.kind,r.title,r.citation,r.source_url,r.status,r.payload from records_fts f join records r on r.rowid=f.rowid '
                   'where records_fts match ?')
            hits = 0
            for rid, state, kind, title, citation, url, status, payload in oul.execute(sql, (queries[query_id],)):
                family = OUL_FAMILY.get(kind)
                if family is None or state not in AB2NAME: continue
                hits += 1
                key = 'open_us_law_row:' + rid
                row = provisions.get(key)
                if row is None:
                    try: year = json.loads(payload).get('last_amended_year')
                    except ValueError: year = None
                    row = provisions[key] = {
                        'provision_id': key, 'source_dataset': 'open_us_law', 'source_tier': 'third_party_snapshot', 'row_id': rid,
                        'state': state, 'family': family, 'citation': citation or None, 'title': ' '.join((title or '').split())[:300],
                        'source_url': url or None, 'publisher_status': status or None,
                        'publisher_status_basis': 'Open US Law act_status field; a publisher assertion, not verified' if status else None,
                        'publisher_last_amended_year': year, 'rule_set': rule_sets.get(rid) if family == 'court_rules' else None,
                        'record_class': None, 'topics': [], 'temporal': oul_temporal}
                tag(row, topic, query_id, title)
            timings['open_us_law:' + query_id] = {'hits_state_law_rows': hits, 'seconds': round(time.time() - started, 1)}
            print('topic', topic, query_id, 'oul', hits, timings['open_us_law:' + query_id]['seconds'], flush=True)

    directory = ro(DIRECTORY_DB)
    for topic, spec in TOPICS.items():
        for query_id in spec['queries']:
            ids = [r[0] for r in directory.execute('select id from search where search match ?', (queries[query_id],))]
            hits = 0
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ','.join('?' * len(chunk))
                for rid, title, state, dataset, kind, url, payload in directory.execute(
                        "select id,title,state,dataset,kind,source_url,payload from records where group_name='laws' and dataset in ('focused','seeger') and id in (%s)" % marks, chunk):
                    abbr = NAME2AB.get(state or '')
                    if abbr is None: continue
                    p = json.loads(payload)
                    kinds = {k.strip() for k in kind.split(';')}
                    if dataset == 'focused':
                        label = labels.get(rid)
                        if kinds & BODY_KINDS: record_class, families = 'published_body_tier', families_of(category_tokens(p.get('category')))
                        elif label and label['law_body_class'] in BODY_CLASSES and label['confidence'] in ('high', 'medium'):
                            record_class, families = label['law_body_class'], label['families']
                        else: continue
                        citation, tier, source = None, 'official_capture', 'directory_focused'
                        captured, basis = p.get('retrieved_at'), 'collector retrieved_at (%s)' % (p.get('retrieval_time_basis') or 'basis not recorded')
                    else:
                        if kind == 'statutory_provision': families = ['statutes']
                        elif kind == 'court_rule_or_order': families = ['court_rules']
                        else: continue
                        record_class, tier, source = kind, 'imported_collection', 'directory_imported'
                        citation = (p.get('metadata') or {}).get('citation')
                        captured, basis = p.get('captured_at'), 'import metadata captured_at (date the imported collection saved the source)'
                    hits += 1
                    key = 'record:' + rid
                    row = provisions.get(key)
                    if row is None:
                        row = provisions[key] = {
                            'provision_id': key, 'source_dataset': source, 'source_tier': tier, 'record_id': rid, 'state': abbr,
                            'family': families[0] if families else None, 'citation': citation,
                            'citation_basis': 'citation field of the imported structured provision' if citation else None,
                            'unit': 'provision' if citation else 'document (chapter- or page-level saved text; locate the provision inside it)',
                            'title': ' '.join((title or '').split())[:300], 'source_url': url or None, 'publisher_status': None,
                            'record_class': record_class, 'rule_set': (labels.get(rid) or {}).get('rule_set'), 'topics': [],
                            'temporal': temporal(captured, basis)}
                    tag(row, topic, query_id, title)
            timings['directory:' + query_id] = {'hits_law_body_records': hits}
            print('topic', topic, query_id, 'directory', hits, flush=True)

    rows = sorted(provisions.values(), key=lambda r: (r['state'], r['family'] or '', r['citation'] or '', r['title'], r['provision_id']))
    for row in rows:
        for t in row['topics']:
            t['query_id'] = t['query_ids'][0]
    write_jsonl(HERE / 'topic_index.jsonl', rows)
    catalog = {'label': TOPIC_LABEL,
               'method': 'FTS5 phrase queries over the Open US Law index (title, citation, text) and the archive directory search index (law bodies only). '
                         'match="title" means a query phrase also occurs in the provision title; otherwise the phrase occurs in the text.',
               'caveats': ['Zero hits means the phrase set was not found (terminology differs by state, e.g. Louisiana prescription/peremption); it does not prove absence of law.',
                           'Open US Law rows are third-party structured reproductions (snapshot %s, publisher date %s); in-force flags are publisher assertions.' % (meta.get('snapshot'), meta.get('snapshot_date')),
                           'Official and imported directory hits are document-level unless a citation is present.',
                           'Medical monitoring and complex-litigation programmes are largely case-law or administrative-order doctrine; statutory hits are incidental.'],
               'topics': {k: {'label': v['label'], 'group': v['group'],
                              'queries': {q: queries[q] for q in v['queries']},
                              'query_provenance': {q: ('saved verbatim in coverage_matrix.json topic_gaps_open_us_law' if q in saved else 'derived: punitive/exemplary subset of the saved damages_any query') for q in v['queries']}}
                          for k, v in TOPICS.items()}}
    (HERE / 'topics.json').write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding='utf-8')
    (HERE / '_topics_report.json').write_text(json.dumps(timings, indent=1), encoding='utf-8')
    print('topic provisions', len(rows))
    return rows


# --------------------------------------------------------------------------------------------- stage: coverage
def empty_family():
    return {'official_capture': {'total': 0, 'body': 0, 'unreviewed': 0, 'navigation': 0, 'other': 0, 'unreviewed_classified_as': {}},
            'imported_collection': 0, 'third_party_snapshot': 0, 'pending_publication': 0}


def stage_coverage():
    matrix = json.loads(MATRIX.read_text(encoding='utf-8'))
    labels = {r['record_id']: r for r in read_jsonl(HERE / 'record_labels.jsonl')}
    rule_rows = read_jsonl(HERE / 'oul_rule_sets.jsonl')
    J = collections.OrderedDict()
    for name, abbr in list(sorted(STATES.items())) + list(sorted(TERRITORIES.items())):
        J[abbr] = {'name': name, 'abbr': abbr, 'jurisdiction_kind': 'territory' if name in TERRITORIES else ('federal_district' if abbr == 'DC' else 'state'),
                   'families': {f: empty_family() for f in FAMILIES},
                   'imported_forms_and_documents': 0, 'imported_other': 0, 'third_party_page_captures': {'trellis_state_rule_pages': 0},
                   'open_us_law_other_rows': {}, 'reviewed_tier': {}, 'open_us_law_rule_sets': {}, 'gaps': []}
    db = ro(DIRECTORY_DB)
    unplaced = collections.Counter()
    for rid, dataset, kind, state, payload in db.execute(
            "select id,dataset,kind,state,payload from records where group_name='laws' and dataset in ('focused','seeger','pending_publication')"):
        abbr = NAME2AB.get(state or '')
        if abbr is None:
            unplaced['%s/%s' % (dataset, state or '(blank)')] += 1
            continue
        row = J[abbr]
        if dataset == 'focused':
            tokens = category_tokens(json.loads(payload).get('category'))
            kinds = {k.strip() for k in kind.split(';')}
            tier = 'body' if kinds & BODY_KINDS else ('navigation' if kinds & NAV_KINDS else ('unreviewed' if kinds & set(REVIEW_KINDS) else 'other'))
            if 'state_rule' in tokens: row['third_party_page_captures']['trellis_state_rule_pages'] += 1
            for family in families_of(tokens):
                cell = row['families'][family]['official_capture']
                cell['total'] += 1
                cell[tier] += 1
                if tier == 'unreviewed' and rid in labels:
                    cls = labels[rid]['law_body_class']
                    cell['unreviewed_classified_as'][cls] = cell['unreviewed_classified_as'].get(cls, 0) + 1
            if tier == 'unreviewed' and rid in labels:
                cls = labels[rid]['law_body_class']
                row['reviewed_tier'][cls] = row['reviewed_tier'].get(cls, 0) + 1
        elif dataset == 'seeger':
            if kind == 'statutory_provision': row['families']['statutes']['imported_collection'] += 1
            elif kind == 'court_rule_or_order': row['families']['court_rules']['imported_collection'] += 1
            elif kind == 'court_form_or_other_document': row['imported_forms_and_documents'] += 1
            else: row['imported_other'] += 1
        else:
            family = kind if kind in FAMILIES else None
            if family: row['families'][family]['pending_publication'] += 1
    oul = ro(OUL_DB)
    for state, kind, n in oul.execute('select state,kind,sum(rows) from files group by 1,2'):
        if state not in J: continue
        family = OUL_FAMILY.get(kind)
        if family: J[state]['families'][family]['third_party_snapshot'] += n
        else: J[state]['open_us_law_other_rows'][kind] = n
    for r in rule_rows:
        if r['state'] in J:
            sets = J[r['state']]['open_us_law_rule_sets']
            sets[r['rule_set']] = sets.get(r['rule_set'], 0) + 1
    label_rule_sets = collections.defaultdict(collections.Counter)
    for r in labels.values():
        if r['state'] and r['rule_set'] and r['law_body_class'] == 'court_rule_body':
            label_rule_sets[r['state']][r['rule_set']] += 1

    trellis = ro(TRELLIS_DB)
    observed = {u for (u,) in trellis.execute("select distinct url from links where category='coverage_county'")}
    saved = {u for (u,) in trellis.execute("select url from pages where category='coverage_county' and status=200")}
    unsaved_by_state = matrix['trellis_unsaved_profile_urls_by_state']
    for abbr, row in J.items():
        source = matrix['states'].get(abbr)
        row['official_court_rule_bodies_by_rule_set'] = dict(label_rule_sets.get(abbr, {}))
        if source is None:
            row['county_layer'] = None
            row['trellis_county_profiles'] = None
            row['source_directory'] = None
            row['audit_flags'] = []
            continue
        row['county_layer'] = dict(source.get('county_registry', {}), records=source['directory'].get('county_records', {}),
                                   distinct_counties=source['directory'].get('county_distinct_geoids', {}))
        t = source.get('trellis_county_profiles', {})
        row['trellis_county_profiles'] = {'observed': t.get('observed', 0), 'saved': t.get('saved', 0),
                                          'unsaved': len(unsaved_by_state.get(abbr, []))}
        row['source_directory'] = {'references': source['source_directory']['references'], 'linked_to_saved': source['source_directory']['linked_to_saved']}
        row['audit_flags'] = source.get('flags', [])

    def any_source(abbr, family):
        cell = J[abbr]['families'][family]
        return cell['official_capture']['total'] + cell['imported_collection'] + cell['third_party_snapshot'] + cell['pending_publication']

    fifty_one = [a for a in J if J[a]['jurisdiction_kind'] != 'territory']
    flag = matrix['flag_index']
    gaps = collections.OrderedDict([
        ('statutes_none_any_source', [a for a in fifty_one if any_source(a, 'statutes') == 0]),
        ('statutes_no_official_or_imported_capture', [a for a in fifty_one if J[a]['families']['statutes']['official_capture']['total'] + J[a]['families']['statutes']['imported_collection'] + J[a]['families']['statutes']['pending_publication'] == 0]),
        ('statutes_third_party_withdrawn_by_publisher', sorted(flag['open_us_law_statutes_absent_publisher_withdrawn'])),
        ('statutes_no_confirmed_or_classified_official_body', [a for a in fifty_one if J[a]['families']['statutes']['official_capture']['body'] + J[a]['families']['statutes']['official_capture']['unreviewed_classified_as'].get('statute_body', 0) + J[a]['families']['statutes']['imported_collection'] == 0]),
        ('regulations_none_any_source', [a for a in fifty_one if any_source(a, 'regulations') == 0]),
        ('regulations_official_capture_present', [a for a in fifty_one if J[a]['families']['regulations']['official_capture']['total'] > 0]),
        ('court_rules_none_any_source', [a for a in fifty_one if any_source(a, 'court_rules') == 0]),
        ('court_rules_third_party_absent', [a for a in fifty_one if J[a]['families']['court_rules']['third_party_snapshot'] == 0]),
        ('court_rules_no_official_capture', [a for a in fifty_one if J[a]['families']['court_rules']['official_capture']['total'] == 0]),
        ('court_rules_no_civil_procedure_set_typed', [a for a in fifty_one if not J[a]['open_us_law_rule_sets'].get('civil_procedure') and not label_rule_sets[a].get('civil_procedure')]),
        ('court_rules_no_evidence_set_typed', [a for a in fifty_one if not J[a]['open_us_law_rule_sets'].get('evidence') and not label_rule_sets[a].get('evidence')]),
        ('court_rules_no_appellate_set_typed', [a for a in fifty_one if not J[a]['open_us_law_rule_sets'].get('appellate') and not label_rule_sets[a].get('appellate')]),
        ('constitution_none_any_source', [a for a in fifty_one if any_source(a, 'constitution') == 0]),
        ('statewide_forms_none', sorted(flag['no_statewide_forms'])),
        ('county_local_rules_none', sorted(flag['no_county_local_rules'])),
        ('trellis_no_county_profiles_observed', sorted(flag['trellis_no_county_profiles_observed'])),
        ('trellis_county_profiles_partial', sorted(a for a in fifty_one if unsaved_by_state.get(a))),
    ])
    gap_definitions = {
        'statutes_none_any_source': 'No statute text from any tier (official capture, imported collection, pending download, Open US Law).',
        'statutes_no_official_or_imported_capture': 'No saved official statute page/file and no imported official export; only the third-party snapshot (if any).',
        'statutes_third_party_withdrawn_by_publisher': 'Open US Law withdrew this state\'s statutes (publisher notice).',
        'statutes_no_confirmed_or_classified_official_body': 'No official statute record in the published body tier, none classified statute_body by this build, and no imported provisions.',
        'regulations_none_any_source': 'No administrative-code text from any tier.',
        'regulations_official_capture_present': 'States with at least one saved official administrative-code record.',
        'court_rules_none_any_source': 'No statewide court-rule text from any tier.',
        'court_rules_third_party_absent': 'State has no court_rules file in Open US Law.',
        'court_rules_no_official_capture': 'No saved official court-rule page or file.',
        'court_rules_no_civil_procedure_set_typed': 'No Open US Law row and no classified official rule body typed civil_procedure. Code states keep civil procedure in statutes, so this is a prompt to check, not proof of absence.',
        'court_rules_no_evidence_set_typed': 'No row or classified official rule body typed evidence (same code-state caveat).',
        'court_rules_no_appellate_set_typed': 'No row or classified official rule body typed appellate.',
        'constitution_none_any_source': 'No constitution text from any tier.',
        'statewide_forms_none': 'No imported statewide court form (from the gap audit).',
        'county_local_rules_none': 'No county-linked local-rule record (from the gap audit).',
        'trellis_no_county_profiles_observed': 'No Trellis county profile URL observed for the state.',
        'trellis_county_profiles_partial': 'Some observed Trellis county profile pages are not saved.',
    }
    for name, members in gaps.items():
        if name == 'regulations_official_capture_present': continue
        for abbr in members:
            if abbr in J: J[abbr]['gaps'].append(name)

    venues = []
    for v in matrix['high_volume_venues']:
        fips = str(v['fips']).zfill(5)
        missing = [k for k, present in (('local_rules', v.get('county_linked_local_rules')), ('forms', v.get('county_linked_forms')),
                                        ('clerk_page', v.get('county_linked_clerk_page'))) if not present]
        venues.append({'fips': fips, 'venue': v['venue'], 'state': v['state'], 'tier': v['tier'],
                       'trellis_profile_url': v.get('trellis_profile_url'), 'trellis_profile_status': v.get('trellis_profile_status'),
                       'county_linked_records': v.get('county_linked_directory_records', {}), 'county_linked_total': v.get('county_linked_total', 0),
                       'county_linked_local_rules': bool(v.get('county_linked_local_rules')), 'county_linked_forms': bool(v.get('county_linked_forms')),
                       'county_linked_clerk_page': bool(v.get('county_linked_clerk_page')), 'missing_county_linked': missing,
                       'court_hosts_checked': v.get('host_patterns_checked', []),
                       'saved_documents_on_court_hosts_not_county_linked': v.get('directory_records_on_court_hosts_not_county_linked', 0),
                       'source_directory_references_on_court_hosts': v.get('source_directory_references_on_court_hosts', []),
                       'statewide_context': {'regulations_none_any_source': v['state'] in gaps['regulations_none_any_source'],
                                             'court_rules_none_any_source': v['state'] in gaps['court_rules_none_any_source'],
                                             'statutes_none_any_source': v['state'] in gaps['statutes_none_any_source']}})
    trellis_unsaved = {'total': sum(len(u) for u in unsaved_by_state.values()),
                       'by_state': {a: {'count': len(u), 'urls': sorted(u)} for a, u in sorted(unsaved_by_state.items())},
                       'catalog_check': {'observed_urls_in_trellis_catalog': len(observed), 'saved_urls_in_trellis_catalog': len(saved),
                                         'unsaved_urls_in_trellis_catalog': len(observed - saved)},
                       'venue_profiles_unsaved': [v['venue'] for v in venues if v['trellis_profile_status'] != 'saved']}
    class_totals = collections.Counter(r['law_body_class'] for r in labels.values())
    totals = {'jurisdictions': len(J), 'states_and_dc': len(fifty_one), 'territories': len(J) - len(fifty_one),
              'unreviewed_official_records_classified': len(labels), 'classified_as': dict(class_totals),
              'open_us_law_court_rule_rows_typed': len(rule_rows), 'venues': len(venues), 'trellis_unsaved_county_profiles': trellis_unsaved['total'],
              'law_records_without_recognised_state': dict(unplaced),
              'by_family': {f: {'official_capture': sum(J[a]['families'][f]['official_capture']['total'] for a in J),
                                'imported_collection': sum(J[a]['families'][f]['imported_collection'] for a in J),
                                'third_party_snapshot': sum(J[a]['families'][f]['third_party_snapshot'] for a in J),
                                'pending_publication': sum(J[a]['families'][f]['pending_publication'] for a in J)} for f in FAMILIES}}
    coverage = {
        'schema_version': '1', 'generated_at': now(),
        'qualification': ('Counts are saved records or exported rows, never unique laws. Official captures are saved official pages/files; '
                          'Open US Law rows are a third-party structured snapshot (v2026.08, publisher date 2026-08-14), not official captures. '
                          'A focused record carrying two category tokens is counted under each family. Class labels on the unreviewed tier are automated '
                          'structural triage and never assert legal currency. A gap means nothing was found in the saved data, not that the law does not exist.'),
        'venue_qualification': ('Venue rows count directory records linked to the county by an explicit 5-character FIPS link. Court-host matches are a convenience '
                                'check and are not county assignments. Trellis figures describe saved third-party profile pages, not court records.'),
        'definitions': {
            'official_capture': 'focused dataset: saved official pages/files. body = published body kinds; unreviewed = needs_content_review / title-evidence-only (see unreviewed_classified_as for this build\'s structural labels); navigation = legal inventory navigation; other = notices, ancillary.',
            'imported_collection': 'imported collection (seeger dataset): structured statute provisions and state court rules/orders; forms are counted separately.',
            'third_party_snapshot': 'Open US Law exported rows by publisher kind.',
            'pending_publication': 'saved downloads awaiting publication in the directory.',
            'jurisdiction_rule': 'State only from the explicit state field; county only from FIPS links; nothing inferred from hostnames.',
            'territories': 'PR, GU, VI, MP, AS are listed for completeness and are outside the 51-jurisdiction gap lists.'},
        'jurisdictions': J, 'gaps': gaps, 'gap_definitions': gap_definitions, 'venues': venues,
        'trellis_unsaved_profiles': trellis_unsaved, 'totals': totals}
    (HERE / 'coverage.json').write_text(json.dumps(coverage, indent=1, ensure_ascii=False), encoding='utf-8')
    print('coverage', {k: len(v) for k, v in gaps.items()})
    return coverage


# --------------------------------------------------------------------------------------------- stage: finalize
def stage_finalize():
    matrix = json.loads(MATRIX.read_text(encoding='utf-8'))
    labels = read_jsonl(HERE / 'record_labels.jsonl')
    rules = read_jsonl(HERE / 'oul_rule_sets.jsonl')
    provisions = read_jsonl(HERE / 'topic_index.jsonl')
    coverage = json.loads((HERE / 'coverage.json').read_text(encoding='utf-8'))
    edges, unresolved = [], read_jsonl(HERE / '_unresolved_labels.jsonl')
    for row in provisions:
        kind, ident = row['provision_id'].split(':', 1)
        node = {'type': kind, 'id': ident}
        basis = ('state field of the Open US Law row (publisher file jurisdiction)' if kind == 'open_us_law_row'
                 else 'explicit state field of the directory record')
        edges.append({'from': node, 'to': {'type': 'state', 'id': row['state']}, 'relation': 'provision_of_jurisdiction', 'basis': basis,
                      'evidence': {'source_dataset': row['source_dataset'], 'citation': row['citation']}})
        for t in row['topics']:
            edges.append({'from': node, 'to': {'type': 'topic', 'id': t['topic']}, 'relation': 'search_candidate_for_topic',
                          'basis': 'FTS5 phrase match (%s); %s' % (t['match'], TOPIC_LABEL),
                          'evidence': {'query_ids': t['query_ids'], 'match': t['match'], 'citation': row['citation']}})
    write_jsonl(HERE / 'edges.jsonl', edges)
    write_jsonl(HERE / 'unresolved.jsonl', unresolved)

    db = ro(DIRECTORY_DB)
    expected = db.execute("select count(*) from records where dataset='focused' and group_name='laws' and "
                          "(kind like '%needs_content_review%' or kind like '%law_document_title_evidence%')").fetchone()[0]
    checks = []

    def check(name, ok, detail):
        checks.append({'name': name, 'passed': bool(ok), 'detail': detail})

    check('every_unreviewed_record_labelled', len(labels) == expected and len({r['record_id'] for r in labels}) == expected, '%d labels for %d unreviewed focused law records' % (len(labels), expected))
    check('label_classes_valid', all(r['law_body_class'] in cl.CLASSES and r['confidence'] in ('high', 'medium', 'low') and r['class_basis'] for r in labels), 'class, basis and confidence present on every label')
    check('no_legal_currency_asserted', all(r['legal_currency_asserted'] is False for r in labels), 'legal_currency_asserted is false on every label')
    check('rule_sets_valid', all(r['rule_set'] in cl.RULE_SETS for r in rules) and all(r['rule_set'] in cl.RULE_SETS for r in labels if r['rule_set']), '%d typed Open US Law rows' % len(rules))
    check('rule_set_mapping_has_no_text', all(set(r) == {'id', 'state', 'rule_set', 'detail', 'basis'} for r in rules), 'mapping rows carry id/state/rule_set/detail/basis only')
    temporal_keys = set(temporal())
    check('temporal_block_everywhere', all(set(r['temporal']) == temporal_keys for r in labels) and all(set(r['temporal']) == temporal_keys for r in provisions), 'ten-key temporal block on labels and topic provisions')
    check('topic_rows_anchored', all(r['state'] in coverage['jurisdictions'] and r['topics'] and (r.get('row_id') or r.get('record_id')) and r['title'] for r in provisions), '%d provisions with state, id, title and >=1 topic' % len(provisions))
    mismatches = []
    for abbr, source in matrix['states'].items():
        for family in FAMILIES:
            audit = source['directory']['focused_laws'].get(family, {})
            mine = coverage['jurisdictions'][abbr]['families'][family]['official_capture']
            for theirs, ours in (('body', 'body'), ('unreviewed_page', 'unreviewed'), ('navigation', 'navigation')):
                if audit.get(theirs, 0) != mine[ours]: mismatches.append('%s/%s/%s audit=%s build=%s' % (abbr, family, ours, audit.get(theirs, 0), mine[ours]))
        for kind, family in (('statutes', 'statutes'), ('constitutions', 'constitution'), ('regulations', 'regulations'), ('court_rules', 'court_rules')):
            if source['open_us_law'].get(kind, 0) != coverage['jurisdictions'][abbr]['families'][family]['third_party_snapshot']:
                mismatches.append('%s/%s open_us_law audit=%s build=%s' % (abbr, family, source['open_us_law'].get(kind, 0), coverage['jurisdictions'][abbr]['families'][family]['third_party_snapshot']))
    check('counts_reconcile_with_gap_audit', not mismatches, 'official tiers and Open US Law rows equal coverage_matrix.json for 51 jurisdictions' if not mismatches else '; '.join(mismatches[:12]))
    flag = matrix['flag_index']
    gaps = coverage['gaps']
    check('gap_statutes_georgia_only', gaps['statutes_none_any_source'] == ['GA'], str(gaps['statutes_none_any_source']))
    check('gap_regulations_34_states', sorted(gaps['regulations_none_any_source']) == sorted(flag['ZERO_REGULATIONS_ANY_SOURCE']) and len(gaps['regulations_none_any_source']) == 34, '%d states' % len(gaps['regulations_none_any_source']))
    check('gap_court_rules_mo_ok', gaps['court_rules_none_any_source'] == ['MO', 'OK'] and sorted(flag['ZERO_COURT_RULES_ANY_SOURCE']) == ['MO', 'OK'], str(gaps['court_rules_none_any_source']))
    # The audit flag excludes the zero-any-source states and counts an imported (Seeger) court-rule document as a capture;
    # this build reports "no official capture" strictly. Compare on the audit's definition and record the difference.
    imported_rule_states = sorted(abbr for abbr, row in coverage['jurisdictions'].items()
                                  if isinstance(row.get('families'), dict) and (row['families'].get('court_rules') or {}).get('imported_collection', 0) > 0)
    audit_view = sorted(set(gaps['court_rules_no_official_capture']) - set(gaps['court_rules_none_any_source']) - set(imported_rule_states))
    check('gap_lists_match_audit_flags', sorted(gaps['court_rules_third_party_absent']) == sorted(flag['open_us_law_court_rules_absent'])
          and audit_view == sorted(flag['no_official_court_rule_capture'])
          and sorted(gaps['statutes_no_official_or_imported_capture']) == sorted(flag['no_official_statute_capture']),
          'third-party-absent and no-official-statute lists equal the audit flags; no-official-rule list equals the audit flag after removing '
          'zero-any-source states %s and states whose only court-rule capture is an imported collection document %s'
          % (gaps['court_rules_none_any_source'], imported_rule_states))
    check('venues_28_with_fips_strings', len(coverage['venues']) == 28 and all(isinstance(v['fips'], str) and re.fullmatch(r'\d{5}', v['fips']) for v in coverage['venues']), '%d venues' % len(coverage['venues']))
    check('trellis_unsaved_reconciles', coverage['trellis_unsaved_profiles']['total'] == sum(j['trellis_county_profiles']['unsaved'] for j in coverage['jurisdictions'].values() if j.get('trellis_county_profiles')), 'unsaved profile URLs: %d' % coverage['trellis_unsaved_profiles']['total'])
    check('edges_explicit', len(edges) == sum(1 + len(r['topics']) for r in provisions), '%d edges' % len(edges))
    # Drive-letter paths (C:\ or C:/Users) and *_path keys; a URL scheme such as https:// is not a path.
    path_leak = re.compile(r'(?<![A-Za-z])[A-Za-z]:\\|(?<![A-Za-z])[A-Za-z]:/(?!/)|"(?:raw_path|text_path|metadata_path)"')
    leaks = [name for name in ('record_labels.jsonl', 'oul_rule_sets.jsonl', 'topic_index.jsonl', 'coverage.json', 'topics.json', 'edges.jsonl')
             if path_leak.search((HERE / name).read_text(encoding='utf-8'))]
    check('data_files_path_free', not leaks, 'no drive-letter or *_path keys in data files' if not leaks else str(leaks))

    data_files = []
    for name in ('coverage.json', 'topics.json', 'topic_index.jsonl', 'record_labels.jsonl', 'oul_rule_sets.jsonl', 'edges.jsonl', 'unresolved.jsonl'):
        payload = (HERE / name).read_bytes()
        rows = payload.count(b'\n') if name.endswith('.jsonl') else 1
        data_files.append({'path': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'rows': rows})
    topic_counts = collections.Counter(t['topic'] for r in provisions for t in r['topics'])
    oul_meta = dict(ro(OUL_DB).execute('select key,value from import_meta'))
    passed = all(c['passed'] for c in checks)
    gate = {
        'schema_version': '1', 'status': 'passed' if passed else 'failed', 'ready': passed, 'validated_at': now(),
        'data_files': data_files,
        'counts': {'labels': len(labels), 'labels_by_class': dict(collections.Counter(r['law_body_class'] for r in labels)),
                   'labels_by_confidence': dict(collections.Counter(r['confidence'] for r in labels)),
                   'official_court_rule_records_typed': sum(1 for r in labels if r['rule_set']),
                   'open_us_law_court_rule_rows_typed': len(rules), 'open_us_law_rule_sets': dict(collections.Counter(r['rule_set'] for r in rules)),
                   'topic_provisions': len(provisions), 'topic_tags': dict(topic_counts),
                   'topic_provisions_by_source_tier': dict(collections.Counter(r['source_tier'] for r in provisions)),
                   'jurisdictions': len(coverage['jurisdictions']), 'venues': len(coverage['venues']),
                   'trellis_unsaved_county_profiles': coverage['trellis_unsaved_profiles']['total'], 'edges': len(edges), 'unresolved': len(unresolved)},
        'checks': checks,
        'qualification': ('Automated structural triage and search-derived topic candidates over already-saved data. No legal review, no statement of legal '
                          'currency or completeness, no new acquisition. Topic index: ' + TOPIC_LABEL + '.'),
        'license_ref': 'Open US Law rows: Vaquill AI open-us-law, CC BY 4.0 (attribution in sources/open_us_law_20260918 import_meta); ids, citations and titles only, no row text copied.',
        'inputs': [{'path': 'reports/corpus_upgrade_20260919/understand/coverage_matrix.json', 'sha256': sha256_file(MATRIX)},
                   {'path': 'delivery/archive-directory/directory.sqlite3', 'sha256': sha256_file(DIRECTORY_DB)},
                   {'path': 'sources/trellis/catalog/catalog.sqlite3', 'sha256': sha256_file(TRELLIS_DB)},
                   {'path': 'sources/open_us_law_20260918/catalog.sqlite3', 'sha256': None,
                    'fingerprint': {'bytes': OUL_DB.stat().st_size, 'snapshot': json.loads(oul_meta.get('snapshot', 'null')),
                                    'indexed_rows': json.loads(oul_meta.get('indexed_rows', 'null')), 'completed_at': json.loads(oul_meta.get('completed_at', 'null')),
                                    'note': '29.8 GB database; identified by import metadata instead of a full-file hash'}}]}
    (HERE / 'validation.json').write_text(json.dumps(gate, indent=1, ensure_ascii=False), encoding='utf-8')
    print('validation', gate['status'], [c['name'] for c in checks if not c['passed']])
    return gate


STAGES = collections.OrderedDict([('labels', stage_labels), ('rules', stage_rules), ('topics', stage_topics),
                                  ('coverage', stage_coverage), ('finalize', stage_finalize)])

if __name__ == '__main__':
    wanted = sys.argv[1:] or list(STAGES)
    for name in STAGES:
        if name in wanted:
            started = time.time()
            STAGES[name]()
            print('stage', name, 'done in %.1fs' % (time.time() - started), flush=True)
