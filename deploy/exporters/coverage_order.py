"""Small reviewed context patch: native topic order and categorized DOJ joins."""
from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
import sys
from places_judges import Exporter, OUTPUT
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from import_catalog import normalize


def main():
    exporter = Exporter(OUTPUT)
    try:
        coverage, snapshot = exporter.freeze('jurisdiction_coverage', 'load')
        if not snapshot: raise ValueError('Coverage publication gate is closed')
        orders = {}
        all_topics = defaultdict(list)
        for (state, topic), entries in snapshot['by_state_topic'].items():
            orders[state + '|' + topic] = [snapshot['provisions'][position]['provision_id'] for _, position in sorted(entries)]
            all_topics[topic].extend(entries)
        for topic, entries in all_topics.items():
            orders['|' + topic] = [snapshot['provisions'][position]['provision_id'] for _, position in sorted(entries)]
        for topic, ids in orders.items():
            if len(ids) != len(set(ids)): raise ValueError('Duplicate native topic membership: ' + topic)

        source = OUTPUT / 'sources.jsonl'
        metadata = json.loads(source.with_suffix('.dataset.json').read_text(encoding='utf8'))
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata['export_jsonl_sha256']:
                raise ValueError('Categorized source export hash mismatch')
        with source.open(encoding='utf8') as stream: retained = {json.loads(line)['id'] for line in stream}
        if len(retained) != metadata['expected_records']: raise ValueError('Categorized source ID count mismatch')
        exporter.remember(source)
        doj, state = exporter.freeze('doj_resources', '_load')
        if not state: raise ValueError('DOJ publication gate is closed')
        resources = copy.deepcopy(state['resources'])
        pruned = 0
        for row in resources:
            original = row['directory_ref_ids']
            row['directory_ref_ids'] = [ident for ident in original if ident in retained]
            removed = len(original) - len(row['directory_ref_ids'])
            if removed:
                pruned += removed
                row['excluded_directory_reference_count'] = removed
                row['original_not_in_source_directory'] = row['not_in_source_directory']
                row['original_directory_match_basis'] = row['directory_match_basis']
                row['source_directory_scope_note'] = 'Only categorized source references are linked in this deployment; omitted references remain in the local source snapshot.'
                if not row['directory_ref_ids']:
                    row['not_in_source_directory'] = True
                    row['directory_match_basis'] = 'none'
        edges = [copy.deepcopy(edge) for edge in state['edges'] if all(endpoint.get('type') != 'source' or
            endpoint.get('id', '').removeprefix('source:') in retained for endpoint in (edge.get('from') or {}, edge.get('to') or {}))]
        state_summary = doj.states()
        for page in state_summary['items'] + ([state_summary['federal_page']] if state_summary.get('federal_page') else []):
            group = page.get('usps') or 'FEDERAL'
            page_rows = [row for row in resources if (row.get('usps') or 'FEDERAL') == group]
            page['not_in_source_directory_count'] = sum(row.get('not_in_source_directory') is True for row in page_rows)
        state_rows = [row for row in resources if row['page_kind'] == 'state_resource_page']
        external = [row for row in state_rows if not row['doj_internal_link']]
        missing_urls = {row['url'] for row in external if row['not_in_source_directory']}
        summary = state_summary['summary']
        summary['original_directory_match_counts'] = {key: summary[key] for key in (
            'external_unique_urls_matched_exact', 'external_unique_urls_matched_any', 'external_unique_urls_not_in_source_directory',
            'rows_not_in_source_directory', 'directory_references_matched_from_state_pages', 'source_directory_references',
            'edges', 'edges_source_to_state', 'edges_state_to_circuit', 'unresolved')}
        summary.update(source_directory_references=len(retained), external_unique_urls_matched_exact=len({row['url'] for row in external if row['directory_match_basis'] == 'exact'}),
            external_unique_urls_matched_any=len({row['url'] for row in external} - missing_urls),
            external_unique_urls_not_in_source_directory=len(missing_urls),
            rows_not_in_source_directory=sum(bool(row['not_in_source_directory']) for row in resources),
            directory_references_matched_from_state_pages=len({ident for row in state_rows for ident in row['directory_ref_ids']}),
            edges=len(edges), edges_source_to_state=sum(row['relation'] == 'listed_on_doj_state_resource_page' for row in edges),
            edges_state_to_circuit=sum(row['relation'] == 'served_by_federal_circuit' for row in edges),
            excluded_source_reference_links=pruned,
            unresolved=summary['unresolved'] + sum(row.get('original_not_in_source_directory') is False and row['not_in_source_directory'] is True for row in resources))
        qualification = doj.QUALIFICATION + ' Source-directory links and membership counts in this deployment cover categorized references only; uncategorized references are retained locally and excluded here.'
        state_summary['qualification'] = qualification
        contexts = {'coverage:topic_order': orders, 'doj:resources': resources, 'doj:edges': edges,
                    'doj:states': state_summary, 'doj:qualification': qualification}
        exporter.ensure_unchanged()
        output = OUTPUT / 'contexts.coverage_order.jsonl'
        with output.with_suffix('.tmp').open('w', encoding='utf8', newline='\n') as stream:
            for key, value in contexts.items():
                # section_path is a published H2/H3 label, not a filesystem path.
                # Use the deployment's exact sensitive-key scrubber here.
                value = normalize({'id': 'context', 'dataset': 'context', 'category': 'directories', 'item': value})['item']
                digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf8')).hexdigest()
                exporter.line(stream, {'key': key, 'data': value, 'source_sha256': digest})
        output.with_suffix('.tmp').replace(output)
        receipt = {'status': 'passed', 'contexts': len(contexts), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                   'bytes': output.stat().st_size, 'topic_order_keys': len(orders), 'retained_source_references': len(retained),
                   'excluded_source_links': pruned, 'excluded_edges': len(state['edges']) - len(edges),
                   'resources_preserved': len(resources), 'network_requests': 0, 'source_snapshots_unchanged': True}
        output.with_suffix('.receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
        print(json.dumps(receipt))
    finally: exporter.stack.close()


if __name__ == '__main__': main()
