#!/usr/bin/env python3
"""One-off: persist the CourtListener MCP connector responses received on 2026-09-19 (UTC) for the judge-alias layer.

Each response is stored verbatim (the JSON object the connector returned) with tool, arguments, UTC time and SHA-256.
These are connector responses, not original HTTP bytes. Existing receipt files are never overwritten.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'receipts'
FIELDS = 'docket_id,court_id,assigned_to_id,referred_to_id,dateFiled,dateTerminated,docketNumber,caseName,assignedTo'
# The usage check answered with an api_usage 10/min window resetting at 2026-09-19T11:42:11Z, i.e. it was made at about 11:41:11Z;
# every docket search below was requested after that moment.
REQUESTED_AFTER = '2026-09-19T11:41:11Z'


def r(assigned_to, assigned_to_id, case_name, court_id, filed, terminated, docket_number, docket_id, referred_to_id, query_id):
    return {'query_id': query_id, 'count': 1, 'results': [{'assignedTo': assigned_to, 'assigned_to_id': assigned_to_id, 'caseName': case_name,
            'court_id': court_id, 'dateFiled': filed, 'dateTerminated': terminated, 'docketNumber': docket_number, 'docket_id': docket_id,
            'referred_to_id': referred_to_id}]}


SEARCHES = [
    (2885, 'flnd', '3:19-md-02885', r('Margaret Catharine Rodgers', 2755, 'IN RE: 3M COMBAT ARMS EARPLUG PRODUCTS LIABILITY LITIGATION', 'flnd', '2019-04-03', None, '3:19-md-02885', 14916674, 9162, '0c8843a2')),
    (2358, 'ded', '1:12-md-02358', r('Joshua D. Wolson', None, 'In Re: Google Inc. Cookie Placement Consumer Privacy Litigation', 'ded', '2012-06-12', '2026-09-01', '1:12-md-02358', 4219726, None, '74d8fdf1')),
    (3015, 'flsd', '0:21-md-03015', r('Anuraang H. Singhal', 15357, 'IN RE: Johnson & Johnson Aerosol Sunscreen Marketing, Sales Practices and Products Liability Litigation', 'flsd', '2021-10-08', '2023-04-05', '0:21-md-03015', 60635499, None, 'bbc1d79d')),
    (2740, 'laed', '2:16-md-02740', r('Jane Margaret Triche Milazzo', 2243, 'In Re: Taxotere (Docetaxel) Products Liability Litigation', 'laed', '2016-10-04', None, '2:16-md-02740', 4510790, 9270, 'bba896b8')),
    (3023, 'laed', '2:22-md-03023', r('Jane Margaret Triche Milazzo', 2243, 'In Re: Taxotere (Docetaxel) Eye Injury Products Liability Litigation', 'laed', '2022-02-01', None, '2:22-md-03023', 62995309, 9270, '796baac5')),
    (2879, 'mdd', '8:19-md-02879', r('John Preston Bailey', None, 'In Re: Marriott International, Inc., Customer Data Security Breach Litigation', 'mdd', '2019-02-06', None, '8:19-md-02879', 14550106, None, '3ad96fad')),
    (2984, 'mowd', '4:21-md-02984', r('Mary Elizabeth Phillips', 2558, 'In re: Folgers Coffee Marketing', 'mowd', '2021-04-01', None, '4:21-md-02984', 59782865, None, '679a36ad')),
    (2695, 'nmd', '1:16-md-02695', r('James O. Browning', 430, 'In Re: Santa Fe Natural Tobacco Company Marketing and Sales Practices Litigation', 'nmd', '2016-04-11', None, '1:16-md-02695', 4518570, 9394, '27625400')),
    (1358, 'nysd', '1:00-cv-01898', r('Denise Cote', 733, 'In Re: Methyl Tertiary Butyl Ether ("MTBE") Products Liability Litigation', 'nysd', '2000-03-10', '2003-02-25', '1:00-cv-01898', 4357482, None, '4007d0a3')),
    (3043, 'nysd', '1:22-md-03043', r('Denise Cote', 733, 'In Re: Acetaminophen - ASD-ADHD Products Liability Litigation', 'nysd', '2022-10-05', '2024-08-21', '1:22-md-03043', 65408277, None, '12ce8a19')),
]

USAGE = {
    'summary': '97 of 150 API requests remaining at 150/hour (53 used; the oldest request expires at 2026-09-19T11:43:17.922829+00:00). 455 of 600 API requests remaining at 600/day (145 used; the oldest request expires at 2026-09-20T04:25:29.024276+00:00). 20 of 20 API requests remaining at 20/min (0 used; the window is currently empty).',
    'current_usage': {'user': {'limits': [
        {'scope': 'user', 'rate': '150/hour', 'used': 53, 'limit': 150, 'remaining': 97, 'window_seconds': 3600, 'reset_at': '2026-09-19T11:43:17.922829+00:00', 'blocked': False},
        {'scope': 'user', 'rate': '600/day', 'used': 145, 'limit': 600, 'remaining': 455, 'window_seconds': 86400, 'reset_at': '2026-09-20T04:25:29.024276+00:00', 'blocked': False},
        {'scope': 'user', 'rate': '20/min', 'used': 0, 'limit': 20, 'remaining': 20, 'window_seconds': 60, 'reset_at': None, 'blocked': False}]}},
    'membership': {'level': 'CL Membership - Tier 1', 'is_active': True},
    'note': 'quota-relevant part of the get_api_usage response (user limits, summary, membership); the descriptive text, citation/fetch/api_usage windows and the per-day history were not needed and are not reproduced here',
}


def canonical_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    written = 0
    jobs = [('api_usage_precheck.json', {'label': 'connector response, not original HTTP bytes', 'connector': 'CourtListener MCP', 'tool': 'get_api_usage',
                                          'arguments': {}, 'mdl_number': None, 'requested_at_utc_approx': REQUESTED_AFTER, 'saved_at_utc': now,
                                          'response_sha256': canonical_sha(USAGE), 'response': USAGE})]
    for num, court, docket, response in SEARCHES:
        jobs.append(('search_mdl_%d.json' % num, {
            'label': 'connector response, not original HTTP bytes', 'connector': 'CourtListener MCP', 'tool': 'search',
            'arguments': {'type': 'd', 'q': 'MDL %d' % num, 'court': court, 'docket_number': docket, 'fields': FIELDS},
            'mdl_number': num, 'requested_after_utc': REQUESTED_AFTER, 'saved_at_utc': now,
            'response_sha256': canonical_sha(response), 'response': response}))
    for name, payload in jobs:
        path = OUT / name
        if path.exists():
            continue
        path.write_bytes(json.dumps(payload, ensure_ascii=False, indent=1).encode('utf-8'))
        written += 1
    print('receipts written', written, 'of', len(jobs))


if __name__ == '__main__':
    main()
