"""Credential scan. Prints WHERE a credential-shaped string occurs (path, pattern name, count) and never the string itself.

    python tools/scan_secrets.py git            # the files git tracks (run before every push)
    python tools/scan_secrets.py tree [folder]  # every file below the project (or one folder), in 8 MB windows

Compressed formats (pdf, parquet, zip, images) cannot show a plain string and are skipped in tree mode.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'supabase_secret_key': rb'\bsb_secret_[A-Za-z0-9_\-]{20,}\b',
    'firecrawl_key': rb'\bfc-[0-9a-f]{32}\b',
    'github_token': rb'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b',
    'aws_access_key_id': rb'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b',
    'anthropic_or_openai_key': rb'\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}\b',
    'tavily_key': rb'\btvly-[A-Za-z0-9\-_]{20,}\b',
    'apify_token': rb'\bapify_api_[A-Za-z0-9]{30,}\b',
    'slack_token': rb'\bxox[abprs]-[A-Za-z0-9\-]{20,}\b',
    'authorization_token_header': rb'(?i)authorization["\']?\s*[:=]\s*["\']?(?:token|bearer)\s+[A-Za-z0-9._\-]{30,}',
    'private_key_block': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'url_with_embedded_credentials': rb'https?://[A-Za-z0-9._%\-]+:[A-Za-z0-9._%\-]{12,}@',
}
COMPILED = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}
SKIP_EXT = {'.pdf', '.parquet', '.zip', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.woff', '.woff2', '.ttf', '.gz', '.tar', '.docx', '.xlsx', '.doc', '.exe', '.dll', '.node', '.traineddata', '.epub', '.ico', '.bin'}
SKIP_DIRS = {'.git', 'node_modules', '_transfer_scratch', '__pycache__'}
WINDOW, OVERLAP = 8 * 1024 * 1024, 512


def scan_file(path):
    hits = Counter()
    try:
        with open(path, 'rb') as handle:
            tail = b''
            while True:
                block = handle.read(WINDOW)
                if not block:
                    break
                data = tail + block
                for name, pattern in COMPILED.items():
                    found = sum(1 for match in pattern.finditer(data) if match.end() > len(tail))
                    if found:
                        hits[name] += found
                tail = data[-OVERLAP:]
    except OSError:
        return path, {'unreadable': 1}
    return path, dict(hits)


def tree_files(top):
    for base, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(base) == ROOT and d in ('devvvv', '.tools'))]
        for name in files:
            if os.path.splitext(name)[1].lower() not in SKIP_EXT:
                yield os.path.join(base, name)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'git'
    if mode == 'git':
        listed = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True).stdout.split(b'\0')
        files = [str(ROOT / item.decode('utf-8')) for item in listed if item]
    else:
        files = list(tree_files(str(ROOT / sys.argv[2]) if len(sys.argv) > 2 else str(ROOT)))
    print('scanning', len(files), 'files')
    flagged = 0
    with ProcessPoolExecutor(max_workers=4) as pool:
        for path, hits in pool.map(scan_file, files, chunksize=64):
            if hits:
                flagged += 1
                print('  %s  %s' % (os.path.relpath(path, ROOT).replace(os.sep, '/'), hits))
    print('files with credential-shaped strings:', flagged)
    return 1 if flagged else 0


if __name__ == '__main__':
    sys.exit(main())
