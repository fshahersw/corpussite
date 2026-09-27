"""Recover two short substantive county sections from pinned saved responses. Offline only."""
from __future__ import annotations
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ORIGINAL = ROOT / 'sources/trellis_county_firecrawl_20260919'
QUEUE = ROOT / 'reports/trellis_refocus_20260919/county_audit/remaining_observed_county_urls.jsonl'
PINS = {
    'georgia/lowndes': ('5b35b609c546005ae4baf7f8', 'eb440ae71b990840bbb346625de93c1c0f7267ff0f029332d3ff27a912b805bc', {}),
    'oklahoma/major': ('d6f1aebdd028c40e43014c0d', 'f32bf6cfb845c87edb42a38b6e79fbea933c5728e146818a02f6070852103e6f', {'population': '7,527', 'county_seat': 'Fairview'}),
    'oklahoma/seminole': ('3360821cf918d13313fd52cd', '5c1492edfddc7f76e2712410b4355141818c57843114ed2e97796ddc4930b80b', {'population': '25,482', 'county_seat': 'Wewoka'}),
}
FROZEN_MANIFEST_SHA = '6d0fbe33bad93576c0e535f2189c1b3c353499923711d8b584853abca2ff8d60'
PARSER_SHA = '317e816ae53f47b21b8ebc0c2af49a12f49ca572cd371794862f4d11132d8f36'

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def rel(path): return path.relative_to(ROOT).as_posix()
def load(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def readl(path): return [json.loads(x) for x in path.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
def save(path, obj):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)

def main():
    assert sha(ORIGINAL/'resources.jsonl') == FROZEN_MANIFEST_SHA
    assert sha(ORIGINAL/'collect.py') == PARSER_SHA
    spec = importlib.util.spec_from_file_location('county_saved_parser', ORIGINAL/'collect.py')
    parser_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser_module)  # Only class definitions; collector main is not invoked.
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    queue_sha = sha(QUEUE)
    attempts = {x['url']: x for x in readl(ORIGINAL/'attempts.jsonl')}
    queued_urls = {x['url'] for x in readl(QUEUE)}
    saved_urls = {x['source_url'] for x in readl(ORIGINAL/'resources.jsonl')}
    c = sqlite3.connect((ROOT/'sources/trellis/catalog/catalog.sqlite3').as_uri()+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        proofs = {}
        for row in c.execute("select source_url,url,label from links where category='coverage_county'"):
            proofs.setdefault(row['url'], dict(row))
    finally:
        c.close()
    save(HERE/'validation.json', {'ready': False, 'status': 'updating', 'validated_at': stamp})
    for name in ('text', 'html'): (HERE/name).mkdir(exist_ok=True)
    resources, reviews = [], []
    for tail, (ident, expected_sha, expected_fields) in PINS.items():
        url = 'https://trellis.law/coverage/' + tail
        raw = ORIGINAL/'raw'/f'{ident}.json'
        assert sha(raw) == expected_sha
        obj = load(raw); metadata = obj['metadata']
        assert metadata['statusCode'] == 200 and metadata['sourceURL'] == metadata['url'] == url
        assert metadata['proxyUsed'] == 'basic' and metadata['creditsUsed'] == 1
        assert url in queued_urls and url in proofs and url not in saved_urls
        assert 'top-county-info-block__container' in obj['html']
        parsed = parser_module.ProfileParser(); parsed.feed(obj['html'])
        assert parsed.heading.endswith('Courts Records')
        assert parsed.fields == expected_fields and parsed.website is None and obj['links'] == []
        assert len(obj['markdown']) < 100
        accepted = len([v for v in parsed.fields.values() if v.strip()]) >= 2
        reviews.append({
            'source_url': url, 'raw_path': rel(raw), 'raw_sha256': expected_sha,
            'status_code': 200, 'heading': parsed.heading, 'fields': parsed.fields,
            'markdown_characters': len(obj['markdown']), 'html_characters': len(obj['html']),
            'substantive_fields': len(parsed.fields), 'observed_links': 0,
            'original_failure': attempts[url]['reason'], 'recovered': accepted,
            'finding': 'Two source-backed fields; rejected solely by the original 100-character markdown floor.' if accepted else 'Saved selected HTML contains only the heading and empty information containers. No substantive county fields are available in this response.',
        })
        if not accepted: continue
        text = HERE/'text'/f'{ident}.md'; html = HERE/'html'/f'{ident}.html'
        text.write_text(obj['markdown'], encoding='utf-8'); html.write_text(obj['html'], encoding='utf-8')
        state, county = tail.split('/')
        resources.append({
            'id': 'trellis-county-fc:'+ident, 'source_url': url, 'url': url,
            'state_slug': state, 'county_slug': county, 'title': parsed.heading, 'heading': parsed.heading,
            'fields': parsed.fields, 'website_url': None,
            'raw_path': rel(raw), 'raw_sha256': expected_sha, 'raw_bytes': raw.stat().st_size,
            'text_path': rel(text), 'text_sha256': sha(text), 'html_path': rel(html), 'html_sha256': sha(html),
            'captured_at': attempts[url]['completed_at'],
            'captured_at_basis': 'Original local acquisition completion from the preserved attempt receipt; not the offline recovery time or publisher update time.',
            'normalized_at': stamp,
            'provider': {'scrape_id': metadata['scrapeId'], 'proxy_used': metadata['proxyUsed'], 'cache_state': metadata['cacheState'], 'credits_used': metadata['creditsUsed'], 'status_code': metadata['statusCode']},
            'county_geoid': None, 'county_geoid_basis': 'Unresolved: use explicit heading/state and source-backed crosswalk, not slug alone.',
            'source_queue_path': rel(QUEUE), 'source_queue_sha256': queue_sha, 'observed_parent': proofs[url],
            'representation': parser_module.REPRESENTATION,
            'quality': 'Short selected county information section with two source-backed fields; no website, filings, dockets or judge analyses acquired.',
            'offline_recovery': {'reason': 'Removed arbitrary character threshold for a source section with two verified nonempty fields.', 'network_requests': 0, 'new_credits': 0, 'original_attempts_path': rel(ORIGINAL/'attempts.jsonl'), 'original_attempts_sha256': sha(ORIGINAL/'attempts.jsonl')},
        })
    assert len(resources) == 2 and len(reviews) == 3
    manifest = HERE/'resources.jsonl'
    temp = manifest.with_suffix('.jsonl.tmp')
    temp.write_text(''.join(json.dumps(x, ensure_ascii=False)+'\n' for x in resources), encoding='utf-8')
    os.replace(temp, manifest)
    for row in resources:
        for kind in ('raw', 'text', 'html'): assert sha(ROOT/row[kind+'_path']) == row[kind+'_sha256']
    city_report = ROOT/'sources/trellis_coverage_20260919/integration_verification.json'
    city_data = load(city_report)
    variants = city_data['404_url_variants_with_explicit_county_identity']
    assert len(variants) == 26 and sum(x['alternative_profile_detail_saved'] for x in variants) == 24
    save(HERE/'offline_review.json', {
        'reviewed_at': stamp, 'thin_responses': reviews,
        'city_variants_evidence_path': rel(city_report), 'city_variants_evidence_sha256': sha(city_report),
        'city_variants_review_snapshot': variants,
        'city_variant_qualification': 'These are the county integration agent\'s explicit-label/Census matches, not inferred URL aliases or new requests. Twenty-four have a separately saved alternate profile; Richmond city and Roanoke city do not. The unsuffixed Roanoke response conflicts with its parent label (city versus county) and remains unassigned.',
        'network_requests': 0, 'new_credits': 0,
    })
    summary = {
        'status': 'passed', 'reviewed_at': stamp, 'reviewed_thin_responses': 3, 'recovered_profiles': 2,
        'still_thin': ['https://trellis.law/coverage/georgia/lowndes'], 'artifact_hashes_checked': 6,
        'city_404_variants': 26, 'city_variants_with_alternate_saved_profile': 24,
        'city_variants_without_alternate_saved_profile': ['Richmond city (51760)', 'Roanoke city (51770)'],
        'network_requests': 0, 'new_credits': 0, 'identity_merges': 0, 'frozen_manifest_unchanged': True,
        'frozen_manifest_sha256': FROZEN_MANIFEST_SHA, 'resources_sha256': sha(manifest),
        'qualification': 'Profile fields are dated publisher claims, not current official population or complete local court data. Existing acquisition costs remain in the original batch receipts. The low-information response remains excluded.',
    }
    assert sha(ORIGINAL/'resources.jsonl') == FROZEN_MANIFEST_SHA
    save(HERE/'summary.json', summary)
    save(HERE/'validation.json', {
        'ready': True, 'status': 'passed', 'manifest_path': 'resources.jsonl', 'manifest_sha256': sha(manifest),
        'records': 2, 'validated_at': stamp, 'complete': False, 'review_complete': True,
        'scope': 'Exactly two short county profile sections recovered offline; original frozen manifest preserved.',
        'provider_representation': True, 'network_requests': 0, 'new_credits': 0,
        'summary_sha256': sha(HERE/'summary.json'), 'review_sha256': sha(HERE/'offline_review.json'), 'builder_sha256': sha(Path(__file__)),
        'qualification': parser_module.REPRESENTATION+' County GEOIDs unresolved; no website or document content inferred.',
    })
    print(json.dumps(summary))

if __name__ == '__main__': main()
