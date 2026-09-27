"""Free evidence capture by plain HTTP (no provider, no credits) for bounded, reviewed URL lists (2026-09-19).

Saves one JSON per URL in the same shape the county-filing merge already reads (data.markdown, data.links,
data.metadata.sourceURL/url/statusCode/title), marked capture_kind "direct_http". Polite by construction: robots.txt is
respected, one request per host at a time with a two-second pause, a plain research User-Agent, no retries, no cookies,
no JavaScript. A blocked, disallowed or failed address is recorded as such and never retried or routed elsewhere.

    python scripts/direct_capture.py scrape --out <dir> --urls <file with one URL per line> [--max 60]
"""
from __future__ import annotations

import argparse
import hashlib
import html.parser
import io
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

AGENT = 'LegalCorpusResearch/1.0'
MAX_BYTES = 25 * 1024 * 1024


class Reader(html.parser.HTMLParser):
    """Visible text with links kept as [text](absolute url); script/style/nav chrome dropped."""
    SKIP = {'script', 'style', 'noscript', 'svg', 'template'}
    BLOCK = {'p', 'div', 'li', 'tr', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'table', 'ul', 'ol', 'dd', 'dt'}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.links, self.skip, self.href, self.anchor, self.title, self.in_title = base, [], [], 0, None, [], '', False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == 'title':
            self.in_title = True
        elif tag == 'a':
            href = dict(attrs).get('href')
            if href:
                try:
                    self.href = urllib.parse.urldefrag(urllib.parse.urljoin(self.base, href.strip()))[0]
                except ValueError:
                    self.href = None
                self.anchor = []
        elif tag in self.BLOCK:
            self.parts.append('\n')
        if tag in ('h1', 'h2', 'h3'):
            self.parts.append('#' * int(tag[1]) + ' ')

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == 'title':
            self.in_title = False
        elif tag == 'a' and self.href:
            text = ' '.join(''.join(self.anchor).split())
            if self.href.startswith(('http://', 'https://')):
                self.links.append(self.href)
                self.parts.append('[%s](%s)' % (text or self.href, self.href))
            else:
                self.parts.append(text)
            self.href = None
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
        elif self.href is not None:
            self.anchor.append(data)
        else:
            self.parts.append(data)

    def text(self):
        lines = [' '.join(line.split()) for line in ''.join(self.parts).split('\n')]
        out, blank = [], False
        for line in lines:
            if line:
                out.append(line)
                blank = False
            elif not blank:
                out.append('')
                blank = True
        return '\n'.join(out).strip()


def pdf_text(data: bytes, pages: int = 40) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        return '\n\n'.join((page.extract_text() or '') for page in reader.pages[:pages]).strip()
    except Exception:
        return ''


def robots_allowed(url, cache, lock):
    parts = urllib.parse.urlsplit(url)
    origin = parts.scheme + '://' + parts.netloc
    with lock:
        rules = cache.get(origin)
    if rules is None:
        rules = urllib.robotparser.RobotFileParser()
        try:
            request = urllib.request.Request(origin + '/robots.txt', headers={'User-Agent': AGENT})
            with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
                rules.parse(response.read(512 * 1024).decode('utf-8', 'replace').splitlines())
        except urllib.error.HTTPError as error:
            rules.parse([] if error.code in (404, 410) else ['User-agent: *', 'Disallow: /'])
        except Exception:
            rules.parse(['User-agent: *', 'Disallow: /'])  # unreachable robots.txt: do not guess permission
        with lock:
            cache[origin] = rules
    return rules.can_fetch(AGENT, url)


def scrape_all(urls, out: Path, limit: int):
    out.mkdir(parents=True, exist_ok=True)
    receipts = out / 'receipts.jsonl'
    done = set()
    if receipts.exists():
        for line in receipts.read_text(encoding='utf-8').splitlines():
            if line.strip():
                done.add(json.loads(line)['url'])
    todo = [u for u in dict.fromkeys(u.strip() for u in urls if u.strip()) if u not in done and u.startswith(('http://', 'https://'))][:limit]
    cache, lock, write_lock, host_locks = {}, threading.Lock(), threading.Lock(), {}

    def one(url):
        host = urllib.parse.urlsplit(url).netloc.lower()
        with lock:
            host_lock = host_locks.setdefault(host, threading.Lock())
        with host_lock:
            record = {'url': url, 'requested_at': datetime.now(timezone.utc).isoformat(), 'capture_kind': 'direct_http'}
            try:
                if not robots_allowed(url, cache, lock):
                    record['status'] = 'robots_disallowed'
                else:
                    request = urllib.request.Request(url, headers={'User-Agent': AGENT, 'Accept': 'text/html,application/pdf;q=0.9,*/*;q=0.5'})
                    with urllib.request.urlopen(request, timeout=45, context=ssl.create_default_context()) as response:
                        raw = response.read(MAX_BYTES + 1)
                        final, status, content_type = response.geturl(), response.status, response.headers.get('Content-Type', '')
                    if len(raw) > MAX_BYTES:
                        raise ValueError('response larger than 25 MB')
                    if 'pdf' in content_type.lower() or raw[:5] == b'%PDF-':
                        markdown, links, title = pdf_text(raw), [], ''
                    else:
                        charset = 'utf-8'
                        if 'charset=' in content_type.lower():
                            charset = content_type.lower().split('charset=')[-1].split(';')[0].strip() or 'utf-8'
                        reader = Reader(final)
                        reader.feed(raw.decode(charset, 'replace'))
                        markdown, links, title = reader.text(), sorted(set(reader.links)), ' '.join(reader.title.split())
                    ok = status == 200 and len(markdown) >= 200
                    body = {'success': ok, 'capture_kind': 'direct_http', 'data': {'markdown': markdown, 'links': links,
                            'metadata': {'sourceURL': url, 'url': final, 'statusCode': status, 'title': title, 'contentType': content_type, 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'raw_bytes': len(raw)}}}
                    name = hashlib.sha256(url.encode()).hexdigest()[:24] + '.json'
                    (out / name).write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
                    record.update(status='captured' if ok else 'failed', source_status=status, file=name, final_url=final, title=title, markdown_chars=len(markdown), links=len(links),
                                  error=None if ok else 'too little readable text (script-rendered page, login wall or empty document)')
            except urllib.error.HTTPError as error:
                record.update(status='failed', source_status=error.code, error='HTTP %s' % error.code)
            except Exception as error:
                record.update(status='failed', error=type(error).__name__ + ': ' + str(error)[:200])
            time.sleep(2.0)
            with write_lock:
                with open(receipts, 'a', encoding='utf-8', newline='\n') as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            return record

    with ThreadPoolExecutor(max_workers=5) as pool:
        records = list(pool.map(one, todo))
    print(json.dumps({'requested': len(todo), 'skipped_already_done': len(done), 'captured': sum(r['status'] == 'captured' for r in records), 'failed': sum(r['status'] == 'failed' for r in records),
                      'robots_disallowed': sum(r['status'] == 'robots_disallowed' for r in records), 'credits_used': 0}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['scrape'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--urls', type=Path, required=True)
    parser.add_argument('--max', type=int, default=60)
    args = parser.parse_args()
    scrape_all(args.urls.read_text(encoding='utf-8').splitlines(), args.out, args.max)


if __name__ == '__main__':
    main()
