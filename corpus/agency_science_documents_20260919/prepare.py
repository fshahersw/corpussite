"""Prepare a finite, document-only download collection from the URL directory's ranked frontier (2026-09-19).

Seeds are exact URLs of not-yet-saved documents on official mass-tort agency and science hosts. Links are not followed.
SEC hosts are excluded (EDGAR fair-access requires a declared contact that has not been provided). The shared crawler
applies robots.txt, per-host pacing, host pauses on access blocks and the disk-space guard.
"""
import collections
import json
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRONTIER = HERE.parents[1] / 'sources' / 'url_directory_20260919' / 'frontier_documents.jsonl'
HOSTS = {
    'www.atsdr.cdc.gov': 'atsdr', 'wwwn.cdc.gov': 'atsdr', 'www.epa.gov': 'epa', 'iris.epa.gov': 'epa', 'cfpub.epa.gov': 'epa', 'nepis.epa.gov': 'epa',
    'www.cpsc.gov': 'cpsc', 'www.jpml.uscourts.gov': 'jpml', 'ntp.niehs.nih.gov': 'ntp', 'www.osha.gov': 'osha', 'www.cdc.gov': 'cdc', 'stacks.cdc.gov': 'cdc',
    'static.nhtsa.gov': 'nhtsa', 'www.nhtsa.gov': 'nhtsa', 'one.nhtsa.gov': 'nhtsa', 'www.cms.gov': 'cms', 'www.fda.gov': 'fda', 'www.accessdata.fda.gov': 'fda',
}
KINDS = {'pdf', 'word', 'spreadsheet', 'text', 'data'}
PER_HOST_CAP = 3000


def main():
    seeds, per_host = [], collections.Counter()
    for line in open(FRONTIER, encoding='utf-8'):
        row = json.loads(line)
        family = HOSTS.get(row['host'])
        if not family or row['doc_kind'] not in KINDS or per_host[row['host']] >= PER_HOST_CAP:
            continue
        path = urllib.parse.urlsplit(row['url']).path or '/'
        per_host[row['host']] += 1
        seeds.append({'url': row['url'], 'source_family': family, 'category': row['doc_kind'], 'jurisdiction': {'level': 'federal'},
                      'scope': {'host': row['host'], 'path_prefixes': [path]}, 'frontier_rank': row['rank'], 'frontier_title': row.get('title')})
    config = {'allow': [{'host': host, 'path_prefixes': ['/']} for host in sorted(per_host)], 'workers': 6, 'per_host_delay': 2.0, 'follow_links': False, 'max_depth': 0,
              'max_retries': 1, 'respect_robots': True, 'pause_host_on_access_block': True, 'shared_host_dir': 'corpus/_shared_hosts',
              'max_response_bytes': 64 * 1024 * 1024, 'min_free_bytes': 20 * 1024 * 1024 * 1024}
    (HERE / 'config.json').write_text(json.dumps(config, indent=1), encoding='utf-8')
    with open(HERE / 'seeds.jsonl', 'w', encoding='utf-8', newline='\n') as handle:
        for seed in seeds:
            handle.write(json.dumps(seed, ensure_ascii=False) + '\n')
    print(json.dumps({'seeds': len(seeds), 'per_host': dict(per_host.most_common())}, indent=1))


if __name__ == '__main__':
    main()
