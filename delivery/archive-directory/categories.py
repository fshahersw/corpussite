"""Small, explicit presentation taxonomy over retained source classifications."""
LABELS = {
    'statutes': 'Statutes & codes', 'rules': 'Rules & orders',
    'constitutions': 'Constitutions', 'regulations': 'Regulations',
    'forms': 'Forms & documents', 'guidance': 'Guides & references',
    'directories': 'Courts & directories', 'other': 'Other saved resources',
}


def classify(kind):
    value = (kind or '').lower()
    if 'constitution' in value:
        return 'constitutions'
    if 'regulat' in value or value in {'administrative_code'}:
        return 'regulations'
    if 'rule' in value or value in {'federal_order_document_link', 'executive_order'}:
        return 'rules'
    if value in {'statutes', 'statutory_provision', 'law_chapter_body', 'local_laws_codes',
                 'county_ordinance_text', 'municipal_code_of_ordinances_pdf', 'unsigned_county_ordinance_text'}:
        return 'statutes'
    if value in {'court_form_or_other_document', 'court_forms_filing_documents', 'judicial_records_request_form', 'document'}:
        return 'forms'
    if value in {'guidance', 'administrative_guidance', 'guideline', 'faq', 'memorandum',
                 'irs_announcement', 'irs_notice', 'irs_rev_proc', 'irs_rev_rul',
                 'reference_original', 'federal_legal_reference_resource',
                 'doj_manual_resource', 'federal_court_practice_or_case_resource'}:
        return 'guidance'
    if value in {'legal_inventory_navigation', 'court_clerk_office'} or any(part in value for part in ['directory', 'website', 'portal', 'roster', 'coverage_county']):
        return 'directories'
    return 'other'


def options(kinds):
    present = {classify(k) for k in kinds}
    return [{'id': key, 'label': label} for key, label in LABELS.items() if key in present]


def condition(category, kinds):
    if not category:
        return '', []
    selected = [kind for kind in kinds if classify(kind) == category] if category in LABELS else []
    return ('kind IN (' + ','.join('?' for _ in selected) + ')', selected) if selected else ('0', [])
