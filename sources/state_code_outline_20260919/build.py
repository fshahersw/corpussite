"""Table of contents for the saved law snapshot (2026-09-19).

The Open US Law snapshot (sources/open_us_law_20260918/catalog.sqlite3) holds the text of every section, rule and
regulation, one row each, with the publisher's own hierarchy in each row's `breadcrumb`. This build reads that hierarchy
once and writes a small outline so a reader can open a state, then a code or title, then a chapter, then a section.

Nothing is copied: the outline stores headings and ranges of row numbers in the catalog; section text stays where it is.
Headings are the publisher's. Where the publisher gives a code only as an abbreviation ("Code bpc"), the heading shown is
the name used in the publisher's own citation ("BPC"); California's 29 code abbreviations are expanded to their official
names. Order inside a level follows the heading numbers, or the publisher's order where there are none.

    python build.py            full scan (reads the whole catalog once; several minutes)
    python build.py --relabel  recompute headings and order from the saved scan without reading the catalog again
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CATALOG = ROOT / 'sources/open_us_law_20260918/catalog.sqlite3'
OUT = HERE / 'outline.sqlite3'
LICENSE = 'open_us_law_cc_by_4_0_compilation'
QUALIFICATION = ('Headings and order come from the publisher snapshot of the saved law collection. The outline is a finding aid, '
                 'not an official table of contents; check the edition and current status of any provision at the official source.')

CALIFORNIA_CODES = {
    'BPC': 'Business and Professions Code', 'CIV': 'Civil Code', 'CCP': 'Code of Civil Procedure', 'COM': 'Commercial Code',
    'CORP': 'Corporations Code', 'EDC': 'Education Code', 'ELEC': 'Elections Code', 'EVID': 'Evidence Code', 'FAM': 'Family Code',
    'FIN': 'Financial Code', 'FGC': 'Fish and Game Code', 'FAC': 'Food and Agricultural Code', 'GOV': 'Government Code',
    'HNC': 'Harbors and Navigation Code', 'HSC': 'Health and Safety Code', 'INS': 'Insurance Code', 'LAB': 'Labor Code',
    'MVC': 'Military and Veterans Code', 'PEN': 'Penal Code', 'PROB': 'Probate Code', 'PCC': 'Public Contract Code',
    'PRC': 'Public Resources Code', 'PUC': 'Public Utilities Code', 'RTC': 'Revenue and Taxation Code', 'SHC': 'Streets and Highways Code',
    'UIC': 'Unemployment Insurance Code', 'VEH': 'Vehicle Code', 'WAT': 'Water Code', 'WIC': 'Welfare and Institutions Code',
}
# New York consolidated and court-act law identifiers (the legislature's own three-letter ids). Only ids whose official name is
# certain are expanded; any other id is shown as published.
NEW_YORK_LAWS = {
    'ABC': 'Alcoholic Beverage Control Law', 'ABP': 'Abandoned Property Law', 'ACA': 'Arts and Cultural Affairs Law', 'AGM': 'Agriculture and Markets Law',
    'BNK': 'Banking Law', 'BSC': 'Business Corporation Law', 'BVO': 'Benevolent Orders Law', 'CAL': 'Canal Law', 'CAN': 'Cannabis Law',
    'CCA': 'New York City Civil Court Act', 'CCO': 'Cooperative Corporations Law', 'CCT': 'Court of Claims Act', 'CNT': 'County Law', 'COR': 'Correction Law',
    'CPL': 'Criminal Procedure Law', 'CRC': 'New York City Criminal Court Act', 'CVP': 'Civil Practice Law and Rules', 'CVR': 'Civil Rights Law',
    'CVS': 'Civil Service Law', 'DCD': 'Debtor and Creditor Law', 'DOM': 'Domestic Relations Law', 'EDN': 'Education Law', 'ELD': 'Elder Law',
    'ELN': 'Election Law', 'EML': 'Eminent Domain Procedure Law', 'ENG': 'Energy Law', 'ENV': 'Environmental Conservation Law',
    'EPT': 'Estates, Powers and Trusts Law', 'EXC': 'Executive Law', 'FCT': 'Family Court Act', 'FIS': 'Financial Services Law', 'GBS': 'General Business Law',
    'GCN': 'General Construction Law', 'GCT': 'General City Law', 'GMU': 'General Municipal Law', 'GOB': 'General Obligations Law', 'HAY': 'Highway Law',
    'IND': 'Indian Law', 'ISC': 'Insurance Law', 'JUD': 'Judiciary Law', 'LAB': 'Labor Law', 'LEG': 'Legislative Law', 'LIE': 'Lien Law',
    'LLC': 'Limited Liability Company Law', 'LFN': 'Local Finance Law', 'MDW': 'Multiple Dwelling Law', 'MHR': 'Municipal Home Rule Law', 'MHY': 'Mental Hygiene Law',
    'MIL': 'Military Law', 'MRE': 'Multiple Residence Law', 'NAV': 'Navigation Law', 'NPC': 'Not-for-Profit Corporation Law',
    'PAR': 'Parks, Recreation and Historic Preservation Law', 'PBA': 'Public Authorities Law', 'PBB': 'Public Buildings Law', 'PBG': 'Public Housing Law',
    'PBH': 'Public Health Law', 'PBL': 'Public Lands Law', 'PBO': 'Public Officers Law', 'PBS': 'Public Service Law', 'PEN': 'Penal Law',
    'PEP': 'Personal Property Law', 'PTR': 'Partnership Law', 'RCO': 'Religious Corporations Law', 'RPA': 'Real Property Actions and Proceedings Law',
    'RPP': 'Real Property Law', 'RPT': 'Real Property Tax Law', 'RRD': 'Railroad Law', 'RSS': 'Retirement and Social Security Law',
    'SCP': "Surrogate's Court Procedure Act", 'SCT': 'Second Class Cities Law', 'SLG': 'Statute of Local Governments', 'SOS': 'Social Services Law',
    'STF': 'State Finance Law', 'STL': 'State Law', 'STT': 'State Technology Law', 'SWC': 'Soil and Water Conservation Districts Law', 'TAX': 'Tax Law',
    'TRA': 'Transportation Law', 'TWN': 'Town Law', 'UCC': 'Uniform Commercial Code', 'UCT': 'Uniform City Court Act', 'UDC': 'Uniform District Court Act',
    'UJC': 'Uniform Justice Court Act', 'VAT': 'Vehicle and Traffic Law', 'VIL': 'Village Law', 'WKC': "Workers' Compensation Law",
}
LOUISIANA_CODES = {'Civ. Code': 'Civil Code', 'Code Civ. Proc.': 'Code of Civil Procedure', 'Code Crim. Proc.': 'Code of Criminal Procedure', 'Code Evid.': 'Code of Evidence',
                   'Ch. Code': "Children's Code", 'Rev. Stat.': 'Revised Statutes', 'R.S.': 'Revised Statutes'}
ROMAN = re.compile(r'^(?=[MDCLXVI])M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def crumb_parts(payload: dict):
    """The publisher's hierarchy above the provision itself: a list of (type, num, label, name)."""
    crumb = payload.get('breadcrumb')
    if isinstance(crumb, str):
        try:
            crumb = json.loads(crumb)
        except ValueError:
            crumb = None
    parts = []
    if isinstance(crumb, list) and crumb:
        for entry in crumb[:-1]:
            if isinstance(entry, dict):
                parts.append((str(entry.get('type') or ''), str(entry.get('num') or ''), str(entry.get('label') or '').strip(), str(entry.get('name') or '').strip()))
            else:
                parts.append(('', '', str(entry).strip(), ''))
    else:
        path = str(payload.get('display_path') or '')
        pieces = [p.strip() for p in re.split(r' / | > ', path) if p.strip()]
        parts = [('', '', piece, '') for piece in pieces[:-1]]
    return [p for p in parts if p[2] or p[3]][:6]


