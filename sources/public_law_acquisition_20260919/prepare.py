"""Freeze an exact, source-observed pilot. No network or guessed URLs."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = Path('C:/Users/firas/Downloads/publicLaw_directory.md')


def write(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def prepare():
    content = SOURCE.read_bytes()
    source_hash = hashlib.sha256(content).hexdigest()
    known = set()
    with sqlite3.connect((ROOT / 'catalog/documents.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        known.update(x[0] for x in db.execute('SELECT source_url FROM versions UNION SELECT final_url FROM versions'))
    with sqlite3.connect((ROOT / 'delivery/archive-directory/directory.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        known.update(x[0] for x in db.execute("SELECT source_url FROM browse WHERE COALESCE(original_id,'')!='' OR COALESCE(text_id,'')!='' OR content_id IS NOT NULL OR inline_text=1"))
    with sqlite3.connect((ROOT / 'corpus/_shared_hosts/hosts.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        blocked = dict(db.execute("SELECT host,pause_reason FROM host_state WHERE COALESCE(pause_reason,'')!=''"))
    seeds, skipped = [], []
    subsection = None
    for number, line in enumerate(content.decode('utf-8').splitlines()[:140], 1):
        if line.startswith('### '):
            subsection = line[4:]
        m = re.match(r'- \[(.*?)\]\((https?://[^ ]+?)\)', line)
        if not m or m[1] not in ('forms', 'judges', 'rules/IOPs'):
            continue
        url = m[2]
        host = urlsplit(url).netloc.lower()
        if url in known or host in blocked:
            skipped.append({'url': url, 'reason': 'already_saved' if url in known else 'shared_host_block', 'access_reason': blocked.get(host)})
            continue
        category = {'forms': 'court_forms', 'judges': 'judge_directory', 'rules/IOPs': 'court_rules'}[m[1]]
        seeds.append({'url': url, 'title': subsection + ' — ' + m[1], 'source_family': 'public_law_directory_observed_official_source', 'category': category, 'jurisdiction': {'country': 'US', 'system': 'federal', 'court': subsection}, 'scope': {'host': host, 'path_prefixes': [urlsplit(url).path or '/']}, 'directory_evidence': {'path': str(SOURCE), 'sha256': source_hash, 'line': number, 'original_markdown': line}})
    assert 20 <= len(seeds) <= 40, len(seeds)
    assert len({urlsplit(x['url']).netloc for x in seeds}) >= 10
    assert len({x['url'] for x in seeds}) == len(seeds)
    (HERE / 'seeds.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in seeds), encoding='utf-8')
    hosts = {}
    for seed in seeds:
        hosts.setdefault(seed['scope']['host'], []).extend(seed['scope']['path_prefixes'])
    cfg = {'allow': [{'host': host, 'path_prefixes': sorted(set(paths))} for host, paths in hosts.items()], 'workers': 8, 'per_host_delay': 2.0, 'respect_robots': True, 'shared_host_dir': 'corpus/_shared_hosts', 'follow_links': False, 'follow_external_allowed_links': False, 'max_depth': 0, 'max_retries': 0, 'timeout_seconds': 30, 'max_transfer_seconds': 120, 'pause_host_on_access_block': True, 'user_agent': 'LegalCorpusResearch/1.0'}
    write('config.json', cfg)
    write('plan.json', {'source_sha256': source_hash, 'source_date_claim': '2026-08-19', 'selected': len(seeds), 'hosts': len(hosts), 'skipped': skipped, 'scope': 'Exact observed federal appellate forms, rules and judge directory pages; no automatic link discovery.', 'limits': {'workers': 8, 'max_resource_attempts': len(seeds), 'max_seconds': 600, 'minimum_host_delay_seconds': 2, 'automatic_retries': 0}, 'deduplication': 'Exact requested/final URL from canonical saved versions, plus directory rows with saved originals/text/content. Linked-only registry rows do not count as captures.', 'complete_site_claim': False})
    print(json.dumps({'seeds': len(seeds), 'hosts': len(hosts), 'skipped': skipped}))


if __name__ == '__main__':
    prepare()
