"""Bounded public GETs with robots checks; saves original HTTP bytes separately."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

OUT = Path(__file__).resolve().parent
UA = 'LegalCorpusResearch/1.0'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url):
    captured = datetime.now(timezone.utc).isoformat()
    try:
        response = build_opener(NoRedirect()).open(Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'identity'}), timeout=30)
    except HTTPError as error:
        response = error
    with response:
        data = response.read(8388609)
        if len(data) > 8388608:
            raise ValueError('Response exceeded finite 8 MiB limit')
        return data, {'requested_url': url, 'response_url': response.url, 'http_status': response.status, 'headers': dict(response.headers), 'captured_at': captured, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'original_http_bytes': True}


def main():
    url = 'https://www.justice.gov/resources'
    origin = 'https://' + urlsplit(url).netloc
    robots_url = origin + '/robots.txt'
    controls = OUT / 'controls'; controls.mkdir(exist_ok=True)
    body, metadata = fetch(robots_url)
    (controls / 'justice_robots.txt').write_bytes(body)
    (controls / 'justice_robots.metadata.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    if metadata['http_status'] != 200:
        raise RuntimeError('Robots policy unavailable; stopping direct capture')
    robot = RobotFileParser(robots_url); robot.parse(body.decode('utf-8', errors='replace').splitlines())
    allowed = robot.can_fetch(UA, url)
    if not allowed:
        raise RuntimeError('Robots policy disallows resources page')
    time.sleep(2)
    body, metadata = fetch(url)
    metadata.update(robots_url=robots_url, robots_allowed=allowed, user_agent=UA)
    raw = OUT / 'original_http'; raw.mkdir(exist_ok=True)
    path = raw / (metadata['sha256'] + '.html')
    path.write_bytes(body)
    metadata['raw_path'] = path.relative_to(OUT).as_posix()
    (raw / 'justice_resources.metadata.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: metadata[key] for key in ['http_status', 'bytes', 'captured_at', 'sha256', 'raw_path', 'robots_allowed']}))


if __name__ == '__main__':
    main()
