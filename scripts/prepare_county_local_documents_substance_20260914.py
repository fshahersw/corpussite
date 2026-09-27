"""Finite offline county preparation with explicit nonjudicial keyword exclusions."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE_SOURCE = ROOT / 'scripts/prepare_county_local_documents_20260914.py'
BASE_DRAFT = ROOT / 'sources/counties/local_documents_20260914/batches/20260914T074809815784Z'
spec = importlib.util.spec_from_file_location('observed_county_preparer', BASE_SOURCE)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
original_classify = base.classify
excluded = {}


def classify(url, anchor):
    classified = original_classify(url, anchor)
    if not classified:
        return None
    value = base.normalize(anchor + ' ' + url)
    category = classified[0]
    reason = None
    if category == 'court_forms_filing_documents' and re.search(r'\b(elections?|candidates?|elected officials)\b', value):
        reason = 'Election or elected-official filing label; not a judicial filing.'
    elif category == 'court_local_resources' and re.search(r'\b(basketball|tennis|pickleball|volleyball|playground|ghost tours?|haunted)\b', value):
        reason = 'Recreation or tourism use of court; not a court legal resource.'
    elif category == 'court_local_resources' and re.search(r'\b(weather alerts?|budget services)\b', value):
        reason = 'Weather alert or court budget-administration landing; deferred for legal-substance priority.'
    elif category == 'court_local_resources' and re.search(r'\b(judiciary|judicial) and public safety committee\b', value):
        reason = 'Government committee event; deferred for legal-substance priority.'
    elif category == 'court_local_resources' and re.search(r'\bhow do i find the county court house\b', value):
        reason = 'Courthouse directions FAQ; deferred for legal-substance priority.'
    elif category == 'court_clerk_office' and base.normalize(anchor) == 'treasurer':
        reason = 'Treasurer anchor conflicts with a legacy clerk URL slug; needs content review.'
    elif category == 'local_laws_codes' and 'faq aspx' in value and 'property information' in value:
        reason = 'General property-information FAQ; deferred for legal-substance priority.'
    if reason:
        excluded[(url, anchor)] = {'url': url, 'anchor_text': anchor, 'discovery_category': category, 'reason': reason}
        return None
    return classified


if __name__ == '__main__':
    base.classify = classify
    base.main()
    # Preserve complete source proofs for exclusions available in the preceding
    # unfiltered snapshot; its frozen files are read only.
    source_rows = [json.loads(line) for line in (BASE_DRAFT / 'all_candidates.jsonl').read_text(encoding='utf-8').splitlines() if line]
    for record in excluded.values():
        record['prior_snapshot_candidates'] = [row for row in source_rows if row['url'] == record['url'] and any(proof['anchor_text'] == record['anchor_text'] for proof in row['provenance'])]
    rows = sorted(excluded.values(), key=lambda row: (row['url'], row['anchor_text']))
    base.jsonl(base.OUT / 'deferred_nonjudicial_or_low_substance.jsonl', rows)
    base.dump(base.OUT / 'substance_filter_review.json', {
        'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
        'filter_script': base.rel(Path(__file__)),
        'filter_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'base_preparer_sha256': hashlib.sha256(BASE_SOURCE.read_bytes()).hexdigest(),
        'prior_unfiltered_snapshot': base.rel(BASE_DRAFT),
        'prior_candidates_sha256': hashlib.sha256((BASE_DRAFT / 'all_candidates.jsonl').read_bytes()).hexdigest(),
        'excluded_url_anchor_pairs': len(rows),
        'excluded_unique_urls': len({row['url'] for row in rows}),
        'exclusion_file_sha256': hashlib.sha256((base.OUT / 'deferred_nonjudicial_or_low_substance.jsonl').read_bytes()).hexdigest(),
        'basis': 'Observed URL and anchor text; downloaded target content has not been inspected.',
        'remaining_qualifications': ['Proposed or draft ordinance labels do not establish enactment.', 'Municipal ordinances remain municipal; candidate county mapping does not establish governing jurisdiction.', 'Filing fee, business filing, calendar, and records-request pages are not actual case filings.', 'Unverified source county associations remain unverified.'],
        'network_requests': 0,
        'collection_mutations': 0,
    })
    print(json.dumps({'filtered_packet': base.rel(base.OUT), 'excluded_url_anchor_pairs': len(rows)}, indent=2))
