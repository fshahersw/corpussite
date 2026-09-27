"""Build a finite federal directory from saved provider text and original HTML."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urljoin, urlsplit, urldefrag

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import corpus_crawler as engine

COURTS_ROOT = 'https://www.uscourts.gov/about-federal-courts/court-role-and-structure/court-website-links'
DOJ_ROOT = 'https://www.justice.gov/resources'
STATES = sorted({row['state'] for row in json.loads((ROOT / 'sources/official_courts/datasets/counties_50_plus_dc.json').read_text())}, key=len, reverse=True)
LINK = re.compile(r'(?<!!)\[([^\[\]]+)\]\(([^\s)]+)(?:\s+"[^"]*")?\)')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def normalize_url(href, parent):
    url = urldefrag(urljoin(parent, href))[0]
    parts = urlsplit(url)
    return url if parts.scheme in ('http', 'https') and parts.hostname else None


def explicit_state(label):
    matches = [state for state in STATES if re.search(r'(?<![A-Za-z])' + re.escape(state) + r'(?![A-Za-z])', label)]
    # Prevent Virginia from also matching the explicitly named West Virginia.
    matches = [state for state in matches if not any(state != other and state in other for other in matches)]
    return matches[0] if len(matches) == 1 else None


def markdown_links(text, parent, parent_path, parent_sha):
    section, headers, offset = '', [], 0
    for line in text.splitlines(keepends=True):
        if re.match(r'^#{1,6} ', line):
            section = re.sub(r'^#+\s*', '', line).strip()
        if line.startswith('|') and '[ ' not in line and '[' not in line and any(word in line for word in ['District Courts', 'Bankruptcy Courts', 'Probation', 'Pretrial']):
            headers = [part.strip() for part in line.strip().strip('|').split('|')]
        for match in LINK.finditer(line):
            title, href = match.groups()
            url = normalize_url(href, parent)
            if not url or href.startswith('#'):
                continue
            column = max(0, line[:match.start()].count('|') - 1)
            table_header = headers[column] if line.startswith('|') and column < len(headers) else None
            yield {'title': title.strip(), 'source_url': url, 'parent_url': parent, 'raw_href': href, 'parent_section': section, 'parent_table_column': table_header, 'evidence_format': 'provider_markdown', 'parent_text_path': parent_path, 'parent_text_sha256': parent_sha, 'literal': match.group(0), 'start_char': offset + match.start(), 'end_char': offset + match.end()}
        offset += len(line)


def useful_kind(link):
    parent, url, title = link['parent_url'], link['source_url'], link['title']
    # Keep literal source captures intact, but do not publish table controls as resources.
    query_keys = {key.lower() for key, _ in parse_qsl(urlsplit(url).query)}
    if query_keys & {'order', 'sort', 'orderby', 'sort_by', 'sort_order', 'page',
                     'pageindex', 'pagenumber', 'offset', 'items_per_page', 'per_page', 'limit'}:
        return None
    label = ' '.join(title.lower().split())
    if label in {'home', 'return to top', 'back to top', 'next', 'previous', 'first',
                 'last', 'reset', 'apply', 'select language', 'skip to main content'}:
        return None
    if re.search(r'\bsort (?:ascending|descending)\b', label):
        return None
    host = urlsplit(url).hostname or ''
    value = (title + ' ' + urlsplit(url).path).lower()
    section = link['parent_section'].lower()
    if parent == COURTS_ROOT:
        if not title or title.lower() in ('return to top', 'select language'):
            return None
        if 'district and bankruptcy' in section:
            return 'federal_bankruptcy_court_website' if 'Bankruptcy' in (link.get('parent_table_column') or '') else 'federal_district_court_website'
        if 'supreme court' in section:
            return 'federal_supreme_court_website'
        if 'courts of appeals' in section:
            return 'federal_court_of_appeals_website'
        if 'probation' in section or 'pretrial' in section:
            return 'federal_probation_or_pretrial_office_website'
        if 'defender' in section:
            return 'federal_defender_website'
        if 'map' in value:
            return 'federal_circuit_map_resource'
        return None
    if not (host == 'justice.gov' or host.endswith('.justice.gov') or host == 'uscourts.gov' or host.endswith('.uscourts.gov')):
        return None
    if re.search(r'careers|employment|vacancy|subscribe|social-media|contact|privacy-policy|login|ejuror|rfq-|scam|seminar|news/', value):
        return None
    if parent == 'https://www.tned.uscourts.gov/local-rules':
        if 'archived' in value:
            return 'federal_archived_rule_index'
        if 'standing orders' in section or 'active standing orders' in section:
            return 'federal_order_document_link'
        if 'rule' in value or 'order' in value or 'default judgment' in value or 'guidelines' in value:
            return 'federal_rule_or_practice_resource'
    if re.search(r'justice.manual|/jm/|title \d+ - ', value):
        return 'doj_manual_resource'
    if re.search(r'privacy.act|legislative.histor|guidance|publications|journal.of.federal.law|statistics|/forms|/rules|general.orders|standing.order', value):
        return 'federal_legal_reference_resource'
    if re.search(r'judges|opinions|memoranda|case|filing|motions|pro.se|without.attorney|docket|appellate.practice|appeal.process|glossary', value):
        return 'federal_court_practice_or_case_resource'
    return None


def main():
    captures, all_links, failed = [], [], []
    parent_by_url = {}
    pages = []
    for response_path in sorted((OUT / 'provider_responses').glob('*.json')):
        data = response_path.read_bytes(); packet = json.loads(data)
        payload = packet['result']['structuredContent']
        failed.extend(payload.get('failed_results', []))
        for page in payload['results']:
            pages.append((page, packet, response_path, sha(data)))
    assert len(pages) == len({page['url'] for page, *_ in pages}) == 15
    for page, packet, response_path, response_sha in pages:
        url, text = page['url'], page['raw_content']
        capture_id = 'federal_capture_' + sha(url.encode())[:24]
        text_path = OUT / 'text' / (capture_id + '.md')
        text_path.parent.mkdir(exist_ok=True); text_path.write_bytes(text.encode('utf-8'))
        item = {'provider': 'tavily_extract', 'captured_at': packet['captured_at'], 'provider_request_id': packet['result']['structuredContent'].get('request_id'), 'source_url': url, 'result': page, 'provider_response_path': relative(response_path), 'provider_response_sha256': response_sha, 'original_http_bytes': False}
        raw_path = OUT / 'captures' / (capture_id + '.provider.json')
        write_json(raw_path, item)
        record = {'id': capture_id, 'record_type': 'capture', 'title': page['title'], 'source_url': url, 'parent_url': None, 'resource_kind': 'federal_directory_page' if url in (COURTS_ROOT, DOJ_ROOT) else 'federal_legal_or_court_resource_page', 'state_if_explicit': explicit_state(page['title']), 'state_basis': 'Explicit source title only; not a judicial-jurisdiction assignment.', 'authority': 'federal', 'captured_at': packet['captured_at'], 'capture_status': 'saved', 'capture_kind': 'provider_extracted_markdown', 'raw_path': relative(raw_path), 'text_path': relative(text_path), 'sha256': sha(raw_path.read_bytes()), 'text_sha256': sha(text_path.read_bytes()), 'original_http_bytes': False, 'original_http_status': None, 'provider': 'tavily_extract', 'provider_request_id': item['provider_request_id'], 'source_fetch_time_known': False, 'court_territorial_jurisdiction_inferred': False, 'county_geoid': None, 'legal_currency_verified': False, 'scope': 'federal'}
        captures.append(record)
        all_links.extend(markdown_links(text, url, relative(text_path), record['text_sha256']))
    # Original DOJ HTML retains cards which the provider extraction omitted.
    meta_path = OUT / 'original_http/justice_resources.metadata.json'
    metadata = json.loads(meta_path.read_text())
    original_path = OUT / metadata['raw_path']; original = original_path.read_bytes()
    assert metadata['http_status'] == 200 and metadata['robots_allowed'] and sha(original) == metadata['sha256']
    parser = engine.TextHTML(); parser.feed(original.decode('utf-8')); parser.close()
    doj_text = OUT / 'text/justice_resources.original_html.txt'; doj_text.write_bytes(parser.text.encode('utf-8'))
    for entry in parser.links:
        if entry['relation'] != 'link':
            continue
        label = ' '.join(entry['text'].split()); url = normalize_url(entry['href'], DOJ_ROOT)
        if not label or not url:
            continue
        all_links.append({'title': label, 'source_url': url, 'parent_url': DOJ_ROOT, 'raw_href': entry['href'], 'parent_section': 'Original DOJ resources HTML; page or navigation link', 'parent_table_column': None, 'evidence_format': 'original_http_html_anchor', 'parent_raw_path': relative(original_path), 'parent_raw_sha256': metadata['sha256'], 'parent_metadata_path': relative(meta_path), 'parent_metadata_sha256': sha(meta_path.read_bytes())})
    doj = next(row for row in captures if row['source_url'] == DOJ_ROOT)
    doj['provider_capture_evidence'] = {key: doj[key] for key in ['raw_path', 'text_path', 'sha256', 'text_sha256', 'captured_at', 'capture_kind']}
    doj.update(raw_path=relative(original_path), text_path=relative(doj_text), sha256=metadata['sha256'], text_sha256=sha(doj_text.read_bytes()), captured_at=metadata['captured_at'], capture_kind='original_http_html', original_http_bytes=True, original_http_status=200, source_fetch_time_known=True, metadata_path=relative(meta_path), metadata_sha256=sha(meta_path.read_bytes()))
    links = []
    seen = set()
    for link in all_links:
        kind = useful_kind(link)
        if not kind or link['source_url'] == link['parent_url']:
            continue
        key = (link['parent_url'], link['source_url'], link['title'], kind)
        if key in seen:
            continue
        seen.add(key)
        record = {'id': 'federal_link_' + sha(json.dumps(key).encode())[:24], 'record_type': 'observed_link', **link, 'resource_kind': kind, 'state_if_explicit': explicit_state(link['title']), 'state_basis': 'Explicit observed anchor label only; unknown remains null.', 'authority': 'federal', 'authority_basis': 'Observed on an official federal source; destination ownership is not independently reverified.', 'captured_at': next(row['captured_at'] for row in captures if row['source_url'] == link['parent_url']), 'capture_status': 'observed_link_only', 'raw_path': None, 'text_path': None, 'sha256': None, 'court_territorial_jurisdiction_inferred': False, 'county_geoid': None, 'scope': 'federal', 'legal_currency_verified': False}
        links.append(record)
        parent_by_url.setdefault(link['source_url'].rstrip('/'), link['parent_url'])
    capture_by_url = {row['source_url'].rstrip('/'): row for row in captures}
    for record in captures:
        if record['source_url'] not in (COURTS_ROOT, DOJ_ROOT):
            record['parent_url'] = parent_by_url.get(record['source_url'].rstrip('/'))
            assert record['parent_url'], 'Every nonroot capture must have an exact observed source link'
    for record in links:
        capture = capture_by_url.get(record['source_url'].rstrip('/'))
        record['target_capture_id'] = capture['id'] if capture else None
    for name, rows in [('captures.jsonl', captures), ('resources.jsonl', captures + links), ('observed_links.jsonl', links)]:
        (OUT / name).write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows), encoding='utf-8')
    summary = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope': 'Federal source enrichment; separate from state and county corpora.', 'root_urls': [COURTS_ROOT, DOJ_ROOT], 'unique_pages_captured': len(captures), 'primary_original_http_captures': sum(row['original_http_bytes'] for row in captures), 'primary_provider_text_captures': sum(not row['original_http_bytes'] for row in captures), 'provider_extractions_saved': len(pages), 'tavily_extract_calls': 3, 'direct_http_requests_including_robots': 2, 'failed_results': failed, 'observed_useful_link_records': len(links), 'observed_unique_target_urls': len({row['source_url'] for row in links}), 'observed_link_kinds': dict(Counter(row['resource_kind'] for row in links)), 'links_with_explicit_state_label': sum(row['state_if_explicit'] is not None for row in links), 'original_document_files_downloaded': 0, 'federal_complete': False, 'state_or_county_collection_changes': 0, 'source_collector_or_delivery_changes': 0, 'limitations': ['Provider markdown is not original HTTP HTML; source fetch/cache time is unknown for provider captures.', 'Directory links do not establish downloaded content, current status, territorial jurisdiction, or current legal effect.', 'The official court directory includes duplicate destination URLs serving different labeled roles; labels and sections are preserved separately.', 'DOJ cards missing from provider markdown were recovered from original HTTP HTML after a robots check.', 'No PACER or court login was attempted; no form was submitted.'], 'artifact_sha256': {name: sha((OUT / name).read_bytes()) for name in ['resources.jsonl', 'captures.jsonl', 'observed_links.jsonl']}}
    write_json(OUT / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
