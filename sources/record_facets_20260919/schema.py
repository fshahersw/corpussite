"""Schema of facets.sqlite3 (shared by build.py and the adapter tests).

One row per directory record id. No file paths are stored; every derived value has a basis column.
"""
SCHEMA_VERSION = '1'

COLUMNS = [
    ('record_id', 'TEXT PRIMARY KEY'),
    ('dataset', 'TEXT'), ('group_name', 'TEXT'),
    ('original_kind', 'TEXT'), ('base_kind', 'TEXT'), ('kind_flags', 'TEXT'),
    ('derived_category', 'TEXT'), ('categories', 'TEXT'), ('category_basis', 'TEXT'),
    ('review_state', 'TEXT'), ('review_basis', 'TEXT'),
    ('doc_subtype', 'TEXT'), ('subtype_basis', 'TEXT'),
    ('record_type', 'TEXT'), ('record_type_basis', 'TEXT'),
    ('representation', 'TEXT'), ('representation_basis', 'TEXT'),
    ('file_type', 'TEXT'), ('file_type_basis', 'TEXT'),
    ('has_original', 'INTEGER'), ('has_text', 'INTEGER'), ('text_chars', 'INTEGER'),
    ('bytes', 'INTEGER'), ('bytes_basis', 'TEXT'), ('original_sha256', 'TEXT'),
    ('jurisdiction_level', 'TEXT'), ('jurisdiction_basis', 'TEXT'),
    ('state', 'TEXT'), ('states', 'TEXT'), ('multi_state', 'INTEGER'), ('court_label_as_published', 'TEXT'),
    ('saved_at', 'TEXT'), ('saved_at_field', 'TEXT'), ('saved_at_basis', 'TEXT'),
    ('saved_lo', 'TEXT'), ('saved_hi', 'TEXT'),
    ('captured_at', 'TEXT'), ('captured_at_basis', 'TEXT'),
    ('source_as_of', 'TEXT'), ('source_as_of_basis', 'TEXT'), ('source_lo', 'TEXT'), ('source_hi', 'TEXT'),
    ('published_at', 'TEXT'), ('published_at_basis', 'TEXT'), ('published_lo', 'TEXT'), ('published_hi', 'TEXT'),
    ('effective_from', 'TEXT'), ('effective_from_basis', 'TEXT'), ('effective_lo', 'TEXT'), ('effective_hi', 'TEXT'),
    ('effective_to', 'TEXT'), ('effective_to_basis', 'TEXT'),
    ('publisher_status', 'TEXT'), ('publisher_status_basis', 'TEXT'),
    ('title', 'TEXT'), ('display_title', 'TEXT'), ('title_basis', 'TEXT'), ('title_issue', 'TEXT'), ('title_evidence', 'TEXT'),
    ('validity', 'TEXT'), ('validity_reason', 'TEXT'), ('capture_flags', 'TEXT'),
    ('label_source', 'TEXT'), ('court_id', 'TEXT'), ('label_county_geoid', 'TEXT'), ('law_body_class', 'TEXT'),
    ('rule_set', 'TEXT'), ('topics', 'TEXT'), ('label_doc_subtype', 'TEXT'),
]
COLUMN_NAMES = [name for name, _ in COLUMNS]

# Date filters map a date_type to the pair of comparable YYYY-MM-DD keys (overlap semantics for partial precision).
DATE_TYPES = {
    'saved': ('saved_lo', 'saved_hi', 'saved_at'),
    'captured': ('saved_lo', 'saved_hi', 'captured_at'),
    'source_as_of': ('source_lo', 'source_hi', 'source_as_of'),
    'published': ('published_lo', 'published_hi', 'published_at'),
    'effective': ('effective_lo', 'effective_hi', 'effective_from'),
}

INDEXES = [
    ('facets_category', 'facets(derived_category)'),
    ('facets_review', 'facets(review_state)'),
    ('facets_rtype', 'facets(record_type)'),
    ('facets_ftype', 'facets(file_type)'),
    ('facets_jur', 'facets(jurisdiction_level, state)'),
    ('facets_state', 'facets(state)'),
    ('facets_validity', 'facets(validity)'),
    ('facets_saved', 'facets(saved_lo)'),
    ('facets_source', 'facets(source_lo)'),
    ('facets_dataset', 'facets(dataset)'),
    ('facets_subtype', 'facets(doc_subtype)'),
    ('facets_repr', 'facets(representation)'),
    ('facet_categories_category', 'facet_categories(category, record_id)'),
    ('wrong_joins_geoid', 'wrong_county_joins(geoid)'),
]


def ddl():
    cols = ',\n  '.join(f'{name} {kind}' for name, kind in COLUMNS)
    statements = [
        f'CREATE TABLE facets(\n  {cols}\n)',
        'CREATE TABLE facet_categories(record_id TEXT, category TEXT, PRIMARY KEY(record_id, category))',
        'CREATE TABLE wrong_county_joins(record_id TEXT, geoid TEXT, reason TEXT, original_sha256 TEXT, PRIMARY KEY(record_id, geoid))',
        'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)',
    ]
    statements += [f'CREATE INDEX {name} ON {target}' for name, target in INDEXES]
    return ';\n'.join(statements) + ';'


def insert_sql():
    return 'INSERT INTO facets(%s) VALUES(%s)' % (','.join(COLUMN_NAMES), ','.join('?' for _ in COLUMN_NAMES))