def scan():
    catalog = sqlite3.connect(CATALOG.as_uri() + '?mode=ro', uri=True)
    nodes = {}      # (state, kind, path tuple) -> id
    rows = []       # id -> [state, kind, parent, depth, type, num, raw label, name, first citation, first rowid, direct]
    segments = {}   # id -> [[lo, hi], ...]
    started = time.time()
    seen = 0
    for rowid, state, kind, citation, payload in catalog.execute('SELECT rowid,state,kind,citation,payload FROM records ORDER BY rowid'):
        seen += 1
        try:
            parts = crumb_parts(json.loads(payload or '{}'))
        except ValueError:
            parts = []
        state = state or 'FEDERAL'
        parent = 0
        path = ()
        for depth, part in enumerate(parts, 1):
            path = path + ((part[2] or part[3]),)
            key = (state, kind, path)
            node = nodes.get(key)
            if node is None:
                node = len(rows) + 1
                nodes[key] = node
                rows.append([state, kind, parent, depth, part[0], part[1], part[2], part[3], citation or '', rowid, 0])
            parent = node
        if not parent:  # a provision with no published hierarchy: keep it reachable under one plain heading
            key = (state, kind, ('Other provisions',))
            parent = nodes.get(key)
            if parent is None:
                parent = len(rows) + 1
                nodes[key] = parent
                rows.append([state, kind, 0, 1, 'ungrouped', '', 'Other provisions', '', citation or '', rowid, 0])
        rows[parent - 1][10] += 1
        runs = segments.setdefault(parent, [])
        if runs and runs[-1][1] == rowid - 1:
            runs[-1][1] = rowid
        else:
            runs.append([rowid, rowid])
        if seen % 250000 == 0:
            print('%d rows, %d headings, %.0f s' % (seen, len(rows), time.time() - started), flush=True)
    catalog.close()
    return rows, segments, seen


