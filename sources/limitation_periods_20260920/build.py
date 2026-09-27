"""Limitation periods by state and claim type, with a check against the statute text saved in this library (2026-09-20).

Two layers, kept apart:

1. The published table: "Advottic US Statute of Limitations Dataset" v2026.06 (Techno Optics LLC, CC BY 4.0), 51 jurisdictions x 9
   claim types, years plus a short caveat. It prints no statutory citations. Pinned copy and SHA-256 in
   sources/upstream_open_data_20260920. Attribution: Techno Optics LLC. (2026). Advottic Legal Data.
   https://github.com/TechnoOptics/legal-data. CC BY 4.0. Values are reproduced unchanged.

2. A library check for three claim types (personal injury, wrongful death, medical malpractice). CANDIDATES names, for each
   state, the code section generally cited for that period. A candidate is only a place to look: the build opens that exact
   citation in the saved law catalog and records one of four outcomes, with the matching sentence when there is one:
     period_wording_found      the saved section contains the table's period in words or digits, near words naming the claim
     same_period_general       the saved section contains the same period but does not name this claim type (a general
                               limitations section, or one that reaches the claim through another section)
     different_wording_found   the saved section exists and speaks of another period (shown; the table value is not corrected)
     section_saved_no_period   the saved section exists but no period wording was recognised
     section_not_saved         no saved provision has that citation (or the state's statutes are not in the snapshot)
   Nothing here is legal advice, a statement of current law, or a ruling on which period governs a given claim.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UPSTREAM = ROOT / 'sources/upstream_open_data_20260920/repos/TechnoOptics__legal-data'
TABLE = UPSTREAM / 'files/data/statute-of-limitations.json'
CATALOG = ROOT / 'sources/open_us_law_20260918/catalog.sqlite3'
OUT = HERE / 'limitation_periods.sqlite3'
LICENSE = 'techno_optics_advottic_legal_data_cc_by_4_0'
ATTRIBUTION = 'Techno Optics LLC. (2026). Advottic Legal Data. https://github.com/TechnoOptics/legal-data. CC BY 4.0.'
QUALIFICATION = ('Periods are a third-party summary table (Advottic Legal Data, reviewed 2026-06-08, CC BY 4.0), reproduced unchanged. Where a code section is named, this '
                 'library opened that section in its saved state code and reports only whether the same period wording appears there. Tolling, discovery rules, repose, '
                 'claim-specific statutes and later amendments are not analysed. Not legal advice; confirm against the current official code.')

PI, WD, MM = 'personal-injury', 'wrongful-death', 'medical-malpractice'
CANDIDATES = {
    'AL': {PI: 'Ala. Code § 6-2-38', WD: 'Ala. Code § 6-5-410', MM: 'Ala. Code § 6-5-482'},
    'AK': {PI: 'Alaska Stat. § 09.10.070', WD: 'Alaska Stat. § 09.55.580', MM: 'Alaska Stat. § 09.10.070'},
    'AZ': {PI: 'A.R.S. § 12-542', WD: 'A.R.S. § 12-542', MM: 'A.R.S. § 12-542'},
    'AR': {PI: 'Ark. Code Ann. § 16-56-105', WD: 'Ark. Code Ann. § 16-62-102', MM: 'Ark. Code Ann. § 16-114-203'},
    'CA': {PI: 'Cal. CCP § 335.1', WD: 'Cal. CCP § 335.1', MM: 'Cal. CCP § 340.5'},
    'CO': {PI: 'C.R.S. § 13-80-102', WD: 'C.R.S. § 13-80-102', MM: 'C.R.S. § 13-80-102.5'},
    'CT': {PI: 'Conn. Gen. Stat. § 52-584', WD: 'Conn. Gen. Stat. § 52-555', MM: 'Conn. Gen. Stat. § 52-584'},
    'DC': {PI: 'D.C. Code § 12-301', WD: 'D.C. Code § 16-2702', MM: 'D.C. Code § 12-301'},
    'DE': {PI: '10 Del. C. § 8119', WD: '10 Del. C. § 8107', MM: '18 Del. C. § 6856'},
    'FL': {PI: 'Fla. Stat. § 95.11', WD: 'Fla. Stat. § 95.11', MM: 'Fla. Stat. § 95.11'},
    'HI': {PI: 'Haw. Rev. Stat. § 657-7', WD: 'Haw. Rev. Stat. § 663-3', MM: 'Haw. Rev. Stat. § 657-7.3'},
    'ID': {PI: 'Idaho Code § 5-219', WD: 'Idaho Code § 5-219', MM: 'Idaho Code § 5-219'},
    'IL': {PI: '735 ILCS 5/13-202', WD: '740 ILCS 180/2', MM: '735 ILCS 5/13-212'},
    'IN': {PI: 'Ind. Code § 34-11-2-4', WD: 'Ind. Code § 34-23-1-1', MM: 'Ind. Code § 34-18-7-1'},
    'IA': {PI: 'Iowa Code § 614.1', WD: 'Iowa Code § 614.1', MM: 'Iowa Code § 614.1'},
    'KS': {PI: 'K.S.A. § 60-513', WD: 'K.S.A. § 60-513', MM: 'K.S.A. § 60-513'},
    'KY': {PI: 'KRS § 413.140', WD: 'KRS § 413.180', MM: 'KRS § 413.140'},
    'LA': {PI: 'La. Civ. Code art. 3493.1', WD: 'La. Civ. Code art. 2315.2', MM: 'La. Rev. Stat. § 9:5628'},  # art. 3492 was repealed in 2024; 3493.1 is the saved delictual-actions article
    'ME': {PI: '14 M.R.S. § 752', WD: '18-C M.R.S. § 2-807', MM: '24 M.R.S. § 2902'},
    'MD': {PI: 'Md. Code, Courts and Judicial Proceedings § 5-101', WD: 'Md. Code, Courts and Judicial Proceedings § 3-904', MM: 'Md. Code, Courts and Judicial Proceedings § 5-109'},
    'MA': {PI: 'Mass. Gen. Laws ch. 260, sec. 2A', WD: 'Mass. Gen. Laws ch. 229, sec. 2', MM: 'Mass. Gen. Laws ch. 260, sec. 4'},
    'MI': {PI: 'Mich. Comp. Laws § 600.5805', WD: 'Mich. Comp. Laws § 600.5805', MM: 'Mich. Comp. Laws § 600.5805'},
    'MN': {PI: 'Minn. Stat. § 541.05', WD: 'Minn. Stat. § 573.02', MM: 'Minn. Stat. § 541.076'},
    'MS': {PI: 'Miss. Code Ann. § 15-1-49', WD: 'Miss. Code Ann. § 15-1-49', MM: 'Miss. Code Ann. § 15-1-36'},
    'MO': {PI: 'Mo. Rev. Stat. § 516.120', WD: 'Mo. Rev. Stat. § 537.100', MM: 'Mo. Rev. Stat. § 516.105'},
    'MT': {PI: 'Mont. Code Ann. § 27-2-204', WD: 'Mont. Code Ann. § 27-2-204', MM: 'Mont. Code Ann. § 27-2-205'},
    'NE': {PI: 'Neb. Rev. Stat. § 25-207', WD: 'Neb. Rev. Stat. § 30-810', MM: 'Neb. Rev. Stat. § 25-222'},
    'NV': {PI: 'Nev. Rev. Stat. § 11.190', WD: 'Nev. Rev. Stat. § 11.190', MM: 'Nev. Rev. Stat. § 41A.097'},
    'NH': {PI: 'N.H. Rev. Stat. § 508:4', WD: 'N.H. Rev. Stat. § 508:4', MM: 'N.H. Rev. Stat. § 508:4'},
    'NJ': {PI: 'N.J. Stat. § 2A:14-2', WD: 'N.J. Stat. § 2A:31-3', MM: 'N.J. Stat. § 2A:14-2'},
    'NM': {PI: 'N.M. Stat. § 37-1-8', WD: 'N.M. Stat. § 41-2-2', MM: 'N.M. Stat. § 41-5-13'},
    'NY': {PI: 'N.Y. CVP Law § 214', WD: 'N.Y. EPT Law § 5-4.1', MM: 'N.Y. CVP Law § 214-A'},
    'ND': {PI: 'N.D. Cent. Code § 28-01-16', WD: 'N.D. Cent. Code § 28-01-18', MM: 'N.D. Cent. Code § 28-01-18'},
    'OH': {PI: 'Ohio Rev. Code § 2305.10', WD: 'Ohio Rev. Code § 2125.02', MM: 'Ohio Rev. Code § 2305.113'},
    'OK': {PI: 'Okla. Stat. tit. 12, § 12-95', WD: 'Okla. Stat. tit. 12, § 12-1053', MM: 'Okla. Stat. tit. 76, § 76-18'},
    'OR': {PI: 'ORS § 12.110', WD: 'ORS § 30.020', MM: 'ORS § 12.110'},
    'PA': {PI: '42 Pa.C.S. § 5524', WD: '42 Pa.C.S. § 5524', MM: '42 Pa.C.S. § 5524'},
    'RI': {PI: 'R.I. Gen. Laws § 9-1-14', WD: 'R.I. Gen. Laws § 10-7-2', MM: 'R.I. Gen. Laws § 9-1-14.1'},
    'SC': {PI: 'S.C. Code Ann. § 15-3-530', WD: 'S.C. Code Ann. § 15-3-530', MM: 'S.C. Code Ann. § 15-3-545'},
    'SD': {PI: 'S.D. Codified Laws § 15-2-14', WD: 'S.D. Codified Laws § 21-5-3', MM: 'S.D. Codified Laws § 15-2-14.1'},
    'TN': {PI: 'Tenn. Code Ann. § 28-3-104', WD: 'Tenn. Code Ann. § 28-3-104', MM: 'Tenn. Code Ann. § 29-26-116'},
    'TX': {PI: 'Tex. Civil Practice and Remedies Code § 16.003', WD: 'Tex. Civil Practice and Remedies Code § 16.003', MM: 'Tex. Civil Practice and Remedies Code § 74.251'},
    'UT': {PI: 'Utah Code § 78B-2-307', WD: 'Utah Code § 78B-2-304', MM: 'Utah Code § 78B-3-404'},
    'VT': {PI: '12 V.S.A. § 512', WD: '14 V.S.A. § 1492', MM: '12 V.S.A. § 521'},
    'VA': {PI: 'Va. Code Ann. § 8.01-243', WD: 'Va. Code Ann. § 8.01-244', MM: 'Va. Code Ann. § 8.01-243'},
    'WA': {PI: 'RCW 4.16.080', WD: 'RCW 4.16.080', MM: 'RCW 4.16.350'},
    'WV': {PI: 'W. Va. Code § 55-2-12', WD: 'W. Va. Code § 55-7-6', MM: 'W. Va. Code § 55-7B-4'},
    'WI': {PI: 'Wis. Stat. § 893.54', WD: 'Wis. Stat. § 893.54', MM: 'Wis. Stat. § 893.55'},
    'WY': {PI: 'Wyo. Stat. § 1-3-105', WD: 'Wyo. Stat. § 1-38-102', MM: 'Wyo. Stat. § 1-3-107'},
}
NOT_IN_SNAPSHOT = {'GA': 'Georgia statutes were withdrawn from the saved law snapshot by its publisher.', 'NC': 'North Carolina statutes were withdrawn from the saved law snapshot by its publisher.'}
WORDS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten'}
KEYWORDS = {PI: r'injur', WD: r'death', MM: r'malpractice|health care|medical|physician|hospital'}
ANY_PERIOD = re.compile(r'\b(one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s*(?:\(\d{1,2}\)\s*)?(?:and one-half\s+|and a half\s+)?years?\b', re.I)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def period_pattern(years):
    if years == 2.5:
        return re.compile(r'\btwo\s+years?\s+and\s+six\s+months\b|\btwo and one-half years\b|\b2\.5 years\b|\bthirty months\b', re.I)
    whole = int(years)
    if whole != years or whole not in WORDS:
        return None
    return re.compile(r'\b(?:%s|%d)\s*(?:\(\s*%d\s*\)\s*)?-?\s*years?\b' % (WORDS[whole], whole, whole), re.I)


def sentence(text, match):
    start = max(text.rfind('.', 0, match.start()) + 1, match.start() - 260)
    end = text.find('.', match.end())
    end = min(end + 1 if end != -1 else len(text), match.end() + 260)
    return ' '.join(text[start:end].split())[:520]


def check(catalog, usps, claim, years, citation):
    rows = catalog.execute("SELECT id, title, text, status FROM records WHERE citation=? AND state=? AND kind='statutes'", (citation, usps)).fetchall()
    if not rows:
        return {'outcome': 'section_not_saved', 'record_id': '', 'excerpt': '', 'other_periods': ''}
    record_id, _title, text, _status = max(rows, key=lambda r: len(r[2] or ''))
    text = (text or '').replace('\\n', ' ')
    pattern = period_pattern(years)
    keyword = re.compile(KEYWORDS[claim], re.I)
    first_same = None
    for found in (pattern.finditer(text) if pattern else []):
        first_same = first_same or found
        window = text[max(0, found.start() - 600):found.end() + 600]
        if keyword.search(window):
            return {'outcome': 'period_wording_found', 'record_id': record_id, 'excerpt': sentence(text, found), 'other_periods': ''}
    if first_same:
        return {'outcome': 'same_period_general', 'record_id': record_id, 'excerpt': sentence(text, first_same), 'other_periods': ''}
    others = sorted({m.group(0).lower() for m in ANY_PERIOD.finditer(text)})
    if others:
        first = ANY_PERIOD.search(text)
        return {'outcome': 'different_wording_found', 'record_id': record_id, 'excerpt': sentence(text, first), 'other_periods': '; '.join(others[:8])}
    return {'outcome': 'section_saved_no_period', 'record_id': record_id, 'excerpt': '', 'other_periods': ''}


def main():
    manifest = json.loads((UPSTREAM / 'manifest.json').read_text(encoding='utf-8'))
    if sha256(TABLE) != manifest['files']['data/statute-of-limitations.json']['sha256']:
        raise SystemExit('published table changed since it was fetched')
    table = json.loads(TABLE.read_text(encoding='utf-8'))
    catalog = sqlite3.connect(CATALOG.as_uri() + '?mode=ro', uri=True)
    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript('''
        CREATE TABLE claim_types(id TEXT PRIMARY KEY, label TEXT, description TEXT, position INTEGER);
        CREATE TABLE periods(id TEXT PRIMARY KEY, usps TEXT, state TEXT, claim_type TEXT, claim_label TEXT, years REAL, years_label TEXT, note TEXT,
                             candidate_citation TEXT, outcome TEXT, record_id TEXT, excerpt TEXT, other_periods TEXT, snapshot_note TEXT);
    ''')
    labels = {}
    for position, claim in enumerate(table['claimTypes']):
        labels[claim['id']] = claim['label']
        db.execute('INSERT INTO claim_types VALUES(?,?,?,?)', (claim['id'], claim['label'], claim.get('description') or '', position))
    outcomes = {}
    for state in table['states']:
        usps = state['abbr']
        for claim, limit in state['limits'].items():
            years = limit.get('years')
            citation = (CANDIDATES.get(usps) or {}).get(claim, '')
            result = {'outcome': '', 'record_id': '', 'excerpt': '', 'other_periods': ''}
            snapshot_note = NOT_IN_SNAPSHOT.get(usps, '') if claim in (PI, WD, MM) else ''
            if citation and years is not None:
                result = check(catalog, usps, claim, years, citation)
            elif claim in (PI, WD, MM):
                result['outcome'] = 'section_not_saved' if snapshot_note else ''
            if result['outcome']:
                outcomes[result['outcome']] = outcomes.get(result['outcome'], 0) + 1
            label = ('%g years' % years if years != 1 else '1 year') if years is not None else 'Not stated'
            db.execute('INSERT INTO periods VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('%s:%s' % (usps, claim), usps, state['name'], claim, labels.get(claim, claim), years, label, limit.get('note') or '',
                                                                              citation, result['outcome'], result['record_id'], result['excerpt'], result['other_periods'], snapshot_note))
    db.executescript('CREATE INDEX periods_state ON periods(usps); CREATE INDEX periods_claim ON periods(claim_type);')
    db.commit()
    counts = {'jurisdictions': len(table['states']), 'claim_types': len(table['claimTypes']), 'periods': db.execute('SELECT count(*) FROM periods').fetchone()[0],
              'sections_named': db.execute("SELECT count(*) FROM periods WHERE candidate_citation<>''").fetchone()[0], 'library_check': outcomes,
              'table_version': table.get('version'), 'table_reviewed': table.get('dateModified')}
    checks = {'every_published_cell_is_a_row': counts['periods'] == sum(len(s['limits']) for s in table['states']),
              'values_reproduced_unchanged': all(db.execute('SELECT years FROM periods WHERE id=?', ('%s:%s' % (s['abbr'], c),)).fetchone()[0] == l.get('years') for s in table['states'] for c, l in s['limits'].items()),
              'every_found_wording_has_its_record_and_sentence': db.execute("SELECT count(*) FROM periods WHERE outcome IN ('period_wording_found','same_period_general','different_wording_found') AND (record_id='' OR excerpt='')").fetchone()[0] == 0}
    db.execute('VACUUM')
    db.close()
    stat = CATALOG.stat()
    validation = {'schema_version': 1, 'status': 'passed' if all(checks.values()) else 'failed', 'ready': all(checks.values()), 'validated_at': datetime.now(timezone.utc).isoformat(),
                  'data_files': [{'path': OUT.name, 'sha256': sha256(OUT), 'rows': counts['periods']}], 'counts': counts, 'checks': checks, 'qualification': QUALIFICATION,
                  'license_ref': LICENSE, 'attribution': ATTRIBUTION,
                  'inputs': [{'path': 'sources/upstream_open_data_20260920/repos/TechnoOptics__legal-data/files/data/statute-of-limitations.json', 'sha256': sha256(TABLE), 'commit': manifest['commit']},
                             {'path': 'sources/open_us_law_20260918/catalog.sqlite3', 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}]}
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'checks': checks}, indent=1))


if __name__ == '__main__':
    main()
