"""Validate hashes and every retained exact link without network requests."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urljoin, urldefrag

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import corpus_crawler as engine


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    resources = [json.loads(line) for line in (OUT / 'resources.jsonl').read_text(encoding='utf-8').splitlines()]
    ids = set(); files = {}; html_cache = {}; counts = {'captures': 0, 'provider_markdown_links': 0, 'original_html_links': 0}
    def artifact(name, expected):
        if name not in files:
            path = (ROOT / name).resolve(); path.relative_to(OUT.resolve())
            files[name] = path.read_bytes()
        assert sha(files[name]) == expected, name
        return files[name]
    for row in resources:
        assert row['id'] not in ids; ids.add(row['id'])
        assert row['authority'] == row['scope'] == 'federal' and row['county_geoid'] is None
        assert row['court_territorial_jurisdiction_inferred'] is False and row['legal_currency_verified'] is False
        if row['state_if_explicit']:
            assert row['state_if_explicit'] in row['title']
        if row['record_type'] == 'capture':
            counts['captures'] += 1
            raw = artifact(row['raw_path'], row['sha256'])
            text = artifact(row['text_path'], row['text_sha256']).decode('utf-8')
            assert text.strip()
            if row['capture_kind'] == 'provider_extracted_markdown':
                provider = json.loads(raw)
                assert provider['source_url'] == row['source_url'] and provider['result']['url'] == row['source_url']
                assert provider['result']['raw_content'] == text and provider['original_http_bytes'] is False
                assert row['original_http_status'] is None and row['source_fetch_time_known'] is False
                artifact(provider['provider_response_path'], provider['provider_response_sha256'])
            else:
                metadata = json.loads(artifact(row['metadata_path'], row['metadata_sha256']))
                assert metadata['sha256'] == row['sha256'] and metadata['requested_url'] == row['source_url']
                assert metadata['robots_allowed'] and metadata['http_status'] == 200 and row['original_http_bytes']
                proof = row['provider_capture_evidence']
                artifact(proof['raw_path'], proof['sha256']); artifact(proof['text_path'], proof['text_sha256'])
        else:
            assert row['capture_status'] == 'observed_link_only' and row['raw_path'] is row['text_path'] is row['sha256'] is None
            assert urldefrag(urljoin(row['parent_url'], row['raw_href']))[0] == row['source_url']
            if row['evidence_format'] == 'provider_markdown':
                counts['provider_markdown_links'] += 1
                text = artifact(row['parent_text_path'], row['parent_text_sha256']).decode('utf-8')
                assert text[row['start_char']:row['end_char']] == row['literal']
                assert row['title'] in row['literal'] and row['raw_href'] in row['literal']
            else:
                counts['original_html_links'] += 1
                key = (row['parent_raw_path'], row['parent_raw_sha256'])
                if key not in html_cache:
                    parser = engine.TextHTML(); parser.feed(artifact(*key).decode('utf-8')); parser.close()
                    html_cache[key] = {(entry['href'], ' '.join(entry['text'].split())) for entry in parser.links if entry['relation'] == 'link'}
                assert (row['raw_href'], row['title']) in html_cache[key]
                artifact(row['parent_metadata_path'], row['parent_metadata_sha256'])
    captures = [row for row in resources if row['record_type'] == 'capture']
    for row in resources:
        if row.get('target_capture_id'):
            assert any(c['id'] == row['target_capture_id'] and c['source_url'].rstrip('/') == row['source_url'].rstrip('/') for c in captures)
    assert counts['captures'] == len({row['source_url'] for row in captures}) == 15
    summary = json.loads((OUT / 'summary.json').read_text())
    for name, expected in summary['artifact_sha256'].items():
        assert sha((OUT / name).read_bytes()) == expected
    result = {'validated_at': datetime.now(timezone.utc).isoformat(), 'valid': True, 'resource_records': len(resources), **counts, 'unique_files_hash_verified': len(files), 'all_exact_link_proofs_verified': True, 'no_unknown_state_or_county_inference': True, 'validation_network_requests': 0, 'source_fetch_currency_or_legal_effect_certified': False, 'source_artifact_sha256': summary['artifact_sha256']}
    (OUT / 'validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
