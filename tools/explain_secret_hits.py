"""Second look at the files flagged by scan_secrets.py: says what each credential-shaped string IS, without printing it.

For every match it prints the file, the pattern, a masked form (first characters + length), the text just before it and a verdict:
  url-slug            lower-case words joined by hyphens that happen to start with "sk-" (for example a party name in a case URL)
  signed-url-key-id   an access-key ID inside someone else's pre-signed download link (public by design, not a secret)
  upper-case-text     a run of capitals/digits in ordinary text
  REVIEW              none of the above: look at it before publishing
Usage: python tools/explain_secret_hits.py reports/hosting_plan_20260920/secret_scan_tree.log
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'anthropic_or_openai_key': re.compile(rb'\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}\b'),
    'aws_access_key_id': re.compile(rb'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
}
SLUG = re.compile(rb'sk-[a-z0-9]+(?:-[a-z0-9]+){2,}')
SIGNED = re.compile(rb'(?i)(?:AWSAccessKeyId|X-Amz-Credential|Credential)(?:=|%3D|":\s*")$')
WINDOW, OVERLAP = 8 * 1024 * 1024, 512


def verdict(name, token, before):
    if name == 'anthropic_or_openai_key':
        return 'url-slug' if SLUG.fullmatch(token) else 'REVIEW'
    if SIGNED.search(before):
        return 'signed-url-key-id'
    return 'upper-case-text' if not any(chr(c).isdigit() for c in token[4:]) else 'REVIEW'


def main():
    flagged = [line.strip().split('  {')[0] for line in open(sys.argv[1], encoding='utf-8') if line.startswith('  ') and 'unreadable' not in line]
    totals = Counter()
    for relative in flagged:
        path = ROOT / relative
        seen = Counter()
        with open(path, 'rb') as handle:
            tail = b''
            while True:
                block = handle.read(WINDOW)
                if not block:
                    break
                data = tail + block
                for name, pattern in PATTERNS.items():
                    for match in pattern.finditer(data):
                        if match.end() <= len(tail):
                            continue
                        token = match.group(0)
                        before = data[max(0, match.start() - 48):match.start()]
                        result = verdict(name, token, before)
                        seen[(name, result)] += 1
                        if result == 'REVIEW' and seen[(name, result)] <= 6:
                            shown = re.sub(rb'[^\x20-\x7e]', b'.', before).decode('ascii')
                            print('   REVIEW %s  %s...(%d chars)  after: %r' % (name, token[:7].decode('ascii'), len(token), shown[-44:]), flush=True)
                tail = data[-OVERLAP:]
        for (name, result), count in sorted(seen.items()):
            totals[result] += count
            print('%-7d %-24s %-18s %s' % (count, name, result, relative), flush=True)
    print('totals:', dict(totals))


if __name__ == '__main__':
    main()
