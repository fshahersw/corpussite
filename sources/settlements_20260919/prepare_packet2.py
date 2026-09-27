"""Freeze packet 2: document links observed on the packet-1 captures. Offline; no guessed URLs.

Selection rule (task statement): links observed on captured pages that the classifier
types as agreement / notice / order / claim form PDF / FAQ / deadlines, on the same host
as the captured page or on a court/government host. Never claim-submission, login or
form-post pages; no query strings; robots and host pauses honored by the collector.
Input: packet1/corpus/corpus.sqlite3 (read-only). Output: packet2/seeds.jsonl, config.json, plan.json.
"""
from collections import Counter
from pathlib import Path
import json
import re
import sqlite3
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / 'packet2'
P1 = HERE / 'packet1' / 'corpus' / 'corpus.sqlite3'
ROBOTS_CHECK = ROOT / 'reports/corpus_upgrade_20260919/understand/packets/settlement_robots_check.json'
sys.path.insert(0, str(HERE))
from classifier import classify  # noqa: E402

MAX_URLS = 100
ALLOWED_TYPES = {
    'settlement_agreement', 'master_settlement_agreement', 'settlement_term_sheet', 'trust_agreement',
    'long_form_notice', 'short_form_notice',
    'preliminary_approval_order', 'final_approval_order', 'final_judgment', 'consent_judgment_or_decree',
    'fee_order', 'case_management_or_settlement_order', 'dismissal_order', 'plan_confirmation_order', 'administrative_order',
    'claim_form', 'faq_page', 'deadlines_page',
}
PAGE_TYPES = {'faq_page', 'deadlines_page'}
# Claim submission, login, registration and form-post pages are never captured (blank claim-form PDFs are documents, not portals).
PORTAL_PATH = re.compile(r'log-?in|sign-?in|submit|file-?a-?claim|dashboard|register|portal|\bupload\b|checkout', re.I)
CLAIM_PAGE = re.compile(r'claim-?form|/claim\b', re.I)
GOV_RELEVANCE = re.compile(r'settlement|refund|claim|redress|compensation|consent|order|notice|agreement|judgment|distribution', re.I)


def is_government(host):
    return host.endswith('.gov') or host.endswith('.uscourts.gov')


def is_pdf(url):
    return bool(re.search(r'\.pdf$', urlsplit(url).path, re.I))


def known_urls():
    known = set()
    for db_path, sql in (
        (ROOT / 'catalog/documents.sqlite3', 'SELECT source_url FROM versions UNION SELECT final_url FROM versions'),
        (ROOT / 'delivery/archive-directory/directory.sqlite3', 'SELECT source_url FROM records'),
    ):
        with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
            known.update(x[0] for x in db.execute(sql) if x[0])
    return known


def blocked_hosts():
    """Hosts paused by the shared coordinator, by packet 1, or held by the audit (403 / TLS / exclusions)."""
    blocked = {}
    shared = ROOT / 'corpus/_shared_hosts/hosts.sqlite3'
    if shared.exists():
        with sqlite3.connect(shared.as_uri() + '?mode=ro', uri=True) as db:
            blocked.update(db.execute("SELECT host, pause_reason FROM host_state WHERE COALESCE(pause_reason,'')!=''"))
    with sqlite3.connect(P1.as_uri() + '?mode=ro', uri=True) as db:
        blocked.update(db.execute("SELECT host, pause_reason FROM hosts WHERE COALESCE(pause_reason,'')!=''"))
    if ROBOTS_CHECK.exists():
        for item in json.loads(ROBOTS_CHECK.read_text(encoding='utf-8')).get('held') or []:
            host = urlsplit(item.get('url') or '').netloc.lower()
            if host:
                blocked.setdefault(host, 'audit_held: ' + (item.get('reason') or '')[:80])
    return blocked


