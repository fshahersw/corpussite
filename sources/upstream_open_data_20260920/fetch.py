"""Acquire named open-source repositories for the 2026-09-20 enrichment pass, pinned and hashed.

Two steps, both re-runnable and offline-safe afterwards:

    python fetch.py survey                 repository facts, licence, head commit and full file list for every repo in REPOS
    python fetch.py get <owner/name> <glob> [<glob> ...]
                                           download the matching files at the surveyed commit into repos/<owner>__<name>/files/

Every downloaded file is recorded in repos/<owner>__<name>/manifest.json with its path, bytes, SHA-256, the commit it was
read at and the address it came from. Nothing is executed; files are data. No credentials are used or stored.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOS = ['freelawproject/courts-db', 'freelawproject/reporters-db', 'freelawproject/judge-pics', 'freelawproject/seal-rookery',
         'mayhewsw/legal-linking', 'Vaquill-AI/open-us-law', 'freelawproject/eyecite', 'LegalQuants/lq-skills', 'TechnoOptics/legal-data']
AGENT = 'LegalCorpusResearch/1.0 (local research archive)'
MAX_FILE = 80 * 1024 * 1024


def folder(repo: str) -> Path:
    return HERE / 'repos' / repo.replace('/', '__')


def read(url: str, accept: str = 'application/vnd.github+json') -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': AGENT, 'Accept': accept})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read(MAX_FILE + 1)


def survey():
    for repo in REPOS:
        target = folder(repo)
        target.mkdir(parents=True, exist_ok=True)
        try:
            meta = json.loads(read('https://api.github.com/repos/' + repo))
            branch = meta.get('default_branch') or 'main'
            head = json.loads(read('https://api.github.com/repos/%s/commits/%s' % (repo, branch)))
            sha = head['sha']
            tree = json.loads(read('https://api.github.com/repos/%s/git/trees/%s?recursive=1' % (repo, sha)))
        except (urllib.error.URLError, KeyError, ValueError) as error:
            print(repo, 'SURVEY FAILED', error)
            continue
        facts = {'repository': repo, 'surveyed_at': datetime.now(timezone.utc).isoformat(), 'default_branch': branch, 'commit': sha,
                 'commit_date': (head.get('commit') or {}).get('committer', {}).get('date'), 'licence_spdx': (meta.get('license') or {}).get('spdx_id'),
                 'licence_name': (meta.get('license') or {}).get('name'), 'description': meta.get('description'), 'pushed_at': meta.get('pushed_at'),
                 'stars': meta.get('stargazers_count'), 'archived': meta.get('archived'), 'size_kb': meta.get('size'), 'html_url': meta.get('html_url'),
                 'tree_truncated': tree.get('truncated')}
        (target / 'meta.json').write_text(json.dumps(facts, indent=2), encoding='utf-8')
        files = [{'path': item['path'], 'bytes': item.get('size')} for item in tree.get('tree', []) if item.get('type') == 'blob']
        (target / 'tree.json').write_text(json.dumps(files, indent=1), encoding='utf-8')
        print(repo, '|', facts['licence_spdx'], '|', sha[:10], '|', facts['commit_date'], '|', len(files), 'files |', facts['size_kb'], 'KB')
        time.sleep(1.0)


def get(repo: str, patterns):
    target = folder(repo)
    facts = json.loads((target / 'meta.json').read_text(encoding='utf-8'))
    files = json.loads((target / 'tree.json').read_text(encoding='utf-8'))
    manifest_path = target / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'repository': repo, 'commit': facts['commit'], 'files': {}}
    if manifest.get('commit') != facts['commit']:
        raise SystemExit('manifest was written at another commit; survey and get must use the same commit')
    wanted = [f for f in files if any(fnmatch.fnmatch(f['path'], pattern) for pattern in patterns)]
    print(repo, 'matching files:', len(wanted), 'bytes:', sum(f['bytes'] or 0 for f in wanted))
    for entry in wanted:
        path = entry['path']
        if path in manifest['files'] and (target / 'files' / path).exists():
            continue
        if (entry['bytes'] or 0) > MAX_FILE:
            print('  skipped (too large):', path, entry['bytes'])
            continue
        url = 'https://raw.githubusercontent.com/%s/%s/%s' % (repo, facts['commit'], urllib.request.quote(path))
        try:
            body = read(url, accept='*/*')
        except urllib.error.URLError as error:
            print('  FAILED', path, error)
            continue
        if len(body) > MAX_FILE:
            print('  skipped (too large):', path)
            continue
        destination = target / 'files' / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        manifest['files'][path] = {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'url': url, 'fetched_at': datetime.now(timezone.utc).isoformat()}
        manifest_path.write_text(json.dumps(manifest, indent=1), encoding='utf-8')
        time.sleep(0.25)
    print('  saved:', len(manifest['files']), 'files in manifest')


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'survey':
        survey()
    elif len(sys.argv) >= 4 and sys.argv[1] == 'get':
        get(sys.argv[2], sys.argv[3:])
    else:
        raise SystemExit(__doc__)
