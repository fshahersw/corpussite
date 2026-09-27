"""Saved uscourts.gov pages (2026-09-19): split the user's August 2026 Firecrawl crawl back into one page per URL.

Offline. The input zip is read in memory and never modified. These are provider-rendered Markdown captures, not original
HTTP bytes. A page is published only when its header number and URL agree with the crawl's own index; others are held.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import re
import sqlite3
import tarfile
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = Path('C:/Users/firas/Downloads/filesccsdfsdfs.zip')
DB = HERE / 'uscourts_pages.sqlite3'
FENCE = chr(96) * 3
HEAD = re.compile(r'^#### (\d+)\. (.*?) · HTTP (\d+)\n<(https?://[^>\n]+)>\n\n(' + chr(96) + r'{3,})\n', re.M)
LINK = re.compile(r'\]\((https?://[^)\s]+)\)')
DOCUMENT = re.compile(r'\.(pdf|xlsx?|docx?|csv|zip)$', re.I)
SECTION_LABELS = {
    'statistics-reports': 'Statistics & reports', 'data-news': 'Data & news', 'judges-judgeships': 'Judges & judgeships',
    'about-federal-courts': 'About federal courts', 'rules-policies': 'Rules & policies', 'court-records': 'Court records',
    'forms-rules': 'Forms & rules', 'services-forms': 'Services & forms', 'forms': 'Forms', 'court-programs': 'Court programs',
    'administration-policies': 'Administration & policies', 'news': 'News', 'careers': 'Careers', 'topics': 'Topics',
    'educational-resources': 'Educational resources', 'federal-court-finder': 'Court finder', 'sites': 'Files (reports and documents)',
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_parts(parts):
    """Yield (n, title, status, url, body) for every page header found, in crawl order."""
    for text in parts:
        heads = list(HEAD.finditer(text))
        for position, head in enumerate(heads):
            end = heads[position + 1].start() if position + 1 < len(heads) else len(text)
            body = text[head.end():end].rstrip()
            fence = head.group(5)
            if body.endswith(fence):
                body = body[:-len(fence)].rstrip()
            yield int(head.group(1)), head.group(2).strip(), int(head.group(3)), head.group(4).strip(), body


def section_of(url: str):
    path = urllib.parse.urlsplit(url).path.strip('/')
    first = path.split('/')[0] if path else 'home'
    return first, SECTION_LABELS.get(first, first.replace('-', ' ').capitalize() if first else 'Home')


def main():
    archive = zipfile.ZipFile(SOURCE)
    tar = tarfile.open(fileobj=io.BytesIO(archive.read('uscourts_crawl_1404.tar.gz')), mode='r:gz')
    index = {int(item['n']): item for item in json.load(tar.extractfile('uscourts/_index.json'))}
    meta = json.load(tar.extractfile('uscourts/_meta.json'))
    names = sorted(name for name in tar.getnames() if re.search(r'part\d+\.md$', name))
    parts = [tar.extractfile(name).read().decode('utf-8', 'replace') for name in names]
    pages, held = [], []
    for number, title, status, url, body in split_parts(parts):
        listed = index.get(number)
        if not listed or listed.get('url') != url:
            held.append({'n': number, 'url': url, 'reason': 'header does not match the crawl index'})
            continue
        pages.append({'n': number, 'title': listed.get('title') or title, 'status': status, 'url': url, 'body': body})
    seen = {page['n'] for page in pages}
    held += [{'n': number, 'url': item.get('url'), 'reason': 'listed in the crawl index but no page body found'} for number, item in index.items() if number not in seen]

    # Lines that repeat on more than 40% of pages are site chrome (banner, menus, footer), not page content.
    frequency = collections.Counter()
    for page in pages:
        frequency.update({line.strip() for line in page['body'].split('\n') if line.strip()})
    chrome = {line for line, count in frequency.items() if count > 0.4 * len(pages)}

    statistics = {}
    tables = ROOT / 'sources' / 'federal_court_statistics_20260919' / 'tables.jsonl'
    if tables.exists():
        for line in open(tables, encoding='utf-8'):
            if line.strip():
                table = json.loads(line)
                for field in ('source_url', 'source_page'):
                    if table.get(field):
                        statistics.setdefault(table[field], []).append(table['table_id'])

    if DB.exists():
        DB.unlink()
    connection = sqlite3.connect(DB)
    connection.executescript('''
        CREATE TABLE pages(id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT, section TEXT, section_label TEXT, http_status INTEGER, text TEXT NOT NULL,
            text_sha256 TEXT NOT NULL, text_chars INTEGER, raw_chars INTEGER, looks_like_document INTEGER, document_links TEXT, document_link_count INTEGER,
            statistics_tables TEXT, crawled_on TEXT);
        CREATE VIRTUAL TABLE pages_fts USING fts5(title, text, content='pages', content_rowid='id', tokenize='unicode61');
    ''')
    crawled = (meta.get('completedAt') or '')[:10]
    url_rows = []
    for page in pages:
        lines = [line for line in page['body'].split('\n') if line.strip() not in chrome]
        text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        links = []
        for link in LINK.findall(page['body']):
            link = link.rstrip('.,;')
            if DOCUMENT.search(urllib.parse.urlsplit(link).path) and link not in links:
                links.append(link)
        matched = sorted({table for key in [page['url']] + links for table in statistics.get(key, [])})
        section, label = section_of(page['url'])
        connection.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            page['n'], page['url'], page['title'], section, label, page['status'], text, sha(text.encode('utf-8')), len(text), len(page['body']),
            1 if DOCUMENT.search(urllib.parse.urlsplit(page['url']).path) else 0, json.dumps(links[:300]), len(links), json.dumps(matched), crawled))
        url_rows += [{'url': link, 'parent_url': page['url']} for link in links]
    connection.execute("INSERT INTO pages_fts(rowid, title, text) SELECT id, coalesce(title,''), text FROM pages")
    connection.commit()
    counts = {
        'pages_in_crawl_index': len(index), 'pages_published': len(pages), 'pages_held': len(held),
        'pages_with_document_links': connection.execute('SELECT count(*) FROM pages WHERE document_link_count>0').fetchone()[0],
        'document_links': len(url_rows), 'distinct_document_links': len({row['url'] for row in url_rows}),
        'pages_matched_to_parsed_statistics_tables': connection.execute("SELECT count(*) FROM pages WHERE statistics_tables<>'[]'").fetchone()[0],
        'site_chrome_lines_removed': len(chrome), 'by_section': dict(connection.execute('SELECT section_label, count(*) FROM pages GROUP BY 1 ORDER BY 2 DESC')),
        'crawl_completed_at': meta.get('completedAt'),
    }
    connection.execute('VACUUM')
    connection.close()
    (HERE / 'held.jsonl').write_text(''.join(json.dumps(item) + '\n' for item in held), encoding='utf-8')
    with open(HERE / 'document_links.jsonl', 'w', encoding='utf-8', newline='\n') as handle:
        for row in url_rows:
            handle.write(json.dumps(row) + '\n')
    ready = bool(pages) and len(held) <= 0.02 * len(index)
    validation = {
        'schema_version': '1', 'status': 'passed' if ready else 'failed', 'ready': ready, 'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': name, 'sha256': sha((HERE / name).read_bytes()), 'rows': rows} for name, rows in (
            ('uscourts_pages.sqlite3', len(pages)), ('held.jsonl', len(held)), ('document_links.jsonl', len(url_rows)))],
        'counts': counts,
        'checks': {'every_published_page_matches_crawl_index_number_and_url': True, 'no_network_used': True, 'urls_unique': True},
        'qualification': 'Pages of www.uscourts.gov as rendered to Markdown by a Firecrawl crawl that completed on %s (one crawl seeded from the Court Website Links page; '
                         'not the whole site, and not original HTTP bytes). Repeated site menus and banners were removed by line frequency. A page may have changed since; '
                         'figures quoted inside a page are the publisher\'s as of that page.' % (crawled or 'an unrecorded date'),
        'license_ref': 'uscourts_gov_public_pages_provider_capture', 'inputs': [{'path': SOURCE.as_posix(), 'sha256': sha(SOURCE.read_bytes())}],
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1), encoding='utf-8')
    print(json.dumps(counts, indent=1))


if __name__ == '__main__':
    main()
