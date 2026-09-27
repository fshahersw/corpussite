"""Write integration_spec.json from real adapter responses (run after build.py; offline, read-only)."""
from __future__ import annotations

import copy
import datetime
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
import jurisdiction_coverage as jc  # noqa: E402


def trim(value, rows=2, urls=2):
    """Shorten lists inside a real response so the example stays readable; a '...' marker records the cut."""
    value = copy.deepcopy(value)
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, list) and len(item) > rows and key in ('rows', 'items', 'venues', 'urls', 'checks'):
                value[key] = item[:rows] + ['... %d more' % (len(item) - rows)]
            elif isinstance(item, (dict, list)):
                value[key] = trim(item, rows, urls)
    elif isinstance(value, list):
        value = [trim(v, rows, urls) for v in value]
    return value


def main():
    if jc.load() is None:
        raise SystemExit('gate closed: build and validate first')
    matrix = jc.matrix()
    matrix_example = dict(matrix, rows=[r for r in matrix['rows'] if r['abbr'] in ('GA', 'PA')] + ['... %d more rows' % (len(matrix['rows']) - 2)])
    matrix_example['gaps'] = {k: v for k, v in matrix['gaps'].items() if k in ('statutes_none_any_source', 'regulations_none_any_source', 'court_rules_none_any_source')}
    matrix_example['gaps']['...'] = '%d more gap lists' % (len(matrix['gaps']) - 3)
    detail = trim(jc.state_detail('PA'), rows=2)
    topics = jc.topics(state='PA', topic='sol', page=1, limit=2)
    topics_example = dict(topics, topic_catalog={'sol': topics['topic_catalog']['sol'], '...': '%d more topics' % (len(topics['topic_catalog']) - 1)})
    venues = trim(jc.venues(), rows=2)
    labels = trim(jc.labels(state='NV', law_body_class='statute_body', confidence='high', page=1, limit=1), rows=1)
    first_typed = next(iter(jc.load()['oul_sets']))
    rule_set = jc.rule_set(first_typed)
    spec = {
        'schema_version': '1',
        'generated_at': datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        'supplement': 'sources/law_tier_20260919',
        'adapter': 'delivery/archive-directory/jurisdiction_coverage.py',
        'adapter_test': 'delivery/archive-directory/test_jurisdiction_coverage.py',
        'gate': 'load() re-hashes every data file listed in validation.json (status == "passed" and ready is true) and returns None otherwise; every public function then returns an "available": false shape or None. No files are served by this adapter; nothing in a public dict is a filesystem path.',
        'functions': [
            {'name': 'matrix', 'signature': 'matrix(folder=DATA) -> dict',
             'returns': '{available, generated_at, qualification, definitions, rows: [one per state, DC, territory], gaps: {gap_name: [USPS...]}, totals}',
             'row_fields': ['name', 'abbr', 'jurisdiction_kind', 'families.{statutes|constitution|regulations|court_rules}.{official_capture{total,body,unreviewed,navigation,other,unreviewed_classified_as},imported_collection,third_party_snapshot,pending_publication}', 'reviewed_tier', 'gaps', 'county_layer', 'trellis_county_profiles'],
             'order': 'states and DC alphabetically by name, then territories'},
            {'name': 'state_detail', 'signature': "state_detail(state, folder=DATA) -> dict | None",
             'params': {'state': 'USPS abbreviation or full name, case-insensitive; unknown -> None'},
             'returns': 'full coverage row plus topic_counts, reviewed_labels{by_class,by_rule_set,by_confidence}, open_us_law_rule_sets, official_court_rule_bodies_by_rule_set, trellis_unsaved_profiles{count,urls}, venues[], audit_flags, source_directory, topic_label, qualification'},
            {'name': 'topics', 'signature': 'topics(state=None, topic=None, page=1, limit=50, folder=DATA) -> dict',
             'params': {'state': 'optional USPS abbreviation or name', 'topic': 'optional topic id from topic_catalog', 'page': 'int >= 1', 'limit': '1..200 (default 50)'},
             'returns': '{available, label, state, topic, items[], total, page, pages, limit, by_source_tier, topic_catalog{topic: {label, group, queries, query_provenance, provisions}}}; title matches sort before text matches within a topic',
             'item_fields': ['provision_id', 'source_dataset', 'source_tier', 'row_id | record_id', 'state', 'family', 'citation', 'title', 'source_url', 'publisher_status', 'record_class', 'temporal', 'topics', 'match', 'query_ids']},
            {'name': 'venues', 'signature': 'venues(folder=DATA) -> dict', 'returns': '{available, items: [28 venue rows], total, qualification}; fips are 5-character strings'},
            {'name': 'labels', 'signature': 'labels(state=None, law_body_class=None, rule_set=None, confidence=None, page=1, limit=50, folder=DATA) -> dict',
             'returns': 'structural labels of the formerly unreviewed official tier with class_basis, confidence, features and the temporal block; legal_currency_asserted is always false'},
            {'name': 'rule_set', 'signature': 'rule_set(row_id, folder=DATA) -> dict | None', 'returns': '{id, state, rule_set, detail, basis} for one typed Open US Law court-rule row'},
        ],
        'routes': [
            {'method': 'GET', 'path': '/api/coverage/matrix', 'params': {}, 'calls': 'jurisdiction_coverage.matrix()', 'example_response': matrix_example},
            {'method': 'GET', 'path': '/api/coverage/state', 'params': {'state': 'PA'}, 'calls': "jurisdiction_coverage.state_detail(state)", 'status_when_unknown': 404, 'example_response': detail},
            {'method': 'GET', 'path': '/api/coverage/topics', 'params': {'state': 'PA', 'topic': 'sol', 'page': 1, 'limit': 2}, 'calls': 'jurisdiction_coverage.topics(state, topic, page, limit)', 'example_response': topics_example},
            {'method': 'GET', 'path': '/api/coverage/venues', 'params': {}, 'calls': 'jurisdiction_coverage.venues()', 'example_response': venues},
            {'method': 'GET', 'path': '/api/coverage/labels', 'params': {'state': 'NV', 'law_body_class': 'statute_body', 'confidence': 'high', 'page': 1, 'limit': 1}, 'calls': 'jurisdiction_coverage.labels(...)', 'example_response': labels},
            {'method': 'GET', 'path': '/api/coverage/rule-set', 'params': {'id': first_typed}, 'calls': 'jurisdiction_coverage.rule_set(id)', 'status_when_unknown': 404, 'example_response': rule_set},
        ],
        'ui_placement': {
            'area': 'Laws',
            'pages': [
                {'page': 'Jurisdiction coverage', 'section': 'Coverage matrix', 'source': '/api/coverage/matrix',
                 'columns': ['State', 'Statutes (official body / unreviewed-classified body / imported / third-party rows)', 'Constitution', 'Regulations', 'Court rules', 'County layer', 'Trellis saved/observed', 'Gaps'],
                 'filters': ['gap list (multi-select from gaps keys)', 'jurisdiction kind (state / DC / territory)', 'family with any official capture'],
                 'labels': {'official_capture': 'Official capture', 'imported_collection': 'Imported official export', 'third_party_snapshot': 'Open US Law rows (third-party snapshot v2026.08)', 'pending_publication': 'Saved, awaiting publication'},
                 'caveat': 'Counts are saved records or exported rows, never unique laws. A gap means nothing was found in the saved data, not that the law does not exist.'},
                {'page': 'Jurisdiction coverage', 'section': 'State detail drawer', 'source': '/api/coverage/state?state=XX',
                 'blocks': ['family tiers', 'unreviewed tier classified (by class and confidence)', 'court-rule sets typed (official bodies and Open US Law rows)', 'topic counts (links to the topic index)', 'venues in this state', 'Trellis unsaved county profile URLs', 'audit flags'],
                 'caveat': 'Class labels are automated structural triage of saved text and never assert legal currency.'},
                {'page': 'Mass-tort topic index', 'section': 'Provisions by state and topic', 'source': '/api/coverage/topics',
                 'filters': ['state', 'topic', 'source tier'], 'columns': ['Citation', 'Title', 'Family', 'Source tier', 'Match (title/text)', 'Publisher status (Open US Law only)', 'Source URL'],
                 'caveat': 'search-derived candidates, not legal advice or a complete survey'},
                {'page': 'Venues', 'section': 'High-volume mass-tort venues', 'source': '/api/coverage/venues',
                 'columns': ['Venue', 'FIPS', 'Tier', 'Trellis profile', 'County-linked records', 'Missing county-linked (local rules / forms / clerk)', 'Saved docs on court hosts (not linked)', 'Statewide gaps'],
                 'caveat': 'Court-host matches are a convenience check and not county assignments.'},
                {'page': 'Laws browse (existing)', 'section': 'Record chip', 'source': '/api/coverage/labels?state=&law_body_class=',
                 'note': 'Show law_body_class + confidence as a chip on unreviewed official law records; filter "Classified as body" surfaces statute_body / constitution_body / regulation_body / court_rule_body with confidence high or medium.'},
            ],
        },
        'edges': {'file': 'sources/law_tier_20260919/edges.jsonl',
                  'relations': {'provision_of_jurisdiction': 'from {type: open_us_law_row | record, id} to {type: state, id: USPS}; basis is the explicit state field of the row/record',
                                'search_candidate_for_topic': 'from the same provision node to {type: topic, id: topic id}; basis is the FTS5 phrase match (title or text) and the topic label'},
                  'unresolved': 'unresolved.jsonl lists labelled records with no recognised state (jurisdiction is never inferred from the URL)'},
        'caveats': ['No legal review; no statement of legal currency or completeness; no new acquisition.',
                    'Open US Law rows are a third-party structured snapshot (v2026.08, publisher date 2026-08-14), CC BY 4.0; only ids, citations and titles are copied.',
                    'Topic index label: search-derived candidates, not legal advice or a complete survey.'],
    }
    (HERE / 'integration_spec.json').write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding='utf-8')
    print('integration_spec.json written; routes', len(spec['routes']))


if __name__ == '__main__':
    main()