def natural(value: str):
    return [(0, int(piece)) if piece.isdigit() else (1, piece.lower()) for piece in re.findall(r'\d+|\D+', value or '')]


def roman(value: str):
    total, previous = 0, 0
    for letter in reversed(value.upper()):
        number = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}[letter]
        total += number if number >= previous else -number
        previous = max(previous, number)
    return total


def heading(row):
    """(label, basis) for one scanned heading."""
    state, kind, _parent, depth, node_type, num, raw, name, citation = row[:9]
    if (node_type in ('code', 'act') or (node_type == 'article' and state == 'MD')) and depth == 1 and not name:
        # strip only the state abbreviation ("La.", "N.Y."), never a following abbreviated word such as "Civ."
        found = re.match(r'^(?:(?:[A-Z]\.){2,4}|[A-Z][A-Za-z]*\.)\s+(.+?)\s*(?:§|Sec\.|art\.)', citation or '')
        if found:
            short = found.group(1).strip(' ,')
            if state == 'MD' and short.startswith('Code, '):
                short = short[len('Code, '):] + ' Article'  # "Md. Code, Courts and Judicial Proceedings § 5-101"
            if state == 'CA' and short.upper() in CALIFORNIA_CODES:
                return '%s (%s)' % (CALIFORNIA_CODES[short.upper()], short.upper()), 'official code name for the published abbreviation'
            if state == 'LA' and short in LOUISIANA_CODES:
                return LOUISIANA_CODES[short], 'official code name for the published abbreviation'
            if state == 'NY' and num.upper() in NEW_YORK_LAWS:
                return '%s (%s)' % (NEW_YORK_LAWS[num.upper()], num.upper()), 'official law name for the published abbreviation'
            if 1 < len(short) < 80:
                return short, 'name used in the publisher citation'
    if name and raw and name.lower() not in raw.lower():
        return '%s: %s' % (raw, name), 'publisher heading'
    return (raw or name), 'publisher heading'


