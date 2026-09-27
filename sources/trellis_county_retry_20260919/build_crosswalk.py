"""Offline reviewed crosswalk for the 11 county labels the coverage layer left without a FIPS. No network.

A row is resolved only when a documented, reviewed normalisation of the Trellis label equals exactly one county-inventory
name in the same state. Everything else stays unresolved with its reason; no FIPS is guessed.
"""
import hashlib, json, re, sys, unicodedata
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INVENTORY = ROOT / 'delivery/focused_legal_corpus/counties/counties.jsonl'
COVERAGE = ROOT / 'sources/trellis_coverage_20260919'

def readl(p): return [json.loads(x) for x in p.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def strip_marks(s): return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

# Reviewed by hand on 2026-09-19 against the inventory printout; each rule names the only transformation allowed for that label.
REVIEWED = {
    ('NM', 'Dona Ana County'): ('diacritic_fold', 'Trellis prints the name without the tilde; folding combining marks on both sides gives one New Mexico inventory row.'),
    ('VA', 'Roanoke County Circuit Courts Records'): ('strip_heading_suffix', 'The label is a page heading; removing the literal suffix " Circuit Courts Records" leaves "Roanoke County". '
        'Roanoke city (51770) is a separate independent city and is not this label.'),
}
NOT_RESOLVABLE = {
    'CT': 'Connecticut historical county. The inventory vintage lists nine planning regions as county-equivalents and none of the eight historical counties; '
          'the two geographies do not map one-to-one, so no inventory FIPS is assigned.',
    'DE': 'Court of Chancery is a statewide Delaware court venue, not a county; the inventory has Kent, New Castle and Sussex only.',
}

def main():
    inv = readl(INVENTORY); unresolved_in = readl(COVERAGE / 'unresolved.jsonl'); assert len(unresolved_in) == 11
    cov = [r for r in readl(COVERAGE / 'counties.jsonl')]
    resolved, unresolved = [], []
    for u in unresolved_in:
        state, label = u['state'], u['county']
        cov_rows = [r for r in cov if r.get('state') == state and r.get('county') == label]
        evidence = [{'coverage_record_id': r.get('id'), 'coverage_fips': r.get('fips')} for r in cov_rows]
        rule = REVIEWED.get((state, label))
        if rule:
            kind, why = rule
            wanted = strip_marks(re.sub(r' Circuit Courts Records$', '', label) if kind == 'strip_heading_suffix' else label).casefold()
            hits = [r for r in inv if r['usps'] == state and strip_marks(r['name']).casefold() == wanted]
            used = [r.get('id') for r in cov if hits and r.get('fips') == hits[0]['geoid']]
            if len(hits) == 1 and used:
                why = 'inventory match is unique but that FIPS is already carried by coverage rows ' + ', '.join(used) + '; owner must decide whether to merge'
            elif len(hits) == 1:
                h = hits[0]
                resolved.append({'state': state, 'trellis_label': label, 'census_county_name': h['name'], 'fips': h['geoid'], 'normalisation': kind, 'basis': why,
                                 'inventory_candidates_in_state_after_normalisation': 1, 'coverage_rows_already_carrying_this_fips': 0,
                                 'publisher_fields_seen_on_coverage_row': {k: (cov_rows[0].get('publisher_reported') or {}).get(k) for k in ('seat', 'website')} if cov_rows else None,
'geography_vintage': h.get('geography_vintage'),
                                 'reviewed_at': '2026-09-19', 'review': 'agent-reviewed against the inventory; owner should confirm before publishing', 'coverage_rows': evidence})
                continue
            else:
                why = f'reviewed rule produced {len(hits)} inventory rows'
        else:
            why = NOT_RESOLVABLE.get(state, 'No reviewed unambiguous rule.')
        unresolved.append({'state': state, 'trellis_label': label, 'census_county_name': None, 'fips': None, 'reason': why,
                           'inventory_names_in_state': sorted(r['name'] for r in inv if r['usps'] == state) if state in ('CT', 'DE') else None, 'coverage_rows': evidence})
    out = {'generated_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
           'scope': 'The 11 labels in sources/trellis_coverage_20260919/unresolved.jsonl; nothing else.',
           'rule': 'Resolved only when the reviewed normalisation yields exactly one inventory county in the same state. FIPS are 5-character strings from the inventory.',
           'inputs': [{'path': 'delivery/focused_legal_corpus/counties/counties.jsonl', 'sha256': sha(INVENTORY), 'rows': len(inv)},
                      {'path': 'sources/trellis_coverage_20260919/unresolved.jsonl', 'sha256': sha(COVERAGE / 'unresolved.jsonl'), 'rows': 11}],
           'resolved': resolved, 'unresolved': unresolved}
    (HERE / 'county_name_crosswalk.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'resolved': [(r['state'], r['trellis_label'], r['census_county_name'], r['fips']) for r in resolved], 'unresolved': len(unresolved),
                      'coverage_rows_found': sum(len(r['coverage_rows']) for r in resolved + unresolved)}, ensure_ascii=False))

if __name__ == '__main__': main()
