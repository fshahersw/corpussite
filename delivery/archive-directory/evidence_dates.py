"""Explicit date semantics for saved evidence; never infer currency from filenames."""
import datetime as dt
import re


def date_value(value):
    if not isinstance(value, str): return None
    value = value.strip()
    try:
        if re.fullmatch(r'\d{4}', value):
            return value if 1 <= int(value) <= 9999 else None
        if re.fullmatch(r'\d{4}-\d{2}', value):
            dt.date.fromisoformat(value + '-01'); return value
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            dt.date.fromisoformat(value); return value
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T[^\s]+', value):
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.isoformat()
    except (ValueError, OverflowError): return None
    return None


def date_values(value):
    if isinstance(value, list):
        return [item for child in value for item in date_values(child)]
    clean = date_value(value)
    return [clean] if clean else []


def latest_saved(value):
    # Capture timestamps must have a full calendar day; year-only values do not
    # establish when a copy was saved. Retain the source's precision otherwise.
    values = [item for item in date_values(value) if len(item) >= 10]
    def order(item):
        parsed = dt.datetime.fromisoformat(item)
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    return max(values, key=order) if values else None


def document_dates(payload):
    payload = payload if isinstance(payload, dict) else {}
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    def explicit(keys):
        for source in (payload, metadata):
            for key in keys:
                parsed = date_value(source.get(key))
                if parsed: return parsed, key
        return None, None
    source_as_of, basis = explicit(('source_as_of', 'as_of_date', 'as_of', 'source_date', 'snapshot_date'))
    effective_date, effective_basis = explicit(('effective_date',))
    # A saved date needs explicit collection evidence: captured_at first, then the collector's
    # retrieved_at. Index/verification times and HTTP Last-Modified are never used.
    saved_at, saved_field = latest_saved([payload.get('captured_at'), metadata.get('captured_at')]), 'captured_at'
    if not saved_at:
        saved_at, saved_field = latest_saved([payload.get('retrieved_at'), metadata.get('retrieved_at')]), 'retrieved_at'
    saved_basis = None
    if saved_at and saved_field == 'retrieved_at':
        recorded = payload.get('retrieval_time_basis') or metadata.get('retrieval_time_basis')
        saved_basis = recorded if isinstance(recorded, str) and recorded else None
    return {'source_as_of': source_as_of, 'saved_at': saved_at,
            'effective_date': effective_date,
            'date_evidence': {'source_as_of_field': basis, 'effective_date_field': effective_basis,
                              'saved_at_field': saved_field if saved_at else None, 'saved_at_basis': saved_basis},
            'date_note': 'Saved date records collection, not legal effect. Unknown source dates remain unknown.'}