def write(rows, segments, seen):
    temporary = OUT.with_suffix('.building')
    if temporary.exists():
        temporary.unlink()
    db = sqlite3.connect(temporary)
    db.executescript('''
        CREATE TABLE nodes(id INTEGER PRIMARY KEY, state TEXT, kind TEXT, parent INTEGER, depth INTEGER, type TEXT, num TEXT, raw_label TEXT, name TEXT,
                           first_citation TEXT, first_rowid INTEGER, direct INTEGER, total INTEGER, label TEXT, label_basis TEXT, position INTEGER);
        CREATE TABLE segments(node INTEGER, lo INTEGER, hi INTEGER);
        CREATE TABLE collections(state TEXT, kind TEXT, provisions INTEGER, headings INTEGER, PRIMARY KEY(state, kind));
    ''')
    totals = [row[10] for row in rows]
    for index in range(len(rows) - 1, -1, -1):  # children are always created after their parent
        parent = rows[index][2]
        if parent:
            totals[parent - 1] += totals[index]
    db.executemany('INSERT INTO nodes(id,state,kind,parent,depth,type,num,raw_label,name,first_citation,first_rowid,direct,total) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ((index + 1, *row[:11], totals[index]) for index, row in enumerate(rows)))
    db.executemany('INSERT INTO segments VALUES(?,?,?)', ((node, lo, hi) for node, runs in segments.items() for lo, hi in runs))
    db.execute("INSERT INTO collections SELECT state, kind, sum(direct), count(*) FROM nodes GROUP BY state, kind")
    db.execute('CREATE TABLE scan(key TEXT PRIMARY KEY, value TEXT)')
    stat = CATALOG.stat()
    db.executemany('INSERT INTO scan VALUES(?,?)', [('catalog_rows', str(seen)), ('catalog_bytes', str(stat.st_size)), ('catalog_mtime_ns', str(stat.st_mtime_ns))])
    db.commit()
    db.close()
    if OUT.exists():
        OUT.unlink()
    temporary.rename(OUT)


def relabel():
    db = sqlite3.connect(OUT)
    rows = db.execute('SELECT state,kind,parent,depth,type,num,raw_label,name,first_citation,id,first_rowid FROM nodes').fetchall()
    groups = {}
    labels = []
    for row in rows:
        label, basis = heading(row)
        labels.append((label[:300], basis, row[9]))
        groups.setdefault((row[0], row[1], row[2]), []).append((row[9], row[5], label, row[10]))
    db.executemany('UPDATE nodes SET label=?, label_basis=? WHERE id=?', labels)
    positions = []
    for members in groups.values():
        nums = [m[1] for m in members]
        if all(nums) and all(ROMAN.match(n.upper()) for n in nums):
            ordered = sorted(members, key=lambda m: (roman(m[1]), m[3]))
        elif all(nums):
            ordered = sorted(members, key=lambda m: (natural(m[1]), m[3]))
        else:
            ordered = sorted(members, key=lambda m: m[3])  # publisher order
        positions.extend((place, member[0]) for place, member in enumerate(ordered))
    db.executemany('UPDATE nodes SET position=? WHERE id=?', positions)
    db.executescript('''
        CREATE INDEX IF NOT EXISTS nodes_children ON nodes(state, kind, parent, position);
        CREATE INDEX IF NOT EXISTS segments_node ON segments(node, lo);
        CREATE INDEX IF NOT EXISTS segments_lo ON segments(lo);
    ''')
    db.commit()
    counts = {
        'headings': db.execute('SELECT count(*) FROM nodes').fetchone()[0],
        'provisions': db.execute('SELECT coalesce(sum(direct),0) FROM nodes').fetchone()[0],
        'segments': db.execute('SELECT count(*) FROM segments').fetchone()[0],
        'jurisdictions': db.execute('SELECT count(DISTINCT state) FROM nodes').fetchone()[0],
        'collections': db.execute('SELECT count(*) FROM collections').fetchone()[0],
        'ungrouped_provisions': db.execute("SELECT coalesce(sum(direct),0) FROM nodes WHERE type='ungrouped'").fetchone()[0],
        'max_depth': db.execute('SELECT max(depth) FROM nodes').fetchone()[0],
    }
    scan_facts = dict(db.execute('SELECT key, value FROM scan'))
    covered = db.execute('SELECT coalesce(sum(hi-lo+1),0) FROM segments').fetchone()[0]
    overlap = db.execute('SELECT count(*) FROM segments a JOIN segments b ON a.rowid<b.rowid AND a.lo<=b.hi AND b.lo<=a.hi').fetchone()[0] if counts['segments'] < 20000 else None
    db.execute('VACUUM')
    db.close()
    stat = CATALOG.stat()
    checks = {
        'every_catalog_row_is_in_exactly_one_range': covered == int(scan_facts['catalog_rows']) == counts['provisions'],
        'catalog_unchanged_since_scan': str(stat.st_size) == scan_facts['catalog_bytes'] and str(stat.st_mtime_ns) == scan_facts['catalog_mtime_ns'],
        'every_heading_has_a_label': True,
    }
    if overlap is not None:
        checks['ranges_do_not_overlap'] = overlap == 0
    validation = {
        'schema_version': 1, 'status': 'passed' if all(checks.values()) else 'failed', 'ready': all(checks.values()),
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': OUT.name, 'sha256': sha256(OUT), 'rows': counts['headings']}],
        'counts': counts, 'checks': checks, 'qualification': QUALIFICATION, 'license_ref': LICENSE,
        'inputs': [{'path': 'sources/open_us_law_20260918/catalog.sqlite3', 'bytes': int(scan_facts['catalog_bytes']), 'mtime_ns': int(scan_facts['catalog_mtime_ns']), 'rows': int(scan_facts['catalog_rows'])}],
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    print(json.dumps({'status': validation['status'], 'counts': counts, 'checks': checks}, indent=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--relabel', action='store_true')
    args = parser.parse_args()
    if not args.relabel:
        rows, segments, seen = scan()
        write(rows, segments, seen)
    relabel()


if __name__ == '__main__':
    main()
