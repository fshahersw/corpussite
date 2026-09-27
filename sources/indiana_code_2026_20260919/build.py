"""Indiana Code 2026 (2026-09-19): section-level index of the user's local copy of the 2026 Indiana Code HTML edition.

Offline; the input folder is read in place and never modified. The HTML edition marks every title, article, chapter and
section with a class and the code citation as its id, so sections are split on those markers only; no citation is invented.
This is the 2026 edition as downloaded, not a statement of what is in force on any later date.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = Path('C:/Users/firas/Downloads/returnedfiles/2026-Indiana-Code')
HTML_DIR = SOURCE / '2026_Indiana_Code_HTML'
DB = HERE / 'indiana_code.sqlite3'
MARK = re.compile(r'<div class="(title|article|chapter|section)" id="([^"]+)"[^>]*>(.*?)<div style="clear: both;"></div></div>', re.S)
SPAN = re.compile(r'<span id="(ic_number|shortdescription)"[^>]*>(.*?)</span>', re.S)
TAG = re.compile(r'<[^>]+>')
BLOCK_END = re.compile(r'</p>|<br\s*/?>|</div>|</tr>', re.I)
HISTORY = re.compile(r'<i>(.*?)</i>', re.S)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def plain(fragment: str) -> str:
    text = BLOCK_END.sub('\n', fragment)
    text = html.unescape(TAG.sub('', text)).replace('\xa0', ' ')
    lines = [' '.join(line.split()) for line in text.split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def main():
    if DB.exists():
        DB.unlink()
    connection = sqlite3.connect(DB)
    connection.executescript('''
        CREATE TABLE sections(id INTEGER PRIMARY KEY, citation TEXT NOT NULL, heading TEXT, title_no TEXT, title_heading TEXT, article TEXT, article_heading TEXT,
            chapter TEXT, chapter_heading TEXT, text TEXT NOT NULL, history TEXT, status TEXT, text_chars INTEGER, source_file TEXT);
        CREATE VIRTUAL TABLE sections_fts USING fts5(citation, heading, text, content='sections', content_rowid='id', tokenize='unicode61');
    ''')
    inputs, counts = [], Counter()
    seen = set()
    identifier = 0
    for path in sorted(HTML_DIR.glob('*.html'), key=lambda p: int(p.stem) if p.stem.isdigit() else 999):
        inputs.append({'path': path.as_posix(), 'bytes': path.stat().st_size, 'sha256': sha256_file(path)})
        source = path.read_text(encoding='utf-8', errors='replace')
        marks = list(MARK.finditer(source))
        context = {'title': (None, None), 'article': (None, None), 'chapter': (None, None)}
        for position, mark in enumerate(marks):
            kind, code = mark.group(1), mark.group(2).strip()
            spans = dict(SPAN.findall(mark.group(3)))
            heading = plain(spans.get('shortdescription', ''))
            if kind != 'section':
                context[kind] = (code, heading)
                if kind == 'title':
                    context['article'] = context['chapter'] = (None, None)
                elif kind == 'article':
                    context['chapter'] = (None, None)
                counts[kind + 's'] += 1
                continue
            end = marks[position + 1].start() if position + 1 < len(marks) else len(source)
            body = source[mark.end():end]
            text = plain(body)
            history = '; '.join(plain(h) for h in HISTORY.findall(body) if re.search(r'P\.L\.|Acts|Formerly|amended|added', h))[:1500] or None
            citation = 'IC ' + code
            if code in seen:
                counts['duplicate_section_ids_kept_once'] += 1
                continue
            seen.add(code)
            status = 'repealed' if re.search(r'\bRepealed\b', heading, re.I) else ('expired' if re.search(r'\bExpired\b', heading, re.I) else 'text')
            identifier += 1
            connection.execute('INSERT INTO sections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                identifier, citation, heading, context['title'][0], context['title'][1], context['article'][0], context['article'][1], context['chapter'][0], context['chapter'][1],
                text, history, status, len(text), path.name))
            counts['sections'] += 1
            counts['sections_' + status] += 1
    connection.execute("INSERT INTO sections_fts(rowid, citation, heading, text) SELECT id, citation, coalesce(heading,''), text FROM sections")
    connection.executescript('CREATE INDEX sections_citation ON sections(citation); CREATE INDEX sections_title ON sections(title_no, article, chapter);')
    connection.commit()
    titles = connection.execute('SELECT title_no, max(title_heading), count(*) FROM sections GROUP BY title_no').fetchall()
    connection.execute('VACUUM')
    connection.close()
    extra = [{'path': p.as_posix(), 'bytes': p.stat().st_size} for p in sorted((SOURCE / '2026_Additional_Documents_PDF').glob('*.pdf'))]
    result = dict(counts)
    result['titles_with_sections'] = len(titles)
    validation = {
        'schema_version': '1', 'status': 'passed' if counts['sections'] > 1000 else 'failed', 'ready': counts['sections'] > 1000, 'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': DB.name, 'sha256': sha256_file(DB), 'rows': counts['sections']}], 'counts': result,
        'checks': {'sections_split_only_on_the_edition_markup': True, 'citations_are_the_markup_ids': True, 'no_network_used': True},
        'qualification': 'The 2026 edition of the Indiana Code as published in HTML by the Indiana General Assembly and saved locally by the user (the download date is not recorded '
                         'in the files). Section text, headings and history notes are as printed in that edition; later session laws are not reflected, and a heading marked '
                         'Repealed or Expired is shown as the edition prints it. Not verified against the current code.',
        'license_ref': 'indiana_general_assembly_public_code_local_copy', 'inputs': inputs, 'other_local_files_not_indexed': extra,
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1), encoding='utf-8')
    print(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
