"""Export read-only county, judge and historical-person API snapshots.

Categorical memberships come from the native indexes. No name matching,
identity merging, crawling, database rebuild or remote operation is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'delivery/archive-directory'))
OUTPUT = ROOT / '_transfer_scratch/supabase_export'


def stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class Exporter:
    def __init__(self, output=OUTPUT):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.server = importlib.import_module('server')
        self.watch = {}
        self.stack = ExitStack()
        self.photo_cache = {}

    def remember(self, path):
        path = Path(path).resolve()
        if path.is_file():
            self.watch.setdefault(path, stamp(path))

    def remember_module(self, module):
        for field in ('DB', 'DIRECTORY'):
            path = getattr(module, field, None)
            if isinstance(path, Path):
                self.remember(path)
        folder = getattr(module, 'FOLDER', None) or getattr(module, 'DATA', None)
        if isinstance(folder, Path) and folder.is_dir():
            for file in folder.iterdir():
                if file.is_file() and file.suffix in {'.json', '.jsonl', '.sqlite3'}:
                    self.remember(file)

    def freeze(self, module_name, function):
        module = importlib.import_module(module_name)
        self.remember_module(module)
        value = getattr(module, function)()
        self.stack.enter_context(patch.object(module, function, return_value=value))
        return module, value

    def ensure_unchanged(self):
        if any(not path.is_file() or stamp(path) != old for path, old in self.watch.items()):
            raise ValueError('A source snapshot changed during export')

    def public(self, value):
        # Public semantic labels such as DOJ section_path are not private paths.
        # Match the deployment scrubber's exact sensitive keys and path values.
        if str(ROOT / 'deploy') not in sys.path:
            sys.path.insert(0, str(ROOT / 'deploy'))
        from import_catalog import normalize
        return normalize({'id': 'public', 'dataset': 'public', 'category': 'directories', 'item': value})['item']

    def artifact(self, url):
        if not url:
            return []
        if url in self.photo_cache:
            return self.photo_cache[url]
        asset = None
        if url.startswith('/judge-images/'):
            asset = self.server.judges.image_file(unquote(url.split('/judge-images/', 1)[1]))
        elif url.startswith('/library-assets/'):
            found = self.server.local_library.asset(url.split('/library-assets/', 1)[1])
            asset = found[:2] if found else None
        elif url.startswith('/supplement-files/judge_portraits/'):
            module = importlib.import_module('judge_portraits')
            found = module.original(url.rsplit('/', 1)[1])
            if found:
                data, mime, name = found
                path = (module.DATA / 'images' / name).resolve()
                if path.read_bytes() != data:
                    raise ValueError('Portrait changed after verification')
                asset = path, mime
        if not asset:
            if url.startswith('/'):
                raise ValueError('Published portrait/visual has no verified local artifact: ' + url)
            return []
        path, mime = asset
        self.remember(path)
        data = path.read_bytes()
        result = [{'url': url, 'local_path': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                   'bytes': len(data), 'mime': mime, 'role': 'image'}]
        self.photo_cache[url] = result
        return result

    def finish(self, dataset, label, temp, count, listing, index, extra=None):
        self.ensure_unchanged()
        final = self.output / (dataset + '.jsonl')
        digest = hashlib.sha256(temp.read_bytes()).hexdigest()
        temp.replace(final)
        receipt = {'status': 'passed', 'dataset': dataset, 'records': count, 'sha256': digest,
                   'bytes': final.stat().st_size, 'created_at': datetime.now(timezone.utc).isoformat(),
                   'network_requests': 0, 'remote_writes': 0, 'source_snapshots_unchanged': True}
        descriptor = {'id': dataset, 'label': label, 'expected_records': count, 'export_jsonl_sha256': digest,
                      'listing': {k: v for k, v in listing.items() if k not in {'items', 'total', 'page', 'limit', 'offset', 'filters'}},
                      'filter_index': index, 'summary': receipt}
        if extra:
            descriptor.update(extra)
        for suffix, value in [('.receipt.json', receipt), ('.dataset.json', descriptor)]:
            final.with_suffix(suffix).write_bytes((json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf8'))
        print(json.dumps(receipt), flush=True)
        return receipt

    @staticmethod
    def line(stream, record):
        stream.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')

    def counties(self):
        server = self.server
        self.remember(server.DB)
        county_data = server.county_litigation.load()
        if not county_data.get('gate'):
            raise ValueError('County litigation enrichment gate is closed')
        for path in county_data['watched']:
            self.remember(path)
        litigation_counts = server.county_litigation.county_counts()
        self.stack.enter_context(patch.object(server.county_litigation, 'county_counts', return_value=litigation_counts))
        coverage = server.trellis_coverage.load()
        self.stack.enter_context(patch.object(server.trellis_coverage, 'load', return_value=coverage))
        registry_geoids = server.county_registry.covered_geoids()
        self.stack.enter_context(patch.object(server.county_registry, 'covered_geoids', return_value=registry_geoids))
        self.remember_module(server.trellis_coverage)
        self.remember_module(server.county_registry)
        self.remember_module(server.local_library)
        with server.ro(server.DB) as connection:
            originals = {r['geoid']: json.loads(r['payload']) for r in connection.execute('SELECT geoid,payload FROM counties')}
        first = server.query_counties({'page': 1, 'limit': 100})
        if first['total'] != len(originals):
            raise ValueError('County inventory count differs from the current listing')
        temp = self.output / 'counties.jsonl.tmp'
        index = []
        with temp.open('w', encoding='utf8', newline='\n') as stream:
            rank = 0
            for page in range(1, (first['total'] + 99) // 100 + 1):
                listing = first if page == 1 else server.query_counties({'page': page, 'limit': 100})
                for item in listing['items']:
                    key = item['geoid']
                    original = originals[key]
                    availability = ['local_resources' if (original.get('local_resources') or 0) > 0 else 'no_local_resources']
                    if (original.get('saved_profiles') or 0) + (original.get('saved_sites') or 0) > 0 or item.get('has_court_registry') or item.get('has_trellis_detail'):
                        availability.append('saved_information')
                    if item.get('has_court_registry'): availability.append('court_registry')
                    if item.get('has_trellis_detail'): availability.append('trellis_details')
                    if item.get('litigation_resources'): availability.append('litigation_resources')
                    filters = {'id': key, 'state': item['state'], 'availability': availability, '__rank': rank}
                    public = self.public(item)
                    self.line(stream, {'id': key, 'dataset': 'counties', 'category': 'county_directory',
                        'state': item['state'], 'county_geoids': [key], 'title': item['name'],
                        'source_url': item.get('website') or None, 'item': public, 'detail': public,
                        'text': '', 'filters': filters, 'ordinal': rank,
                        'artifacts': self.artifact((item.get('visual') or {}).get('image_url'))})
                    index.append({'id': key, 'name': item['name'], 'filters': filters})
                    rank += 1
        return self.finish('counties', 'County directory', temp, rank, first, index)

    def judges(self):
        server = self.server
        module = server.judges
        self.remember_module(module)
        self.freeze('judge_aliases', '_load')
        structured, structured_state = self.freeze('judge_structured', '_load')
        self.freeze('judge_evidence', '_state')
        self.freeze('judge_portraits', '_state')
        self.freeze('mdl_registry', '_load')
        try:
            self.freeze('judge_disclosures', '_load')
        except (OSError, ValueError):
            pass  # The current server intentionally exposes this as unavailable.
        reports = module.judge_report_links.index()
        self.remember_module(module.judge_report_links)
        first = module.listing({'page': 1, 'limit': 60})
        with module.connect() as connection:
            native = [dict(r) for r in connection.execute('SELECT * FROM profiles ORDER BY score DESC,name COLLATE NOCASE,id')]
            memberships = defaultdict(lambda: defaultdict(list))
            for key, table, field in [('state', 'states', 'state'), ('system', 'systems', 'system'), ('court', 'courts', 'court')]:
                for row in connection.execute('SELECT id,' + field + ' AS value FROM ' + table):
                    memberships[row['id']][key].append(row['value'])
        if first['total'] != len(native):
            raise ValueError('Judge listing count changed')
        structured_memberships = defaultdict(lambda: defaultdict(list))
        if structured_state:
            for key, values in [('status', structured_state.status), ('role', structured_state.role), ('president', structured_state.president)]:
                for value, members in values.items():
                    for entity in members:
                        structured_memberships[entity][key].append(value)
        aliases = {}
        index = []
        temp = self.output / 'judges.jsonl.tmp'
        portrait_routes = set()
        with temp.open('w', encoding='utf8', newline='\n') as stream:
            for rank, row in enumerate(native):
                item = json.loads(row['card'])
                entity = item.get('entity_id') or item['id']
                item['report_link_count'] = len(reports.get(item.get('entity_id'), []))
                if not item.get('photo_url'):
                    item.update(server.judge_layer('judge_portraits', 'for_judge', entity) or {})
                detail = module.profile(item['id'])
                if not detail:
                    raise ValueError('Native judge profile is unavailable: ' + item['id'])
                detail['mdls'] = server.mdl_registry.for_judge(entity)
                detail['structured'] = server.judge_overlay(entity)
                detail['evidence'] = server.judge_layer('judge_evidence', 'evidence_for', entity)
                detail['disclosures'] = server.judge_layer('judge_disclosures', 'for_judge', entity)
                if not detail.get('photo_url'):
                    detail.update(server.judge_layer('judge_portraits', 'for_judge', entity) or {})
                features = [name for name, field in [('details', 'has_details'), ('photo', 'has_photo'), ('biography', 'has_biography'), ('analysis', 'analysis_count')] if row[field] > 0]
                if item.get('entity_id') in reports: features.append('reports')
                filters = {'id': item['id'], **dict(memberships[item['id']]), **dict(structured_memberships.get(entity, {})), 'has': features, '__rank': rank}
                if item.get('entity_id'):
                    aliases[item['entity_id']] = item['id']
                portrait = self.artifact(detail.get('photo_url'))
                if portrait: portrait_routes.add(portrait[0]['url'])
                public_item, public_detail = self.public(item), self.public(detail)
                native_aliases = [x['alias'] for x in detail.get('aliases') or [] if x.get('alias')]
                text = detail.get('biography') or ''
                self.line(stream, {'id': item['id'], 'dataset': 'judges', 'category': 'judge_profile', 'state': item.get('state'),
                    'county_geoids': [], 'title': item['name'], 'source_url': item.get('source_url') or next((r['url'] for r in detail.get('sources') or [] if r.get('url')), None),
                    'item': public_item, 'detail': public_detail, 'text': text, 'filters': filters, 'ordinal': rank, 'artifacts': portrait})
                index.append({'id': item['id'], 'name': item['name'], 'filters': filters, 'search': row['search'], 'aliases': native_aliases})
                if (rank + 1) % 1000 == 0: print(json.dumps({'dataset': 'judges', 'exported': rank + 1}), flush=True)
        with server.ro(server.DB) as connection:
            known = {r['id'] for r in native}
            for row in connection.execute('SELECT id,preferred_id FROM display_groups'):
                if row['preferred_id'] in known: aliases[row['id']] = row['preferred_id']
        return self.finish('judges', 'Judge directory', temp, len(native), first, index,
                           {'id_aliases': aliases, 'validated_portrait_routes': sorted(portrait_routes)})

    def people(self):
        server = self.server
        module = server.people
        self.remember_module(module)
        info = module.info()
        if not info.get('ready'):
            raise ValueError('Historical people source gate is closed')
        self.stack.enter_context(patch.object(module, 'info', return_value=info))
        first = module.listing({'include_aliases': '1', 'limit': 60, 'offset': 0})
        with module.connect() as connection:
            native = {r['person_id']: dict(r) for r in connection.execute('SELECT * FROM people_search')}
            courts = defaultdict(set)
            for row in connection.execute("SELECT person_id,court_id FROM positions WHERE court_id<>''"):
                courts[row['person_id']].add(row['court_id'])
        if first['total'] != len(native): raise ValueError('Historical people listing count changed')
        temp = self.output / 'people.jsonl.tmp'
        index = []
        with temp.open('w', encoding='utf8', newline='\n') as stream:
            rank = 0
            for offset in range(0, first['total'], 60):
                listing = first if offset == 0 else module.listing({'include_aliases': '1', 'limit': 60, 'offset': offset})
                for item in listing['items']:
                    item.update(server.judge_layer('judge_portraits', 'for_person', item['id']) or {})
                    detail = module.profile(item['id'])
                    if not detail or not detail.get('ready'): raise ValueError('Historical person is unavailable')
                    detail.update(server.judge_layer('judge_portraits', 'for_person', item['id']) or {})
                    filters = {'id': item['id'], 'court': sorted(courts[item['id']]), 'is_alias': '1' if item['is_alias'] else '0', '__rank': rank}
                    text = ' '.join([item['name'], item.get('career_summary') or '', item.get('education_summary') or ''])
                    self.line(stream, {'id': item['id'], 'dataset': 'people', 'category': 'historical_biography',
                        'state': None, 'county_geoids': [], 'title': item['name'], 'source_url': None,
                        'item': self.public(item), 'detail': self.public(detail), 'text': text, 'filters': filters,
                        'ordinal': rank, 'artifacts': self.artifact(detail.get('photo_url'))})
                    index.append({'id': item['id'], 'filters': filters, 'search': native[item['id']]['search_text']})
                    rank += 1
                if rank % 3000 == 0: print(json.dumps({'dataset': 'people', 'exported': rank}), flush=True)
        return self.finish('people', 'Historical biographies', temp, rank, first, index)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--datasets', nargs='+', choices=['counties', 'judges', 'people'], default=['counties', 'judges', 'people'])
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    exporter = Exporter(args.output)
    try:
        for dataset in args.datasets:
            getattr(exporter, dataset)()
    finally:
        exporter.stack.close()


if __name__ == '__main__':
    main()
