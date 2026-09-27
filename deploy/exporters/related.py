"""Offline finite related-material blocks and source-native court matching data."""
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
from unittest.mock import patch
from places_judges import Exporter, OUTPUT


class CategorizedConnection:
    """Read-only query projection matching the generic export's inclusion rule."""
    def __init__(self, connection, table, predicate):
        self.connection, self.table, self.predicate = connection, table, predicate
    def execute(self, sql, values=()):
        sql = sql.replace('FROM ' + self.table, 'FROM (SELECT * FROM ' + self.table + ' WHERE ' + self.predicate + ') AS ' + self.table)
        return self.connection.execute(sql, values)
    def close(self): self.connection.close()


def main():
    exporter = Exporter(OUTPUT / 'related')
    contexts = []
    def put(key, value):
        if value is None: return
        value = exporter.public(value)
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf8')
        contexts.append({'key': key, 'data': value, 'source_sha256': hashlib.sha256(raw).hexdigest()})
    try:
        citation, ready = exporter.freeze('citation_index', '_state')
        if not ready[0]: raise ValueError('Citation index gate is closed')
        with citation._connect() as db:
            ids = [r[0] for r in db.execute("SELECT DISTINCT local_record_id FROM authorities WHERE local_record_id<>''")]
            cited_docs = [tuple(r) for r in db.execute('SELECT DISTINCT layer,doc_id FROM mentions')]
        groups = OUTPUT / 'core/display_groups.jsonl'
        exporter.remember(groups)
        allowed = set()
        for line in groups.read_text(encoding='utf8').splitlines():
            row = json.loads(line)
            allowed.update(row.get('member_ids') or [])
            allowed.update([row['id'], row['preferred_id']])
        for key in ids:
            if key in allowed or key.startswith('oul:'):
                put('related:citations:' + key, citation.for_record(key))
        put('related:citations:status', {'available': True, 'excluded_unpublished_records': sum(key not in allowed and not key.startswith('oul:') for key in ids)})
        canonical_layers = {'agency-documents': 'agency_science_documents', 'source-documents': 'source_documents',
                            'uscourts': 'uscourts_pages', 'saved-pages': 'saved_pages'}
        for layer, ident in cited_docs:
            if layer not in canonical_layers: continue
            block = citation.for_document(layer, ident)
            if block and block.get('results'):
                put('generic:extra:' + canonical_layers[layer] + ':' + ident,
                    {'citation_section': {'heading': 'Authorities cited in this document (%d)' % block['total'], 'items': block['results'][:40]}})

        court, court_ready = exporter.freeze('court_reference', '_state')
        if not court_ready[0]: raise ValueError('Court reference gate is closed')
        import courts_db
        with court._connect() as db:
            court_rows = [dict(row) for row in db.execute('SELECT * FROM courts')]
        for row in court_rows:
            extra = court.for_court(row['id'])
            if extra: put('generic:extra:court_spine:' + row['id'], {'facts': extra['facts'], 'sections': extra['sections']})
        court_index = {r['id']: {'id': r['id'], 'name': r['name'], 'cited_as': r['citation_string'], 'place': r['place'],
            'system': r['system'], 'level': r['level_label'], 'in_registry': bool(r['in_registry']),
            'link': '#courts?q=' + r['id'] if r['in_registry'] else ''} for r in court_rows}
        patterns = [{'pattern': entry[0].pattern, 'id': entry[1], 'type': entry[3], 'parent': entry[5]} for entry in courts_db.regexes]
        put('related:court-matcher', {'available': True, 'courts': court_index, 'patterns': patterns,
            'native_courts': [{'id': r['id'], 'name': r['name'], 'type': r['type'], 'parent': r.get('parent')} for r in courts_db.courts],
            'publisher': 'Free Law Project courts-db', 'package_version': importlib.metadata.version('courts-db')})

        urls, url_state = exporter.freeze('url_directory', '_state')
        if not url_state[0]: raise ValueError('URL directory gate is closed')
        url_connect = urls._connect
        def url_connection(state):
            return CategorizedConnection(url_connect(state), 'urls', "trim(coalesce(content_type,''))<>'' AND is_noise=0")
        exporter.stack.enter_context(patch.object(urls, '_connect', side_effect=url_connection))
        con = url_connect(url_state[0])
        try:
            states = {r[0] for r in con.execute("SELECT DISTINCT state FROM urls WHERE state<>''")}
            agencies = {r[0] for r in con.execute("SELECT DISTINCT agency_key FROM urls WHERE agency_key<>''")}
            courts = {r[0] for r in con.execute("SELECT DISTINCT court_id FROM urls WHERE court_id<>''")}
        finally: con.close()
        docs = importlib.import_module('court_documents')
        exporter.remember_module(docs)
        exporter.remember(docs.DB_PATH)
        gate = docs._gate()
        exporter.stack.enter_context(patch.object(docs, '_gate', return_value=gate))
        docs_connect = docs._connect
        def docs_connection():
            connection, value = docs_connect()
            return CategorizedConnection(connection, 'documents', "coalesce(doc_type,'') NOT IN ('','other_unknown')"), value
        exporter.stack.enter_context(patch.object(docs, '_connect', side_effect=docs_connection))
        con, _ = docs_connect()
        try:
            states.update(r[0] for r in con.execute("SELECT DISTINCT state FROM documents WHERE state<>''"))
            states.update(r[0] for r in con.execute("SELECT DISTINCT court_state FROM documents WHERE court_state<>''"))
            for r in con.execute('SELECT DISTINCT court_id,candidate_court_ids FROM documents'):
                if r['court_id']: courts.add(r['court_id'])
                courts.update(json.loads(r['candidate_court_ids'] or '[]'))
        finally: con.close()
        for name, function in [('state_proceedings', '_load'), ('saved_pages', '_state'), ('limitation_periods', '_state')]:
            exporter.freeze(name, function)
        # Inventory includes every state already represented by the county directory.
        for line in (OUTPUT / 'counties.jsonl').read_text(encoding='utf8').splitlines():
            states.add(json.loads(line)['state'])
        for state in sorted(states):
            put('related:blocks:state:' + state, {key: exporter.server.judge_layer(key, 'for_state', state)
                for key in ['state_proceedings', 'court_documents', 'saved_pages', 'limitation_periods']})
            put('related:urls:state:' + state, urls.for_state(state))
        for agency in sorted(agencies): put('related:urls:agency:' + agency, urls.for_agency(agency))
        for ident in sorted(courts):
            block = urls.for_court(ident)
            put('related:urls:court:' + ident, block)
            put('related:blocks:court:' + ident, {'court_documents': docs.for_court(ident), 'urls': block})
        exporter.ensure_unchanged()
        temp = exporter.output / 'contexts.jsonl.tmp'
        with temp.open('w', encoding='utf8', newline='\n') as stream:
            for row in contexts: exporter.line(stream, row)
        final = exporter.output / 'contexts.jsonl'
        temp.replace(final)
        receipt = {'status': 'passed', 'contexts': len(contexts), 'sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
                   'bytes': final.stat().st_size, 'source_snapshots_unchanged': True, 'network_requests': 0,
                   'court_patterns': len(patterns), 'court_names': len(court_index),
                   'categorized_projection': 'Court document other_unknown and URL entries lacking content_type are excluded, matching generic export.'}
        (exporter.output / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
        print(json.dumps(receipt))
    finally: exporter.stack.close()


if __name__ == '__main__': main()
