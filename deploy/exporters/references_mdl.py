"""Export categorized source references and the validated JPML registry, offline."""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from places_judges import Exporter, OUTPUT


def categorized_docket_block(block, rows, compact):
    """Keep MDL related links inside the same classified document export."""
    if not block:
        return block
    retained = [row for row in rows if str(row.get('doc_type') or '').strip().lower()
                not in {'', 'other', 'unknown', 'uncategorized', 'other_unknown'}]
    result = copy.deepcopy(block)
    result.update(total=len(retained), by_doc_type=dict(Counter(row['doc_type'] for row in retained)),
                  latest_25=[compact(row) for row in retained[:25]], source_snapshot_total=len(rows),
                  excluded_uncategorized=len(rows) - len(retained))
    result['qualification'] = (result.get('qualification') or '') + (
        ' Linked documents and displayed type counts include categorized records only. '
        'The source snapshot total also includes uncategorized records retained locally and excluded from this deployment.')
    return result


class ReferenceExporter(Exporter):
    def verified_asset(self, url, path, served):
        if url in self.photo_cache:
            return self.photo_cache[url]
        if not served or not path:
            raise ValueError('Published source artifact is unavailable: ' + url)
        data, mime, _ = served
        path = Path(path).resolve()
        if path.read_bytes() != data:
            raise ValueError('Source artifact changed: ' + url)
        self.remember(path)
        result = [{'url': url, 'local_path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                   'bytes': len(data), 'mime': mime, 'role': 'original' if not url.endswith('/text') else 'text'}]
        self.photo_cache[url] = result
        return result

    def sources(self):
        server = self.server
        module, original = self.freeze('source_directory', '_load')
        data = copy.deepcopy(original)
        excluded = [r for r in data['entries'] if not r.get('category') or r['category'] in {'unknown', 'uncategorized'}]
        data['entries'] = [r for r in data['entries'] if r not in excluded]
        data['_by_id'] = {r['id']: r for r in data['entries']}
        fields = {'jurisdictions': 'jurisdiction', 'categories': 'category', 'access_methods': 'access_method', 'statuses': 'verification_status'}
        fields.update({facet: pair[0] for facet, pair in module.TYPE_FACETS.items()})
        for facet, field in fields.items():
            counts = Counter(r.get(field) or module.UNSPECIFIED for r in data['entries'])
            data['facets'][facet] = [{**r, 'count': counts[r['value']]} for r in data['facets'][facet] if counts[r['value']]]
        summary = data['summary']
        summary.update(source_records=len(data['entries']), categorized=len(data['entries']), unique_urls=len({r['url'] for r in data['entries']}),
                       historical_verified_claims=sum(r['verification_status'] == 'historically_verified' for r in data['entries']),
                       task_tagged=sum(bool(r.get('task_family')) for r in data['entries']),
                       markdown_observations=sum(len(r.get('observations') or []) for r in data['entries']),
                       explicit_api_records=sum(r['access_method'] == 'api' for r in data['entries']),
                       api_bulk_layer_records=sum(r.get('layer') == 'api_bulk' for r in data['entries']),
                       api_hints_present=sum(bool(r.get('api_hint')) for r in data['entries']),
                       excluded_uncategorized=len(excluded))
        self.stack.enter_context(patch.object(module, '_load', return_value=data))
        captures, capture_state = self.freeze('source_captures', '_state')
        saved = capture_state['records']
        for path, _ in capture_state['watched']:
            self.remember(path)
        archive, archive_rows = self.freeze('source_archive_links', 'load')
        self.remember(server.DB)
        # The hosted catalog intentionally excludes unclassified main records.
        # Keep source attachments consistent with the categorized display groups.
        groups_path = self.output / 'core/display_groups.jsonl'
        if not groups_path.is_file():
            raise ValueError('Categorized main display-group export is required before source references')
        self.remember(groups_path)
        allowed = set()
        with groups_path.open(encoding='utf8') as group_stream:
            for line in group_stream:
                group = json.loads(line)
                allowed.update(group.get('member_ids') or [])
                allowed.update(x for x in (group.get('id'), group.get('preferred_id')) if x)
        pruned_links = []
        filtered_archive = {}
        for url, record in archive_rows.items():
            targets = []
            for target in record['targets']:
                if target['kind'] == 'directory_record' and target['record_id'] not in allowed:
                    pruned_links.append({'source_id': record['source_id'], 'record_id': target['record_id'],
                                         'reason': 'main_record_not_in_categorized_export'})
                else: targets.append(target)
            if targets: filtered_archive[url] = {**record, 'targets': targets}
        archive_rows = filtered_archive
        self.freeze('source_api_context', '_load')
        self.freeze('doj_resources', '_load')
        saved_urls = captures.source_urls(saved)
        archive_counts = archive.link_counts(archive_rows)
        listing = module.listing({'limit': 100}, saved_urls, archive_counts)
        if not listing['ready']:
            raise ValueError('Source directory gate is closed')
        listing['summary']['api_context'] = server.source_api_context.summary()
        listing['summary']['archive_links'] = archive.summary(archive_rows)
        listing['summary']['excluded_archive_links'] = len(pruned_links)
        registry_order = {r['id']: i for i, r in enumerate(data['entries'])}
        archive_assets = {t['version_id']: t for row in archive_rows.values() for t in row['targets'] if t['kind'] == 'retained_capture'}
        temp = self.output / 'sources.jsonl.tmp'
        index = []
        with temp.open('w', encoding='utf8', newline='\n') as stream:
            rank = 0
            for page in range(1, (listing['total'] + 99) // 100 + 1):
                batch = module.listing({'limit': 100, 'page': page}, saved_urls, archive_counts)
                for item in batch['items']:
                    detail = module.detail(item['id'])
                    detail['captures'] = captures.attachments(item['url'], saved)
                    detail['has_saved_content'] = bool(detail['captures'])
                    detail['archive_records'] = archive.records_for(item['url'], archive_rows)
                    detail['has_archive_records'] = bool(detail['archive_records'])
                    detail['archive_record_count'] = len(detail['archive_records'])
                    detail['api_reference'] = server.source_api_context.detail(detail['source_record']['id'], item['url'])
                    detail['doj_listings'] = server.doj_resources.source_listings(item['id'])
                    artifacts = []
                    for attachment in detail['captures'] + detail['archive_records']:
                        for key in ('original_url', 'text_url'):
                            url = attachment.get(key) or ''
                            if not url.startswith('/source-assets/'):
                                continue  # Main catalog /files and /api/text assets are owned by its exporter.
                            token = url.split('/source-assets/', 1)[1]
                            ident, kind = token.split('/')
                            if ident.startswith('archive:'):
                                row = archive_assets[ident.split(':', 1)[1]]
                                served = archive.asset(token, archive_rows)
                            else:
                                row = saved[ident]
                                served = captures.asset(token)
                            artifacts += self.verified_asset(url, row['_raw' if kind == 'original' else '_text'], served)
                    filters = {key: item.get(key) or module.UNSPECIFIED for key in (
                        'jurisdiction', 'category', 'access_method', 'verification_status', 'layer', 'task_family',
                        'content_kind', 'source_type', 'access_requirements')}
                    filters.update(id=item['id'], api_bulk='1' if item.get('is_api_bulk_reference') else '0',
                                   has=(['saved'] if item['has_saved_content'] else []) + (['archived'] if item['has_archive_records'] else []) +
                                   (['links_only'] if not item['has_saved_content'] and not item['has_archive_records'] else []), __rank=rank)
                    self.line(stream, {'id': item['id'], 'dataset': 'sources', 'category': item['category'],
                        'state': item['jurisdiction'].upper() if len(item['jurisdiction']) == 2 else None,
                        'county_geoids': [], 'title': item['title'], 'source_url': item['url'],
                        'item': self.public(item), 'detail': self.public(detail), 'text': detail.get('description') or detail.get('notes') or '',
                        'filters': filters, 'ordinal': rank, 'artifacts': artifacts})
                    index.append({'id': item['id'], 'filters': filters, 'search': data['_search'][item['id']], 'registry_order': registry_order[item['id']]})
                    rank += 1
                if rank % 1000 == 0: print(json.dumps({'dataset': 'sources', 'exported': rank}), flush=True)
        with (self.output / 'sources.excluded.jsonl').open('w', encoding='utf8') as stream:
            for row in excluded:
                self.line(stream, {'id': row['id'], 'category': row.get('category'), 'reason': 'uncategorized_source_reference'})
            for row in pruned_links: self.line(stream, row)
        return self.finish('sources', 'Legal source directory', temp, rank, listing, index,
                           {'excluded_records': len(excluded), 'excluded_archive_links': len(pruned_links),
                            'original_import_summary': original['summary']})

    def mdls(self):
        server = self.server
        module, loaded = self.freeze('mdl_registry', '_load')
        state, reason = loaded
        if not state:
            raise ValueError('MDL source gate closed: ' + str(reason))
        overlays = [('state_proceedings', 'state_proceedings'), ('appearances', 'mdl_appearances'),
                    ('docket_documents', 'mdl_docket_documents'), ('docket_activity', 'mdl_docket_activity'),
                    ('cases', 'mdl_case_inventory'), ('counsel_directory', 'counsel_directory'),
                    ('verdict_reports', 'verdict_reports'), ('expert_rulings', 'expert_rulings')]
        for _, name in overlays:
            self.remember_module(importlib.import_module(name))
        docket_module, docket_loaded = self.freeze('mdl_docket_documents', '_load')
        docket_state, docket_reason = docket_loaded
        if not docket_state:
            raise ValueError('MDL docket-document source gate closed: ' + str(docket_reason))
        temp = self.output / 'mdls.jsonl.tmp'
        index = []
        first = module.listing({'status': 'all', 'limit': 200})
        with temp.open('w', encoding='utf8', newline='\n') as stream:
            for rank, row in enumerate(module._sort(list(state['mdls'].values()), 'pending_desc')):
                detail = module.detail(row['mdl_number'])
                for key, name in overlays:
                    detail[key] = server.judge_layer(name, 'for_mdl', row['mdl_number'])
                    if isinstance(detail[key], dict): detail[key].setdefault('mdl_number', row['mdl_number'])
                detail['docket_documents'] = categorized_docket_block(detail['docket_documents'],
                    docket_state['by_mdl'].get(row['mdl_number'], []), docket_module._compact)
                item = module._summary_row(row)
                artifacts = []
                for doc in detail['documents']:
                    source = state['docs'][doc['document_id']]
                    artifacts += self.verified_asset(doc['url'], module.DATA / source['raw_path'], module.original(doc['document_id']))
                judge = row.get('transferee_judge') or {}
                search = ' '.join(str(x) for x in (row.get('title'), judge.get('name_as_printed'), judge.get('name_by_number_report'),
                                                 row.get('master_docket'), row['mdl_number'], 'mdl-' + str(row['mdl_number']), 'mdl ' + str(row['mdl_number'])) if x).lower()
                filters = {'id': str(row['mdl_number']), 'status': row['status'], '__rank': rank,
                           'court': list({str(x).lower() for x in [row.get('cl_court_id'), row.get('district_code')] if x}),
                           'circuit': str(row.get('circuit') or '').lower(), 'litigation_type': str(row.get('litigation_type') or '').lower(),
                           'judge_resolved': 'true' if row.get('judge_links') else 'false',
                           'entity_id': [link['entity_id'] for link in row.get('judge_links') or [] if link.get('entity_id')],
                           'cl_person_id': str((row.get('cl_links') or {}).get('assigned_to_id') or '')}
                self.line(stream, {'id': str(row['mdl_number']), 'dataset': 'mdls', 'category': 'multidistrict_litigation',
                    'state': None, 'county_geoids': [], 'title': row['title'], 'source_url': None,
                    'item': self.public(item), 'detail': self.public(detail), 'text': '', 'filters': filters,
                    'ordinal': rank, 'artifacts': artifacts})
                index.append({'id': str(row['mdl_number']), 'filters': filters, 'search': search, 'item': self.public(item)})
        return self.finish('mdls', 'JPML multidistrict litigation', temp, len(index), first, index,
                           {'registry_summary': module.summary(), 'source_id_aliases': {r['id']: str(r['mdl_number']) for r in state['mdls'].values()}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', nargs='+', choices=['sources', 'mdls'], default=['sources', 'mdls'])
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    exporter = ReferenceExporter(args.output)
    try:
        for dataset in args.datasets: getattr(exporter, dataset)()
    finally:
        exporter.stack.close()


if __name__ == '__main__': main()
