"""Read-only public-law source catalogue; separate from saved-content counts."""
import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / 'sources/public_law_directory_20260919'
# Display-only labels from the same registry's standalone litigation browser.
# IDs, categories, counts and original records remain unchanged. Provenance:
# reports/litigation_source_review_20260919/display_labels.json (source hash recorded there).
CATEGORY_LABELS = {
    'api': 'API', 'attorney_admission_discipline': 'Attorney admission & discipline',
    'corporate': 'Corporate records', 'efiling_cmecf': 'E-filing / CM-ECF',
    'ethics_professional_resp': 'Ethics & professional responsibility',
    'facility_provider_data': 'Facility & provider data', 'licensing_verification': 'License verification',
    'lien_msp': 'Liens & MSP', 'opinions_decisions': 'Opinions & decisions',
    'public_records_foia': 'Public records & FOIA', 'recall_safety_data': 'Recalls & safety data',
    'regulations_register': 'Regulations & register', 'self_help_pro_se': 'Self-help / pro se',
    'statutes_codes': 'Statutes & codes',
}
# Reference-type facets use the source's own tags. Labels come from the same registry's
# standalone browser (display_labels.json); values and counts are never reassigned.
LAYER_LABELS = {
    'agency_enforcement': 'Agency enforcement', 'api_bulk': 'APIs & bulk data', 'background': 'Background & vetting',
    'bankruptcy': 'Bankruptcy', 'case_law': 'Case law', 'civil_liability': 'Civil liability', 'corporate': 'Corporate',
    'court_practice': 'Court practice', 'cyber': 'Cyber & breach', 'damages_data': 'Damages data', 'discovery': 'Discovery',
    'enforcement': 'Enforcement', 'executive': 'Executive', 'facility': 'Facilities', 'insurance': 'Insurance',
    'intl_service': 'International service', 'lien_settlement': 'Liens & settlement', 'mdl_practice': 'MDL practice',
    'other': 'Other', 'professional_license': 'Professional licensing', 'public_records': 'Public records',
    'regulation': 'Regulation', 'safety_data': 'Safety data', 'sci_evidence': 'Scientific evidence',
    'secondary': 'Secondary sources', 'state_resource': 'State resources', 'statute': 'Statutes',
    'transport_evidence': 'Transport evidence', 'trial_authority': 'Trial authority', 'tribunal_topical': 'Topical tribunals',
}
TASK_FAMILY_LABELS = {
    'courts-procedure': 'Courts & procedure', 'law-authority': 'Law & authority',
    'regulatory-administrative': 'Regulatory & administrative', 'discovery-trial': 'Discovery & trial',
    'complex-litigation': 'Complex litigation / MDL', 'settlement-recovery': 'Settlement & recovery',
    'people-professional-records': 'People & professional records', 'organizations-assets': 'Organizations & assets',
    'evidence-acquisition': 'Evidence acquisition', 'science-evidence': 'Scientific evidence', 'insurance': 'Insurance',
    'healthcare-facilities': 'Healthcare facilities', 'data-apis': 'Data & APIs',
}
CONTENT_KIND_LABELS = {'page': 'Web page', 'pdf': 'PDF', 'docx': 'Word', 'xlsx': 'Excel', 'csv': 'CSV', 'json': 'JSON',
                       'xml': 'XML', 'zip': 'ZIP archive', 'api': 'API'}
SOURCE_TYPE_LABELS = {'official': 'Official', 'nonprofit_or_assoc': 'Nonprofit or association',
                      'commercial_or_thirdparty': 'Commercial or third party', 'institutional': 'Institutional'}
ACCESS_REQUIREMENT_LABELS = {'open': 'Open', 'registration': 'Registration listed', 'fee': 'Fee listed', 'dua': 'Data use agreement listed'}
# facet name -> (record field, labels). 'unspecified' selects records the source left untagged.
TYPE_FACETS = {'layers': ('layer', LAYER_LABELS), 'task_families': ('task_family', TASK_FAMILY_LABELS),
               'content_kinds': ('content_kind', CONTENT_KIND_LABELS), 'source_types': ('source_type', SOURCE_TYPE_LABELS),
               'access_requirements': ('access_requirements', ACCESS_REQUIREMENT_LABELS)}
UNSPECIFIED = 'unspecified'
PUBLIC = ('id','title','url','host','jurisdiction','jurisdiction_label','category','tags','section',
          'access_method','access_requirements','verification_status','source_as_of','notes','has_saved_content',
          'content_kind','source_type','public_url_candidate','original_category','task_family','layer','is_api_bulk_reference','subsection')


@lru_cache(maxsize=2)
def _decode(digest, payload):
    data = json.loads(payload)
    for option in data['facets']['categories']:
        option['label'] = CATEGORY_LABELS.get(option['value'], option['label'])
    for facet, (field, labels) in TYPE_FACETS.items():
        counts = {}
        for row in data['entries']:
            value = row.get(field) or UNSPECIFIED
            counts[value] = counts.get(value, 0) + 1
        data['facets'][facet] = sorted(
            ({'value': value, 'label': 'Not tagged in source' if value == UNSPECIFIED else labels.get(value, value.replace('_', ' ').replace('-', ' ').capitalize()), 'count': count}
             for value, count in counts.items()), key=lambda option: (-option['count'], option['label']))
    data['_by_id'] = {r['id']: r for r in data['entries']}
    data['_search'] = {r['id']: ' '.join(str(r.get(k) or '') for k in ('title','url','host','jurisdiction_label','category','tags','section','subsection','description','notes')).casefold() for r in data['entries']}
    return data


