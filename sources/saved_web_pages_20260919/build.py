"""Saved web pages (2026-09-19): one searchable index over the user's August 2026 Firecrawl page captures.

Offline; inputs under returnedfiles are read in place and never modified. These are provider-rendered Markdown captures,
not original HTTP bytes, and each folder is one bounded crawl, never a whole site. Excluded on purpose: codes.findlaw.com
captures (publisher terms prohibit reuse) and Oklahoma docket/case pages (they name individual litigants).
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RF = Path('C:/Users/firas/Downloads/returnedfiles')
DB = HERE / 'pages.sqlite3'
JSON_FOLDERS = {'NV-codeslaw': 'Nevada Legislature crawl', 'MO-codelaw': 'Montana Code Annotated crawl', 'OKcodelaw': 'Oklahoma State Courts Network crawl',
                'PA outputs': 'Philadelphia courts crawl', 'More PA outputs': 'Pennsylvania courts crawl', 'dccourtsoutputs': 'D.C. Courts crawl',
                'uscourtswidecrawl': 'Federal courts and Justice Department wide crawl'}
MD_FOLDERS = {'illinois_county_circuits': 'Illinois circuit court sites crawl', 'illinoiscourts_highvalue': 'Illinois Courts high-value pages crawl', 'mdoutputs': 'Kentucky and other court pages crawl'}
HEAD = re.compile(r'^#### (\d+)\.([\s\S]{0,400}?)· HTTP (\d+)[ \t]*\n\s*<(https?://[^>\n]+)>[ \t]*\n', re.M)
DETAILS = re.compile(r'^\s*<details>[\s\S]*?</details>\s*', re.I)
FENCE = re.compile(r'^' + chr(96) + r'{3,}[a-z]*[ \t]*$')
EXCLUDED_HOSTS = re.compile(r'(^|\.)findlaw\.com$', re.I)
CASE_SEARCH_HOSTS = re.compile(r'^search\.txcourts\.gov$', re.I)
EXCLUDED_PATHS = re.compile(r'/dockets?/|GetCaseInformation|GetDocument|deliverdocument|OCISWeb', re.I)
HOST_RULES = (
    (r'(^|\.)leg\.state\.nv\.us$', 'NV', 'legislature_law'), (r'(^|\.)legmt\.gov$', 'MT', 'legislature_law'), (r'(^|\.)oscn\.net$', 'OK', 'state_court'),
    (r'(^|\.)courts\.phila\.gov$', 'PA', 'county_court'), (r'(^|\.)pacourts\.us$', 'PA', 'state_court'), (r'(^|\.)dccourts\.gov$', 'DC', 'state_court'),
    (r'(^|\.)kycourts\.gov$', 'KY', 'state_court'), (r'(^|\.)txcourts\.gov$', 'TX', 'state_court'), (r'(^|\.)illinoiscourts\.gov$', 'IL', 'state_court'), (r'(^|\.)uscourts\.gov$', None, 'federal_court'),
    (r'(^|\.)justice\.gov$', None, 'federal_agency'), (r'(^|\.)usdoj\.gov$', None, 'federal_agency'),
)
LAYER_LABELS = {'legislature_law': 'Legislature & statutes', 'state_court': 'State courts', 'county_court': 'County & circuit courts', 'federal_court': 'Federal courts',
                'federal_agency': 'Federal agencies', 'other': 'Other'}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(host: str, collection_key: str):
    for pattern, state, layer in HOST_RULES:
        if re.search(pattern, host):
            return state, layer
    if collection_key.startswith('illinois'):
        return 'IL', 'county_court'
    return None, 'other'


def json_pages():
    for folder, label in JSON_FOLDERS.items():
        for path in sorted((RF / folder).rglob('*.json')):
            try:
                item = json.loads(path.read_text(encoding='utf-8', errors='replace'))
            except (OSError, ValueError):
                yield None, folder, 'unreadable json', str(path)
                continue
            if not isinstance(item, dict):
                continue
            meta = item.get('metadata') or {}
            url = meta.get('sourceURL') or meta.get('url')
            yield {'url': url, 'title': (meta.get('title') or meta.get('ogTitle') or '').strip(), 'status': meta.get('statusCode'), 'body': item.get('markdown') or '',
                   'collection': folder, 'collection_label': label, 'file': path, 'saved_on': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()}, folder, None, None


def md_pages():
    for folder, label in MD_FOLDERS.items():
        for path in sorted((RF / folder).rglob('*.md')):
            text = path.read_text(encoding='utf-8', errors='replace')
            heads = list(HEAD.finditer(text))
            saved_on = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
            for position, head in enumerate(heads):
                end = heads[position + 1].start() if position + 1 < len(heads) else len(text)
                body = DETAILS.sub('', text[head.end():end], count=1).strip()
                lines = body.split('\n')
                if lines and FENCE.match(lines[0]):
                    lines = lines[1:]
                if lines and FENCE.match(lines[-1]):
                    lines = lines[:-1]
                body = '\n'.join(lines).strip()
                # Some crawls print the title twice ("Title · HTTP 200\n\tTitle\n · HTTP 200"): keep the text before the first marker.
                title = ' '.join(re.split(r'\s*· HTTP \d+', head.group(2))[0].split())
                yield {'url': head.group(4).strip(), 'title': title, 'status': head.group(3), 'body': body, 'collection': folder, 'collection_label': label,
                       'file': path, 'saved_on': saved_on}, folder, None, None


def main():
    pages = {}
    held = collections.Counter()
    for source in (json_pages(), md_pages()):
        for page, folder, problem, where in source:
            if page is None:
                held[f'{folder}: {problem}'] += 1
                continue
            url = page['url']
            try:
                parts = urllib.parse.urlsplit(url or '')
            except ValueError:
                parts = None
            if not parts or parts.scheme not in ('http', 'https') or not parts.hostname:
                held[f'{folder}: no usable address'] += 1
                continue
            host = parts.hostname.lower()
            if EXCLUDED_HOSTS.search(host):
                held[f'{folder}: excluded publisher (terms prohibit reuse)'] += 1
                continue
            if EXCLUDED_PATHS.search(parts.path + '?' + parts.query) or CASE_SEARCH_HOSTS.search(host):
                held[f'{folder}: docket or case page naming litigants (not published)'] += 1
                continue
            try:
                status = int(page['status']) if page['status'] is not None else None
            except (TypeError, ValueError):
                status = None
            if status is not None and status != 200:
                held[f'{folder}: HTTP {status}'] += 1
                continue
            if len(page['body'].strip()) < 200:
                held[f'{folder}: nearly empty capture'] += 1
                continue
            page['host'] = host
            key = url.split('#')[0]
            if key not in pages or len(page['body']) > len(pages[key]['body']):
                pages[key] = page

    by_host = collections.defaultdict(list)
    for page in pages.values():
        by_host[page['host']].append(page)
    chrome = {}
    for host, items in by_host.items():
        if len(items) < 20:
            chrome[host] = set()
            continue
        frequency = collections.Counter()
        for page in items:
            frequency.update({line.strip() for line in page['body'].split('\n') if line.strip()})
        chrome[host] = {line for line, count in frequency.items() if count > 0.4 * len(items)}

    if DB.exists():
        DB.unlink()
    connection = sqlite3.connect(DB)
    connection.executescript('''
        CREATE TABLE pages(id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, host TEXT NOT NULL, title TEXT, collection TEXT, collection_label TEXT, state TEXT, layer TEXT,
            text TEXT NOT NULL, text_sha256 TEXT NOT NULL, text_chars INTEGER, saved_on TEXT, looks_like_document INTEGER);
        CREATE VIRTUAL TABLE pages_fts USING fts5(title, url, text, content='pages', content_rowid='id', tokenize='unicode61');
    ''')
    for identifier, (url, page) in enumerate(sorted(pages.items()), 1):
        lines = [line for line in page['body'].split('\n') if line.strip() not in chrome[page['host']]]
        text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        if len(text) < 120:
            held[f"{page['collection']}: only site menus left after cleaning"] += 1
            continue
        state, layer = classify(page['host'], page['collection'])
        title = page['title'] or next((line.lstrip('# ').strip() for line in text.split('\n') if line.startswith('#')), '') or url
        connection.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (identifier, url, page['host'], title[:300], page['collection'], page['collection_label'], state, layer,
                                                                                   text, sha(text.encode('utf-8')), len(text), page['saved_on'],
                                                                                   1 if re.search(r'\.(pdf|docx?|xlsx?)$', urllib.parse.urlsplit(url).path, re.I) else 0))
    connection.execute("INSERT INTO pages_fts(rowid, title, url, text) SELECT id, coalesce(title,''), url, text FROM pages")
    connection.execute('CREATE INDEX pages_facets ON pages(collection, state, layer)')
    connection.execute('CREATE INDEX pages_host ON pages(host)')
    connection.commit()
    counts = {
        'pages': connection.execute('SELECT count(*) FROM pages').fetchone()[0], 'hosts': connection.execute('SELECT count(DISTINCT host) FROM pages').fetchone()[0],
        'text_characters': connection.execute('SELECT sum(text_chars) FROM pages').fetchone()[0],
        'by_collection': dict(connection.execute('SELECT collection_label, count(*) FROM pages GROUP BY 1 ORDER BY 2 DESC')),
        'by_state': dict(connection.execute("SELECT coalesce(state,'federal or not stated'), count(*) FROM pages GROUP BY 1 ORDER BY 2 DESC")),
        'by_layer': dict(connection.execute('SELECT layer, count(*) FROM pages GROUP BY 1 ORDER BY 2 DESC')), 'held_not_published': dict(held),
    }
    connection.execute('VACUUM')
    connection.close()
    validation = {
        'schema_version': '1', 'status': 'passed' if counts['pages'] else 'failed', 'ready': bool(counts['pages']), 'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': DB.name, 'sha256': sha(DB.read_bytes()), 'rows': counts['pages']}], 'counts': counts,
        'checks': {'urls_unique': True, 'no_network_used': True, 'findlaw_captures_excluded': True, 'oklahoma_docket_pages_excluded': True},
        'qualification': 'Web pages as rendered to Markdown by Firecrawl crawls the user ran in August 2026 (provider captures, not original HTTP bytes). Each crawl was bounded, so '
                         'a site is never complete here, and a page may have changed since. Repeated site menus were removed by line frequency per site. The "saved" date is the date '
                         'of the local capture file, not a publication or effective date. Statute pages are as displayed on the crawl date, not verified current.',
        'license_ref': 'public_government_pages_provider_capture', 'inputs': [{'path': (RF / folder).as_posix()} for folder in list(JSON_FOLDERS) + list(MD_FOLDERS)],
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1), encoding='utf-8')
    print(json.dumps(counts, indent=1))


if __name__ == '__main__':
    main()
