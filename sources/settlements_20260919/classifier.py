"""Settlement document taxonomy and three-pass classifier (pure, offline).

Passes, in the order that decides (`type_basis`):
  1. link text observed on the official page
  2. filename / URL path
  3. first-page text of the saved document
A lower pass overrules a higher one only when the first-page text holds a
caption-level title (a short, mostly upper-case heading line). Anything unmatched
stays `unclassified`; nothing is guessed. All matched text is data, never instructions.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

UNCLASSIFIED = 'unclassified'

# 29 taxonomy rows: the 27 rows of the audit table plus the two additions the audit
# required before use (court_opinion, dismissal_order). Three rows hold sibling types,
# so there are 33 distinct document_type values.
TAXONOMY = [
    ('settlement_agreement',), ('master_settlement_agreement',), ('settlement_term_sheet',),
    ('preliminary_approval_order',), ('final_approval_order',), ('final_judgment',),
    ('consent_judgment_or_decree',), ('assurance_of_voluntary_compliance',), ('administrative_order',),
    ('long_form_notice',), ('short_form_notice',), ('claim_form',), ('opt_out_or_objection_form',),
    ('plan_of_allocation',), ('fee_motion',), ('fee_order',), ('approval_motion',),
    ('complaint_or_petition',), ('case_management_or_settlement_order',),
    ('trust_distribution_procedures',), ('trust_agreement',),
    ('bankruptcy_plan', 'plan_confirmation_order', 'disclosure_statement'),
    ('faq_page',), ('deadlines_page',), ('settlement_website_home', 'court_documents_index'),
    ('status_report_or_claims_report',), ('press_release', 'executive_summary'),
    ('court_opinion',), ('dismissal_order',),
]
DOCUMENT_TYPES = [value for row in TAXONOMY for value in row]

LABELS = {
    'settlement_agreement': 'Settlement agreement', 'master_settlement_agreement': 'Master settlement agreement',
    'settlement_term_sheet': 'Term sheet', 'preliminary_approval_order': 'Preliminary approval order',
    'final_approval_order': 'Final approval order', 'final_judgment': 'Final judgment',
    'consent_judgment_or_decree': 'Consent judgment or decree',
    'assurance_of_voluntary_compliance': 'Assurance of voluntary compliance / discontinuance',
    'administrative_order': 'Administrative (agency) order', 'long_form_notice': 'Long-form notice',
    'short_form_notice': 'Short-form notice', 'claim_form': 'Claim form (blank)',
    'opt_out_or_objection_form': 'Opt-out or objection form', 'plan_of_allocation': 'Plan of allocation',
    'fee_motion': 'Fee motion', 'fee_order': 'Fee order', 'approval_motion': 'Approval motion',
    'complaint_or_petition': 'Complaint or petition',
    'case_management_or_settlement_order': 'Case-management or settlement-administration order',
    'trust_distribution_procedures': 'Trust distribution procedures', 'trust_agreement': 'Trust agreement',
    'bankruptcy_plan': 'Bankruptcy plan', 'plan_confirmation_order': 'Plan confirmation order',
    'disclosure_statement': 'Disclosure statement', 'faq_page': 'FAQ', 'deadlines_page': 'Deadlines / important dates',
    'settlement_website_home': 'Settlement website home page', 'court_documents_index': 'Court documents index',
    'status_report_or_claims_report': 'Status or claims report', 'press_release': 'Press release',
    'executive_summary': 'Summary (never authority for terms)', 'court_opinion': 'Court opinion',
    'dismissal_order': 'Dismissal order', UNCLASSIFIED: 'Unclassified',
}

J = r'judge?ments?'
# A cue preceded by one of these describes a document ABOUT another document
# ("Order Approving AVC", "Notice of Agreement"); the embedded cue must not decide.
WRAPPER = re.compile(r'\b(?:order (?:approving|granting|on|re|regarding)|application for|motion (?:for|to)|notice of|'
                     r'memorandum|brief|declaration|exhibit|objections? to|response to|opposition to)\b[^,;:]{0,60}$', re.I)
GUARDED = {'settlement_agreement', 'master_settlement_agreement', 'assurance_of_voluntary_compliance',
           'consent_judgment_or_decree', 'trust_agreement', 'bankruptcy_plan', 'final_judgment'}

# Ordered: first match wins. "About" documents and specific instruments precede general ones.
LABEL_RULES = [(name, re.compile(pattern, re.I)) for name, pattern in [
    ('short_form_notice', r'short[- ]form(?: class)? notice|summary notice|post ?card notice|e-?mail notice|publication notice'),
    ('faq_page', r'\bfaqs?\b|frequently asked questions'),
    ('press_release', r'press release|news release'),
    ('executive_summary', r'executive summary|\bsummary of\b|\bhighlights\b|\bq ?(?:&|and) ?a\b'),
    ('status_report_or_claims_report', r'status report|claims administrator\W{0,2}s? report|distribution report|claims report'),
    ('trust_distribution_procedures', r'trust distribution procedures?|\btdps?\b|claims resolution procedures?'),
    ('trust_agreement', r'trust agreement'),
    ('plan_confirmation_order', r'confirmation order|order confirming'),
    ('disclosure_statement', r'disclosure statement'),
    ('bankruptcy_plan', r'plan of reorgani[sz]ation|chapter 11 plan|plan of liquidation'),
    ('master_settlement_agreement', r'master settlement agreement|\bmsa\b|global settlement'),
    ('settlement_term_sheet', r'term sheet|memorandum of understanding'),
    ('fee_order', r'order (?:awarding|granting|approving|on)[^.]{0,40}\bfees\b|\bfee order\b|common benefit order|fee award order|fee payment order'),
    ('fee_motion', r'motion for[^.]{0,60}\bfees\b|fee petition|fee (?:and expense )?application|fee motion|service awards?'),
    ('approval_motion', r'motion for (?:preliminary |final )?approval|motion (?:for|to)[^.]{0,30}approv|memorandum (?:of law )?in support'),
    ('preliminary_approval_order', r'preliminary approval order|order[^.]{0,50}preliminar(?:y|ily) approv|order directing notice'),
    ('final_approval_order', r'final approval order|order[^.]{0,50}final approval|final order and ' + J + r'|order and final ' + J + r'|final ' + J + r' and order (?:approving|of dismissal)'),
    ('dismissal_order', r'order of dismissal|order dismissing|dismissal order'),
    ('court_opinion', r'memorandum opinion|memorandum and order|opinion and order|\bopinion\b'),
    ('case_management_or_settlement_order', r'\bcmo\b|case management order|pre-?trial order|order appointing|qualified settlement fund order|'
                                            r'allocation order|cost fund order|order (?:approving|granting)[^.]{0,60}(?:final accounting|dissolution|distribution|allocation|qualified settlement fund)'),
    ('administrative_order', r'order instituting|cease[- ]and[- ]desist|administrative consent order|agreement containing consent orders?|'
                             r'\b(?:ftc|sec|cfpb|fcc|occ|fdic|finra)\b.{0,60}\bconsent (?:order|decree)'),
    ('consent_judgment_or_decree', r'consent (?:order and )?(?:' + J + r'|decrees?|orders?)|stipulated (?:final )?(?:' + J + r'|orders?)|agreed (?:final )?' + J +
                                   r'|agreed entry|' + J + r' upon stipulation|stipulation for final ' + J),
    ('final_judgment', r'final ' + J + r'|\b' + J + r'\b'),
    ('assurance_of_voluntary_compliance', r'assurance of voluntary compliance|assurance of discontinuance|\bavc\b|\baod\b'),
    ('plan_of_allocation', r'plan of allocation|distribution plan|plan of distribution|allocation (?:methodology|plan)|settlement matrix|injury grid'),
    ('opt_out_or_objection_form', r'exclusion (?:request|form)|request for exclusion|opt[- ]?out(?: form)?|objection form'),
    ('claim_form', r'claim form|proof of claim|registration form'),
    ('long_form_notice', r'long[- ]form notice|detailed notice|class notice|full notice|notice of (?:proposed )?(?:class action )?settlement|notice of class action'),
    ('settlement_agreement', r'settlement agreement|stipulation (?:and agreement )?of (?:class action )?settlement|agreement of settlement'),
    ('complaint_or_petition', r'\bcomplaint\b|\bpetition\b'),
    ('deadlines_page', r'important dates|key dates|\bdeadlines\b'),
    ('court_documents_index', r'court documents|important documents|case documents|settlement documents|court filings'),
]]

# First-page headings. `heading` must appear on a caption-level line to overrule passes 1-2.
TEXT_RULES = [(name, re.compile(heading), re.compile(extra, re.I | re.S) if extra else None, negate) for name, heading, extra, negate in [
    ('trust_distribution_procedures', r'TRUST DISTRIBUTION PROCEDURES', None, False),
    ('trust_agreement', r'TRUST AGREEMENT', None, False),
    ('plan_confirmation_order', r'ORDER CONFIRMING|CONFIRMATION ORDER', None, False),
    ('disclosure_statement', r'DISCLOSURE STATEMENT', None, False),
    ('bankruptcy_plan', r'PLAN OF REORGANI[SZ]ATION|CHAPTER 11 PLAN', None, False),
    ('master_settlement_agreement', r'MASTER SETTLEMENT AGREEMENT', None, False),
    ('settlement_term_sheet', r'TERM SHEET', None, False),
    ('fee_motion', r"MOTION FOR[^\n]{0,80}ATTORNEYS.? FEES", None, False),
    ('approval_motion', r'MOTION FOR (?:PRELIMINARY|FINAL) APPROVAL', None, False),
    ('fee_order', r'\bORDER\b', r"awarding attorneys.? fees|common benefit", False),
    ('preliminary_approval_order', r'\bORDER\b', r'preliminarily approv|directing notice to the (?:settlement )?class', False),
    ('final_approval_order', r'\bFINAL\b', r'approv.{0,4000}fair, reasonable,? and adequate|fair, reasonable,? and adequate.{0,4000}approv', False),
    ('case_management_or_settlement_order', r'PRETRIAL ORDER NO|CASE MANAGEMENT ORDER', None, False),
    ('dismissal_order', r'ORDER OF DISMISSAL', None, False),
    ('court_opinion', r'MEMORANDUM OPINION|OPINION AND ORDER', None, False),
    ('consent_judgment_or_decree', r'CONSENT JUDGE?MENT|CONSENT DECREE|STIPULATED (?:FINAL )?JUDGE?MENT', None, False),
    ('final_judgment', r'FINAL JUDGE?MENT', r'approv', True),
    ('assurance_of_voluntary_compliance', r'ASSURANCE OF VOLUNTARY COMPLIANCE|ASSURANCE OF DISCONTINUANCE', None, False),
    ('administrative_order', r'\bORDER\b', r'(?:securities and exchange commission|consumer financial protection bureau|federal trade commission|federal communications commission)'
                                         r'.{0,1500}(?:file no\.|administrative proceeding|docket no\.)', False),
    ('plan_of_allocation', r'PLAN OF ALLOCATION', None, False),
    ('opt_out_or_objection_form', r'REQUEST FOR EXCLUSION|OPT-?OUT FORM|EXCLUSION FORM', None, False),
    ('claim_form', r'CLAIM FORM|PROOF OF CLAIM', None, False),
    ('settlement_agreement', r'SETTLEMENT AGREEMENT|STIPULATION (?:AND AGREEMENT )?OF SETTLEMENT', None, False),
    ('complaint_or_petition', r'\bCOMPLAINT\b', None, False),
]]
ENTERED_INTO = re.compile(r'this (?:class action )?settlement agreement[^.]{0,80}is (?:made and )?entered into', re.I)
NOTICE_LEGEND = re.compile(r'a (?:federal |state )?court (?:has )?authorized this notice', re.I)
NOTICE_QA = re.compile(r'(?m)^\s*\d{1,2}\.\s+(?:Why|What|How|Who|When|Do I|Am I|Can I|Where)\b')


def normalize_label(value):
    """Turn a filename, URL segment or link text into a comparable phrase."""
    value = unquote(value or '')
    value = re.sub(r'\.(?:pdf|html?|aspx?|php|docx?)$', '', value.strip(), flags=re.I)
    value = re.sub(r'(?<=[a-z])(?=[A-Z][a-z])', ' ', value)
    value = re.sub(r'[\s_\-+/\\]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def _label_match(value):
    text = normalize_label(value)
    if not text:
        return None
    for name, pattern in LABEL_RULES:
        for match in pattern.finditer(text):
            if name in GUARDED and WRAPPER.search(text[:match.start()]):
                continue
            return name, match.group(0)
    return None


def _caption_lines(text, limit=60):
    lines = []
    for raw in (text or '').splitlines():
        line = ' '.join(raw.split())
        if not line:
            continue
        letters = [c for c in line if c.isalpha()]
        if letters and len(line) <= 140 and sum(c.isupper() for c in letters) / len(letters) >= 0.7:
            lines.append(line)
        limit -= 1
        if limit <= 0:
            break
    return lines


def _text_match(text, page_count=None):
    """Return (type, matched, caption_level) from first-page text, or None."""
    if not text or not text.strip():
        return None
    head = text[:12000]
    captions = _caption_lines(head)
    for name, heading, extra, negate in TEXT_RULES:
        found = next((m.group(0) for line in captions for m in [heading.search(line)] if m), None)
        if not found:
            continue
        if extra is not None:
            hit = extra.search(head)
            if bool(hit) == negate:
                continue
            if hit and not negate:
                found = found + ' + ' + ' '.join(hit.group(0).split())[:80]
        return name, found, True
    hit = ENTERED_INTO.search(head)
    if hit:
        return 'settlement_agreement', ' '.join(hit.group(0).split()), False
    legend = NOTICE_LEGEND.search(head)
    if legend:
        phrase = ' '.join(legend.group(0).split())
        if isinstance(page_count, int) and page_count > 3:
            return 'long_form_notice', phrase + ' (%d pages)' % page_count, False
        if isinstance(page_count, int) and page_count <= 2:
            return 'short_form_notice', phrase + ' (%d pages)' % page_count, False
        if NOTICE_QA.search(head):
            return 'long_form_notice', phrase + ' + question-and-answer headings', False
    return None


def _evidence(matched, where, value, overruled=None):
    return {'matched': matched, 'where': where, 'source_value': (value or '')[:300], 'overruled': overruled}


def classify(link_text=None, filename=None, url=None, first_page_text=None, is_html=False, page_count=None):
    """Return {'type','type_basis','type_evidence'} using the three ordered passes."""
    decided = None
    hit = _label_match(link_text)
    if hit:
        decided = (hit[0], 'link_text', _evidence(hit[1], 'link_text', link_text))
    if not decided:
        name = filename
        path = ''
        if url:
            path = urlsplit(url).path
            name = name or path.rstrip('/').rsplit('/', 1)[-1]
        hit = _label_match(name)
        if hit:
            decided = (hit[0], 'filename', _evidence(hit[1], 'filename', normalize_label(name)))
        elif url and is_html and path in ('', '/') and not urlsplit(url).query:
            decided = ('settlement_website_home', 'url_path', _evidence('/', 'url_path', url))
    if is_html:
        # For saved pages the "first page" is the page title / first heading.
        if not decided:
            hit = _label_match((first_page_text or '')[:300])
            if hit and hit[0] in ('faq_page', 'deadlines_page', 'court_documents_index', 'press_release'):
                decided = (hit[0], 'first_page_text', _evidence(hit[1], 'page_title', (first_page_text or '')[:300]))
    else:
        text_hit = _text_match(first_page_text, page_count)
        if text_hit:
            name, matched, caption = text_hit
            if not decided:
                decided = (name, 'first_page_text', _evidence(matched, 'first_page_caption_title' if caption else 'first_page_text', None))
            elif caption and name != decided[0]:
                overruled = {'type': decided[0], 'type_basis': decided[1], 'matched': decided[2]['matched']}
                decided = (name, 'first_page_text', _evidence(matched, 'first_page_caption_title', None, overruled))
    if not decided:
        return {'type': UNCLASSIFIED, 'type_basis': 'none', 'type_evidence': None}
    return {'type': decided[0], 'type_basis': decided[1], 'type_evidence': decided[2]}


# ---- mass-tort relevance: keyword mention only, never a legal characterisation ----
MASS_TORT_TERMS = [(category, re.compile(pattern, re.I)) for category, pattern in [
    ('mdl', r'\bMDL\b(?:\s*(?:No\.?|Number|Docket No\.?)?\s*\d{3,4})?|multi-?district litigation'),
    ('product_liability', r'products? liability|defective (?:product|design|drug|device)s?|failure to warn|defective products?'),
    ('personal_injury', r'personal injur(?:y|ies)|bodily injur(?:y|ies)|wrongful death|concussion|\bcancer\b|mesothelioma|traumatic brain'),
    ('pharmaceutical', r'pharmaceuticals?|\bdrugs?\b|\bmedications?\b|opioids?|\w*sartan\b|insulin|ranitidine|zantac|vaccines?|prescription'),
    ('medical_device', r'medical devices?|\bimplants?\b|pacemaker|\bCPAP\b|hernia mesh|pelvic mesh|hip (?:implant|replacement)|\bstents?\b|earplugs?|\bIVC filter'),
    ('chemical_exposure', r'\bPFAS\b|\bAFFF\b|asbestos|\btalc(?:um)?\b|glyphosate|roundup|paraquat|benzene|ethylene oxide|toxic|contaminat\w+|'
                          r'\bchemicals?\b|pesticides?|zonolite|vermiculite|lead (?:paint|poisoning|pipes)|forever chemicals|e-?cigarettes?|vaping'),
]]


def mass_tort_signals(*texts):
    """Distinct (category, term) keyword mentions across the supplied texts."""
    seen, result = set(), []
    for text in texts:
        if not text:
            continue
        for category, pattern in MASS_TORT_TERMS:
            for match in pattern.finditer(text):
                term = ' '.join(match.group(0).split())
                key = (category, term.lower())
                if key not in seen:
                    seen.add(key)
                    result.append({'category': category, 'term': term})
    return result