def _load(folder=DATA):
    gate = json.loads((folder / 'validation.json').read_text(encoding='utf-8'))
    payload = (folder / 'catalog.json').read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if gate.get('status') != 'passed' or gate.get('ready') is not True or digest != gate.get('catalog_sha256'):
        raise ValueError('Source directory validation failed')
    return _decode(digest, payload)


def _param(params, key, default=''):
    value = params.get(key, default)
    if isinstance(value, list): value = value[0] if value else default
    return str(value or default)


def _number(params, key, default, maximum):
    try: return min(maximum, max(1, int(_param(params, key, str(default)))))
    except (ValueError, TypeError): return default


def listing(params=None, saved_urls=None, archive_counts=None):
    """Attach only caller-validated exact source URLs; no acquisition data is read here.

    saved_urls: exact URLs with a validated capture attached (Saved content attached).
    archive_counts: exact URL -> count of validated links to records already saved elsewhere
    in this archive (Saved in archive). The two availabilities are reported separately.
    """
    params = params or {}
    saved_urls = set(saved_urls or ())
    if isinstance(archive_counts, dict):
        archive_counts = {k: int(v) for k, v in archive_counts.items() if isinstance(k, str) and isinstance(v, int) and v > 0}
    else:
        archive_counts = {k: 1 for k in (archive_counts or ()) if isinstance(k, str)}
    page, limit = _number(params,'page',1,100000), _number(params,'limit',30,100)
    try: data = _load()
    except (OSError, ValueError, KeyError, TypeError):
        return {'ready': False, 'total': 0, 'items': [], 'page': page, 'limit': limit,
                'facets': {'jurisdictions': [], 'categories': [], 'access_methods': [], 'statuses': [], 'availability': []}, 'summary': {}}
    terms = _param(params,'q').casefold().split()
    filters = {k: _param(params,k) for k in ('jurisdiction','category','access_method','verification_status')}
    if filters['jurisdiction']:
        requested = filters['jurisdiction'].casefold()
        for option in data['facets']['jurisdictions']:
            if requested in (option['value'].casefold(), option['label'].casefold()):
                filters['jurisdiction'] = option['value']
                break
    type_filters = {field: _param(params, field) for field, _ in TYPE_FACETS.values()}
    def typed(r): return all(not v or (r.get(k) or UNSPECIFIED) == v for k, v in type_filters.items())
    api_bulk = _param(params, 'api_bulk') == '1'
    availability = _param(params, 'has')
    def archived(r): return archive_counts.get(r['url'], 0) > 0
    result = [r for r in data['entries'] if (not api_bulk or r.get('is_api_bulk_reference') is True)
              and (availability != 'saved' or r['url'] in saved_urls)
              and (availability != 'archived' or archived(r))
              and (availability != 'links_only' or (r['url'] not in saved_urls and not archived(r)))
              and all(not v or r.get(k) == v for k,v in filters.items()) and typed(r)
              and all(t in data['_search'][r['id']] for t in terms)]
    # Most useful first: references with saved content, then ones already saved in the archive, then official publishers;
    # the registry's own order is kept inside each band (stable sort), so nothing is ranked by opinion.
    if _param(params, 'order') != 'registry':
        result.sort(key=lambda r: (r['url'] not in saved_urls, not archived(r), r.get('source_type') != 'official'))
    saved_count = sum(r['url'] in saved_urls for r in data['entries'])
    archived_count = sum(archived(r) for r in data['entries'])
    links_only_count = sum(r['url'] not in saved_urls and not archived(r) for r in data['entries'])
    facets = copy.deepcopy(data['facets'])
    facets['availability'] = [
        {'value': 'saved', 'label': 'Saved content attached', 'count': saved_count},
        {'value': 'archived', 'label': 'Saved in archive', 'count': archived_count},
        {'value': 'links_only', 'label': 'Source link only', 'count': links_only_count}]
    summary = copy.deepcopy(data['summary'])
    summary['saved_content_records'] = saved_count
    summary['archive_linked_records'] = archived_count
    summary['qualification'] = ('Directory references retain historical draft verification and access claims. '
                                'Saved-content counts include only validated linked captures with an exact source URL match; '
                                'Saved in archive counts validated links to records already saved elsewhere in this archive, also by exact URL. '
                                'Entries without either may have content elsewhere in the corpus; no full-corpus absence claim is made.')
    offset = (page-1)*limit
    return {'ready': True, 'total': len(result),
            'items': [{**{k:r.get(k) for k in PUBLIC}, 'has_saved_content': r['url'] in saved_urls,
                       'has_archive_records': archived(r), 'archive_record_count': archive_counts.get(r['url'], 0)} for r in result[offset:offset+limit]],
            'page': page, 'limit': limit, 'facets': facets, 'summary': summary}


def detail(record_id):
    if not isinstance(record_id,str): return None
    try: result = _load()['_by_id'].get(record_id)
    except (OSError,ValueError,KeyError,TypeError): return None
    return {'ready': True, **copy.deepcopy(result)} if result else None