def observed_links():
    """Distinct link targets from complete packet-1 page captures with their anchor texts."""
    db = sqlite3.connect(P1.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    pages = {r['url']: dict(r) for r in db.execute("SELECT url, host, status, title, raw_path FROM resources WHERE status='downloaded'")}
    rows = db.execute("SELECT l.target_url, l.anchor_text, l.relation, r.url AS source_url, r.host AS source_host "
                      "FROM links l JOIN resources r ON r.id=l.source_id WHERE r.status='downloaded' AND l.relation='link'")
    targets = {}
    for r in rows:
        t = r['target_url']
        if not t or not t.startswith(('http://', 'https://')):
            continue
        entry = targets.setdefault(t, {'anchors': [], 'sources': set()})
        anchor = ' '.join((r['anchor_text'] or '').split())
        if anchor and anchor not in entry['anchors']:
            entry['anchors'].append(anchor)
        entry['sources'].add(r['source_url'])
    db.close()
    return pages, targets


def decide(url, anchors, sources, pages, known, blocked, seen):
    parts = urlsplit(url)
    host = parts.netloc.lower()
    source_hosts = {urlsplit(s).netloc.lower() for s in sources}
    pdf = is_pdf(url)
    result = None
    for anchor in anchors or ['']:
        res = classify(link_text=anchor or None, url=url, is_html=not pdf)
        if res['type'] in ALLOWED_TYPES:
            result = (anchor, res)
            break
    if result is None:
        return None, 'not_a_targeted_document_type'
    anchor, res = result
    kind = res['type']
    if url in seen:
        return None, 'duplicate'
    if url in pages or url in known:
        return None, 'already_saved_exact_url'
    if parts.query or parts.fragment:
        return None, 'query_string_or_fragment'
    if PORTAL_PATH.search(parts.path) or (CLAIM_PAGE.search(parts.path) and not pdf):
        return None, 'claim_login_or_form_like_url'
    if kind == 'claim_form' and not pdf:
        return None, 'claim_form_page_not_blank_pdf'
    if kind not in PAGE_TYPES and not pdf:
        return None, 'document_type_requires_pdf'
    same_host = host in source_hosts
    if not same_host and not is_government(host):
        return None, 'third_party_host_not_court_or_government'
    if is_government(host):
        # Government portals host unrelated FAQs: keep a link only when the captured page is the site root
        # (a dedicated program site), the target sits under the captured page's path, or the link names the matter.
        source_paths = [urlsplit(s).path.rstrip('/') for s in sources if urlsplit(s).netloc.lower() == host]
        under_page = any(not sp or parts.path.startswith(sp + '/') for sp in source_paths)
        if not under_page and not GOV_RELEVANCE.search((anchor or '') + ' ' + parts.path):
            return None, 'government_link_without_settlement_relevance'
    if host in blocked:
        return None, 'host_blocked: ' + str(blocked[host])
    if res['type_basis'] == 'url_path':
        return None, 'site_root_not_a_document'
    return {'anchor': anchor, 'classification': res, 'kind': kind, 'host': host, 'path': parts.path, 'pdf': pdf,
            'sources': sorted(sources), 'same_host': same_host}, None


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')


def prepare():
    OUT.mkdir(exist_ok=True)
    pages, targets = observed_links()
    known, blocked = known_urls(), blocked_hosts()
    seeds, skipped, seen = [], Counter(), set()
    skipped_examples = {}
    for url in sorted(targets):
        info = targets[url]
        chosen, reason = decide(url, info['anchors'], info['sources'], pages, known, blocked, seen)
        if not chosen:
            skipped[reason] += 1
            if reason != 'not_a_targeted_document_type':
                skipped_examples.setdefault(reason, []).append(url)
            continue
        seen.add(url)
        seeds.append({
            'url': url, 'title': chosen['anchor'] or None, 'source_family': 'packet1_page_document_link',
            'category': chosen['kind'], 'expected_document_type': chosen['kind'],
            'classifier': chosen['classification'], 'link_text': chosen['anchor'], 'is_pdf': chosen['pdf'],
            'discovered_from': chosen['sources'][0], 'observed_on_pages': chosen['sources'], 'same_host_as_page': chosen['same_host'],
            'jurisdiction': {'country': 'US'}, 'scope': {'host': chosen['host'], 'path_prefixes': [chosen['path'] or '/']},
            'packet': 2, 'packet1_corpus': P1.relative_to(ROOT).as_posix(),
        })
    # Documents first, then pages, then alphabetical; cap at the packet limit.
    seeds.sort(key=lambda s: (0 if s['is_pdf'] else 1, 0 if s['category'] not in PAGE_TYPES else 1, s['url']))
    deferred = seeds[MAX_URLS:]
    seeds = seeds[:MAX_URLS]
    hosts = {}
    for seed in seeds:
        hosts.setdefault(seed['scope']['host'], set()).update(seed['scope']['path_prefixes'])
    (OUT / 'seeds.jsonl').write_text(''.join(json.dumps(s, ensure_ascii=False) + '\n' for s in seeds), encoding='utf-8', newline='\n')
    write(OUT / 'config.json', {
        'allow': [{'host': host, 'path_prefixes': sorted(paths)} for host, paths in sorted(hosts.items())],
        'workers': 8, 'per_host_delay': 2.0, 'respect_robots': True, 'shared_host_dir': 'corpus/_shared_hosts',
        'follow_links': False, 'follow_external_allowed_links': False, 'max_depth': 0, 'max_retries': 0,
        'timeout_seconds': 30, 'max_transfer_seconds': 120, 'pause_host_on_access_block': True,
        'user_agent': 'LegalCorpusResearch/1.0'})
    write(OUT / 'plan.json', {
        'packet': 2, 'input': P1.relative_to(ROOT).as_posix(), 'observed_link_targets': len(targets),
        'selected': len(seeds), 'hosts': len(hosts), 'deferred_over_limit': [s['url'] for s in deferred],
        'selected_by_type': dict(Counter(s['category'] for s in seeds)), 'selected_pdf': sum(1 for s in seeds if s['is_pdf']),
        'skipped_by_reason': dict(skipped), 'skipped_examples': {k: v[:25] for k, v in skipped_examples.items()},
        'scope': 'Exact document/page links observed on packet-1 captures; classifier-typed agreement/notice/order/claim-form PDF/FAQ/deadlines; '
                 'same host as the captured page or a .gov/.uscourts.gov host. GET only, no link following, no forms, no claim pages, no query strings.',
        'limits': {'workers': 8, 'max_resource_attempts': MAX_URLS, 'max_seconds': 600, 'minimum_host_delay_seconds': 2,
                   'robots': 'evaluated at fetch time by the collector; crawl-delay honored', 'automatic_retries': 0},
        'deduplication': 'Exact URL against packet-1 captures, catalog/documents.sqlite3 versions (source_url, final_url) and directory.sqlite3 records.source_url.',
        'complete_site_claim': False})
    print(json.dumps({'seeds': len(seeds), 'hosts': len(hosts), 'by_type': dict(Counter(s['category'] for s in seeds)),
                      'skipped': dict(skipped), 'deferred': len(deferred)}, indent=1))


if __name__ == '__main__':
    prepare()
