"""Reproducible library inventory, not invented judicial outcome analytics."""
import datetime as dt
from urllib.parse import urlsplit, urlunsplit
from evidence_dates import latest_saved


def source_identity(value):
    if not isinstance(value, str): return None
    try:
        p = urlsplit(value)
        if p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password: return None
        return urlunsplit((p.scheme, p.netloc.lower(), p.path, p.query, ''))
    except ValueError: return None


def period_label(value):
    if isinstance(value, str): return value.strip()
    if isinstance(value, (int, float)): return str(value)
    if isinstance(value, dict):
        return '; '.join(str(key).replace('_', ' ').capitalize() + ': ' + str(item)
                         for key, item in value.items() if item is not None and not isinstance(item, (list, dict)))
    return ''


def derive(profile, computed_at=None):
    sources = profile.get('sources') or []
    source_urls = sorted({key for row in sources if (key := source_identity(row.get('url')))})
    analyses = profile.get('analyses') or []
    # Do not deduplicate measures by value/name; separate source observations and
    # reporting periods may legitimately contain the same number.
    scope = {'records': len(analyses),
             'publishers': sorted({row['publisher'] for row in analyses if row.get('publisher')}),
             'periods': sorted({label for row in analyses if (label := period_label(row.get('period')))}),
             'with_reporting_period': sum(bool(period_label(row.get('period'))) for row in analyses),
             'with_sample_size': sum(isinstance(row.get('sample_size'), (int, float))
                                     and not isinstance(row.get('sample_size'), bool)
                                     and row['sample_size'] > 0 for row in analyses)}
    components = [('Biography', bool(profile.get('biography'))),
                  ('Education', bool(profile.get('education'))),
                  ('Career history', any(profile.get(key) for key in ('appointments', 'service', 'professional_career'))),
                  ('Portrait', bool(profile.get('photo_url'))),
                  ('Published analysis', bool(analyses))]
    available = [label for label, present in components if present]
    gaps = [label + ' is not saved for this profile.' for label, present in components if not present]
    if not profile.get('current_service_verified'):
        gaps.append('Present-day judicial service is not verified.')
    if analyses and scope['with_reporting_period'] < len(analyses):
        gaps.append(str(len(analyses) - scope['with_reporting_period']) + ' analysis records have no stated reporting period.')
    summary = ('The saved profile includes ' + ', '.join(label.lower() for label in available) + '.'
               if available else 'This is a directory entry with limited saved professional detail.')
    return {'kind': 'derived_library_inventory', 'label': 'Library insights',
            'computed_at': computed_at or dt.datetime.now(dt.timezone.utc).isoformat(),
            'summary': summary,
            'metrics': [{'label': 'Distinct source pages', 'value': len(source_urls)},
                        {'label': 'Saved court affiliations', 'value': len(set(profile.get('courts') or []))},
                        {'label': 'Published analysis records', 'value': len(analyses)}],
            'available_sections': available, 'gaps': gaps, 'analysis_scope': scope,
            'methodology': 'Deterministic counts of the saved profile and its source URLs (URL fragments removed). '
                           'Analysis records are source measures, not unique cases. No performance score, win rate '
                           'or prediction is calculated, and affiliations do not imply simultaneous current service.',
            'source_urls': source_urls}


def decorate(profile, computed_at=None):
    result = dict(profile)
    result['saved_at'] = latest_saved([row.get('captured_at') for row in result.get('sources') or []])
    result.setdefault('source_as_of', None)
    result['date_note'] = 'Latest recorded collection date; it does not verify current office or the source’s as-of date.'
    result['library_insights'] = derive(result, computed_at)
    return result
