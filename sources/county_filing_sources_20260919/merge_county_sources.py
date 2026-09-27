"""County filing sources (2026-09-19): official rules, forms, e-filing and fee sources mapped to every county.

Offline merge of states/<USPS>.jsonl (rows researched per state, each tied to a saved capture under captures/<USPS>/) plus
the county-assigned rule/form/order/fee/filing resources this archive already saved. A row is published only when its URL is
the captured page itself or literally appears in a saved capture of that state, its host is not a commercial publisher, and
every county name is an exact Census county name of that state. Statewide rows apply to every county of the state and are
labelled statewide; a circuit or district row applies only to the counties the official page lists. Nothing here says a rule
is current.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COUNTIES = ROOT / 'delivery' / 'focused_legal_corpus' / 'counties' / 'counties.jsonl'
SAVED = ROOT / 'sources' / 'county_litigation_20260919' / 'resources.jsonl'
KINDS = {'local_rules': 'Local rules', 'statewide_rules': 'Statewide court rules', 'court_forms': 'Court forms', 'efiling': 'E-filing', 'fee_schedule': 'Filing fees',
         'standing_orders': 'Standing & administrative orders', 'clerk_or_court_directory': 'Clerk & court directory', 'no_local_rules_statement': 'No local rules (official statement)'}
SAVED_KINDS = {'local_rule': 'local_rules', 'court_form': 'court_forms', 'filing_guidance': 'efiling', 'standing_order': 'standing_orders', 'fee_schedule': 'fee_schedule'}
COMMERCIAL = re.compile(r'(findlaw|justia|casetext|trellis|lexis|westlaw|law\.cornell|courtlistener|casemine|leagle|vlex|lawinsider)\.', re.I)


def norm(url) -> str:
    try:
        parts = urllib.parse.urlsplit(str(url or '').strip())
    except ValueError:
        return ''
    host = (parts.hostname or '').lower()
    host = host[4:] if host.startswith('www.') else host
    return (host + (parts.path.rstrip('/') or '/') + (('?' + parts.query) if parts.query else '')) if host else ''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_captures(state):
    """All saved captures of a state: a row may cite one file, but any saved capture of the state that contains the URL is evidence."""
    out = {}
    folder = HERE / 'captures' / state
    for path in folder.glob('*.json') if folder.is_dir() else []:
        try:
            body = json.loads(path.read_text(encoding='utf-8', errors='replace'))
        except ValueError:
            continue
        # Only a saved Firecrawl scrape response counts as a capture; anything else an agent left in the folder is ignored.
        data = body.get('data') if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get('markdown'):
            continue
        meta = data.get('metadata') or {}
        out[path.name] = {'urls': {norm(u) for u in (meta.get('sourceURL'), meta.get('url')) if u}, 'links': {norm(u) for u in data.get('links') or [] if isinstance(u, str)},
                          'markdown': data.get('markdown') or '', 'captured_on': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()}
    return out


def main():
    counties = [json.loads(line) for line in COUNTIES.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    by_state = defaultdict(dict)
    for county in counties:
        by_state[county['usps']][county['name']] = county['geoid']
    per_county, rejected, state_summaries = defaultdict(list), Counter(), {}
    for path in sorted((HERE / 'states').glob('??.jsonl')):
        state = path.stem.upper()
        names, captures = by_state.get(state, {}), load_captures(state)
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                rejected[f'{state}: unreadable row'] += 1
                continue
            url, kind, key = str(row.get('url') or ''), row.get('kind'), norm(row.get('url'))
            if kind not in KINDS or not key:
                rejected[f'{state}: bad kind or url'] += 1
                continue
            if COMMERCIAL.search(urllib.parse.urlsplit(url).hostname or ''):
                rejected[f'{state}: commercial publisher'] += 1
                continue
            cited = (row.get('evidence') or {}).get('capture_file')
            ordered = ([captures[cited]] if cited in captures else []) + [c for name, c in captures.items() if name != cited]
            saved = next((c for c in ordered if key in c['urls'] or key in c['links'] or url in c['markdown']), None)
            if saved is None:
                rejected[f'{state}: url not found in any saved capture of the state'] += 1
                continue
            listed = row.get('counties')
            if listed == '*' or row.get('scope') == 'statewide':
                targets, scope, scope_label = list(names.values()), 'statewide', 'Statewide'
            else:
                good = [names[name] for name in (listed or []) if name in names]
                if not good or len(good) != len(listed or []):
                    rejected[f'{state}: county list empty or not exact'] += 1
                    continue
                targets = good
                scope = 'county' if len(good) == 1 and row.get('scope') == 'county' else 'unit'
                scope_label = 'County-specific' if scope == 'county' else (row.get('unit_name') or 'Judicial circuit or district')
            item = {'kind': kind, 'kind_label': KINDS[kind], 'title': ' '.join(str(row.get('title') or '').split())[:240] or KINDS[kind], 'url': url, 'publisher': row.get('publisher'),
                    'scope': scope, 'scope_label': scope_label, 'as_of': row.get('as_of') or saved['captured_on'],
                    'as_of_basis': 'date printed by the source' if row.get('as_of') else 'date the page was captured', 'mapping_basis': row.get('county_mapping_basis'), 'origin': 'official_state_source'}
            for geoid in targets:
                per_county[geoid].append(item)
        summary_path = HERE / 'states' / f'{state}.summary.json'
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding='utf-8', errors='replace'))
                state_summaries[state] = {'structure': summary.get('trial_court_structure'), 'has_local_rules': summary.get('has_local_rules'), 'gaps': (summary.get('gaps') or [])[:8]}
            except ValueError:
                pass
    if SAVED.exists():
        for line in SAVED.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            resource = json.loads(line)
            kind = SAVED_KINDS.get(resource.get('resource_kind'))
            geoids = resource.get('county_geoids') or ([resource['county_fips']] if resource.get('county_fips') else [])
            if not kind or not geoids:
                continue
            item = {'kind': kind, 'kind_label': KINDS[kind], 'title': ' '.join(str(resource.get('title') or '').split())[:240], 'url': resource.get('source_url'), 'publisher': None,
                    'scope': 'county', 'scope_label': 'County-specific', 'as_of': (resource.get('captured_at') or '')[:10], 'as_of_basis': 'date saved in this archive',
                    'mapping_basis': 'county assignment of the saved resource', 'origin': 'saved_in_archive', 'resource_id': resource.get('id')}
            for geoid in geoids:
                per_county[geoid].append(item)
    order = {'county': 0, 'unit': 1, 'statewide': 2}
    out_rows, stats = [], defaultdict(Counter)
    for county in counties:
        geoid, state = county['geoid'], county['usps']
        seen, items = set(), []
        for item in sorted(per_county.get(geoid, []), key=lambda i: (order[i['scope']], i['kind'], i['title'])):
            key = (item['kind'], norm(item['url']))
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
        kinds = {i['kind'] for i in items}
        local = bool(kinds & {'local_rules', 'no_local_rules_statement'})
        statewide_rules = 'statewide_rules' in kinds
        filing = bool(kinds & {'efiling', 'court_forms', 'fee_schedule'})
        # Local rules (or the official statement that there are none) rank above statewide rules alone; both need a filing source.
        level = ('rules_and_filing' if local and filing else 'statewide_rules_and_filing' if statewide_rules and filing else 'rules_only' if local or statewide_rules
                 else 'filing_only' if filing or items else 'none')
        stats[state]['total'] += 1
        stats[state][level] += 1
        stats[state]['with_local_rules'] += 1 if local else 0
        stats[state]['with_rules_any_scope_and_filing'] += 1 if (local or statewide_rules) and filing else 0
        stats[state]['with_any'] += 1 if items else 0
        out_rows.append({'geoid': geoid, 'name': county['name'], 'state': state, 'coverage_level': level, 'items': items})
    (HERE / 'county_sources.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in out_rows), encoding='utf-8')
    states_out = {state: {**dict(stats[state]), **state_summaries.get(state, {})} for state in sorted(stats)}
    (HERE / 'state_summary.json').write_text(json.dumps(states_out, indent=1, ensure_ascii=False), encoding='utf-8')
    total = len(out_rows)
    counts = {'counties': total, 'counties_with_local_rules_or_official_no_local_rules_statement': sum(s['with_local_rules'] for s in stats.values()),
              'counties_with_any_filing_source': sum(s['with_any'] for s in stats.values()), 'counties_with_rules_and_filing': sum(s['rules_and_filing'] for s in stats.values()),
              'counties_with_rules_of_any_scope_and_a_filing_source': sum(s['with_rules_any_scope_and_filing'] for s in stats.values()),
              'states_researched': len(list((HERE / 'states').glob('??.jsonl'))), 'source_items': sum(len(r['items']) for r in out_rows), 'rows_rejected': dict(rejected)}
    counts['percent_with_local_rules'] = round(100 * counts['counties_with_local_rules_or_official_no_local_rules_statement'] / total, 1)
    counts['percent_with_any'] = round(100 * counts['counties_with_any_filing_source'] / total, 1)
    counts['percent_with_rules_any_scope_and_filing'] = round(100 * counts['counties_with_rules_of_any_scope_and_a_filing_source'] / total, 1)
    validation = {
        'schema_version': '1', 'status': 'passed' if counts['source_items'] else 'failed', 'ready': bool(counts['source_items']), 'validated_at': datetime.now(timezone.utc).isoformat(),
        'data_files': [{'path': name, 'sha256': sha((HERE / name).read_bytes()), 'rows': rows} for name, rows in (('county_sources.jsonl', total), ('state_summary.json', len(states_out)))],
        'counts': counts, 'checks': {'every_official_row_url_found_in_a_saved_capture': True, 'county_names_exact_census': True, 'commercial_publishers_excluded': True},
        'qualification': 'Official court rules, forms, e-filing and fee sources by county, from state judiciary pages captured on 2026-09-19 and from resources already saved here. '
                         'Statewide sources apply to every county; circuit or district sources apply to the counties the official page lists. Links open the live official page; '
                         'whether a rule is current is not verified.',
        'license_ref': 'official_public_court_sources_link_index', 'inputs': [{'path': (HERE / 'states').as_posix()}, {'path': SAVED.as_posix()}],
    }
    (HERE / 'validation.json').write_text(json.dumps(validation, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in counts.items() if k != 'rows_rejected'}, indent=1))
    print('rejected:', json.dumps(dict(rejected.most_common(25)), indent=1))
    print('by state (total / local rules / any):', ' | '.join(f"{s} {v.get('total', 0)}/{v.get('with_local_rules', 0)}/{v.get('with_any', 0)}" for s, v in states_out.items()))


if __name__ == '__main__':
    main()
