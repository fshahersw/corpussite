"""Import a user-provided directory as data, independently reconcile Markdown."""
import hashlib
import ipaddress
import json
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MD = Path('C:/Users/firas/Downloads/publicLaw_directory.md')
DB = Path('C:/Users/firas/Downloads/registry_v06_1.sqlite')
LINK = re.compile(r'^- \[(.*?)\]\((\S+)\)(?:\s+(.*))?$')
AS_OF = '2026-08-19'


def sha(data): return hashlib.sha256(data).hexdigest()


def public_url(url):
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower()
        if p.scheme not in ('https', 'http') or not host or p.username is not None or p.password is not None:
            return False
        if host == 'localhost' or '.' not in host or host.endswith(('.localhost', '.local', '.internal')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def parse_markdown(text):
    observations = []
    section = subsection = ''
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith('## '):
            section, subsection = line[3:].strip(), ''
        elif line.startswith('### '):
            subsection = line[4:].strip()
        elif line.startswith('- ['):
            match = LINK.fullmatch(line)
            if not match: raise ValueError(f'Unparsed Markdown source line {n}')
            tail = match[3] or ''
            tag = re.match(r'^\[([^\]]*)\]\s*', tail)
            tags = [v.strip() for v in tag[1].split(',') if v.strip()] if tag else []
            notes = tail[tag.end():] if tag else tail
            observations.append({'line_number': n, 'title': match[1], 'url': match[2],
                                 'section': section, 'subsection': subsection, 'tags': tags,
                                 'notes': notes.removeprefix('- ').strip(), 'verbatim': line,
                                 'source_as_of': AS_OF})
    return observations


def access_method(record):
    kind = record['content_kind']
    return 'api' if kind == 'api' else 'download' if kind in ('pdf','xlsx','zip','docx','json','csv') else 'web'


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / 'validation.json').write_text(json.dumps({'ready': False, 'status': 'building'}))
    md_bytes = MD.read_bytes()
    md_text = md_bytes.decode('utf-8-sig')
    observations = parse_markdown(md_text)
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute('SELECT * FROM registry')]
    claims = {'source_records': len(rows), 'historical_verified_claims': sum(bool(x['verified_date']) for x in rows),
              'categorized': sum(bool(x['record_category']) for x in rows), 'task_tagged': sum(bool(x['task_family']) for x in rows)}
    expected = {'source_records': 9348, 'historical_verified_claims': 8349, 'categorized': 5700, 'task_tagged': 8510}
    if claims != expected or len(observations) != expected['source_records']:
        raise ValueError('Markdown header and registry counts do not reconcile')
    if not re.search(r'9348 records, 8349 liveness-verified, 5700 categorized, 8510 task-tagged', md_text):
        raise ValueError('Directory header differs from inspected draft')
    if Counter(x['url'] for x in rows) != Counter(x['url'] for x in observations):
        raise ValueError('Independent Markdown and SQLite URL observations differ')
    if len({x['id'] for x in rows}) != len(rows): raise ValueError('Duplicate native record ID')
    by_url = {}
    for o in observations: by_url.setdefault(o['url'], []).append(o)
    county_file = ROOT / 'delivery/focused_legal_corpus/counties/counties.jsonl'
    jurisdiction_labels = {'us': 'United States / federal', 'multi': 'Multiple jurisdictions',
                           'pr': 'Puerto Rico', 'vi': 'U.S. Virgin Islands', 'dc': 'District of Columbia'}
    with county_file.open(encoding='utf-8') as stream:
        for row in map(json.loads, stream): jurisdiction_labels[row['usps'].lower()] = row['state']
    entries = []
    for r in rows:
        obs = by_url[r['url']]
        tags = sorted({v for v in [r['task_family'], r['layer'], r['record_category'], *[t for o in obs for t in o['tags']]] if v})
        verified = bool(r['verified_date'])
        entry = {'id': 'pld-' + r['id'], 'title': r['name'] or obs[0]['title'], 'url': r['url'],
                 'host': urlsplit(r['url']).hostname or r['domain'], 'jurisdiction': r['jurisdiction'],
                 'jurisdiction_label': jurisdiction_labels.get(r['jurisdiction'], r['jurisdiction']),
                 'category': r['record_category'] or 'uncategorized', 'original_category': r['record_category'] or None, 'tags': tags,
                 'task_family': r['task_family'] or None, 'layer': r['layer'] or None,
                 'is_api_bulk_reference': r['layer'] == 'api_bulk',
                 'taxonomy_version': 'source-category-v1',
                 'section': r['section'] or obs[0]['section'], 'subsection': r['subsection'] or obs[0]['subsection'],
                 'access_method': access_method(r), 'access_method_basis': 'Source content_kind: ' + r['content_kind'],
                 'access_requirements': r['access'] or None,
                 'verification_status': 'historically_verified' if verified else 'not_verified_in_source',
                 'verification_claim': {'verified_date': r['verified_date'] or None, 'http_status': r['http_status'] or None,
                                        'qualification': 'Historical source claim; not a fresh availability check'},
                 'source_as_of': AS_OF, 'notes': r['notes'], 'description': r['description'],
                 'caveat': r['caveat'], 'source_type': r['source_type'], 'content_kind': r['content_kind'],
                 'api_hint': r['api_hint'] or None, 'crawl_policy': r['crawl_policy'] or None,
                 'has_saved_content': False, 'captures': [], 'observations': obs, 'source_record': r,
                 'public_url_candidate': public_url(r['url']),
                 'provenance': {'markdown_sha256': sha(md_bytes), 'native_id': r['id'],
                                'jurisdiction_basis': 'Explicit structured registry jurisdiction; no county inference'}}
        entries.append(entry)
    entries.sort(key=lambda r: (r['title'].casefold(), r['id']))
    labels = {'access_methods': {'api': 'API reference', 'download': 'Downloadable file', 'web': 'Web page'},
              'statuses': {'historically_verified': 'Source reports verified on August 19', 'not_verified_in_source': 'Not verified in source snapshot'}}
    facets = {}
    for facet, key in (('jurisdictions','jurisdiction'),('categories','category'),('access_methods','access_method'),('statuses','verification_status')):
        counts = Counter(r[key] for r in entries)
        facets[facet] = [{'value': v, 'label': jurisdiction_labels.get(v, v) if facet == 'jurisdictions' else labels.get(facet, {}).get(v, v.replace('_',' ').capitalize()), 'count': count} for v, count in sorted(counts.items())]
    summary = {**claims, 'unique_urls': len(by_url), 'markdown_observations': len(observations),
               'source_as_of': AS_OF, 'draft': True, 'freshly_verified': 0, 'saved_content_records': 0,
               'explicit_api_records': sum(r['content_kind'] == 'api' for r in rows),
               'api_bulk_layer_records': sum(r['layer'] == 'api_bulk' for r in rows),
               'api_hints_present': sum(bool(r['api_hint']) for r in rows),
               'taxonomy_version': 'source-category-v1',
               'taxonomy_qualification': 'Categories and tags preserve the draft source taxonomy and may be inaccurate. Only blank categories become uncategorized; no semantic recategorization or inferred county joins.',
               'unsafe_url_records': sum(not r['public_url_candidate'] for r in entries),
               'qualification': 'Imported directory references, not newly downloaded documents. Verification and access fields are historical draft claims. Saved-content matching has not been performed; only linked captures would set has_saved_content.',
               'structured_source': 'registry_v06_1.sqlite; exact row counts and URL multiset reconcile with publicLaw_directory.md'}
    payload = json.dumps({'entries': entries, 'facets': facets, 'summary': summary}, ensure_ascii=False, separators=(',',':')).encode('utf-8')
    (OUT / 'catalog.json').write_bytes(payload)
    with (OUT / 'observations.jsonl').open('w',encoding='utf-8') as stream:
        for o in observations: stream.write(json.dumps(o,ensure_ascii=False)+'\n')
    shutil.copyfile(MD, OUT / 'publicLaw_directory.source.md')
    shutil.copyfile(DB, OUT / 'registry_v06_1.source.sqlite')
    validation = {'status': 'passed', 'ready': True, 'verified_at': datetime.now(timezone.utc).isoformat(),
                  'catalog_sha256': sha(payload), 'source_markdown_sha256': sha(md_bytes),
                  'source_sqlite_sha256': sha((OUT / 'registry_v06_1.source.sqlite').read_bytes()),
                  'observations_sha256': sha((OUT / 'observations.jsonl').read_bytes()),
                  'checks': {'all_bullets_parsed': True, 'header_counts_match': True, 'exact_url_multiset_match': True, 'native_ids_unique': True},
                  'summary': summary}
    (OUT / 'validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    print(json.dumps(validation))

if __name__ == '__main__': main()
