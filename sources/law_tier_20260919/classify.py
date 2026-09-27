"""Transparent structural classifier for saved official law pages, plus court-rule set typing.

Everything here is rule based and explainable: each label carries the signals that produced it (class_basis)
and a confidence tier. Labels describe the *shape* of the saved text (law body, index, site chrome, notice,
form); they never say that a text is current, complete or in force.
"""
from __future__ import annotations

import re

CLASSES = ('statute_body', 'constitution_body', 'regulation_body', 'court_rule_body', 'hub_or_index', 'navigation',
           'notice', 'form', 'other')
RULE_SETS = ('civil_procedure', 'evidence', 'appellate', 'criminal', 'local', 'professional_conduct', 'other')

PAGE_MARK = re.compile(r'--- PAGE \d+ ---')
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
FUNCTION_WORDS = frozenset('the of to and or a in be by shall any such for is that with as not this may which on an under if'.split())
MODAL = re.compile(r'\b(?:shall|must|may not|may|means|is unlawful|is guilty)\b', re.I)
SECTION_PATTERNS = tuple(re.compile(p) for p in (
    r'§+\s*\d',
    r'\b(?:Sec(?:tion|s)?\.?|SECTION|SEC\.)\s+\d+[\w.\-:]*',
    r'(?:^|\s)\d{1,3}[A-Z]?\s?[-–:]\s?\d{1,5}(?:[-–.:]\d+)*[a-zA-Z]?\.\s+[A-Z]',
    r'\bNRS\s+\d+[A-Z]?\.\d+',
    r'(?:^|\s)\d{1,3}[A-Z]?\.\d{2,5}[a-z]?\s+[A-Z][a-z]',
    r'\b(?:Rule|RULE)\s+\d+(?:\.\d+)*',
    r'\b(?:Article|ARTICLE|Art\.)\s+[IVXLC\d]+',
))
SUBSECTION = re.compile(r'\(\s?(?:[a-z]|\d{1,2}|[ivx]{1,4})\s?\)')
TOC_ENTRY = re.compile(r'\b(?:Chapter|CHAPTER|Ch\.|Chs\.|Title|TITLE|Part|PART|Subtitle|SUBTITLE|Division|DIVISION|Volume|VOLUME)\s+[\dIVXLC]+[A-Z]?\b')
HISTORY = re.compile(r'\bHistory:|\bam\. \d{4}|\badded \d{4}|\bLaws \d{4}|\bL\. ?\d{4}|\bP\.A\. \d|\bActs \d{4}|\bS\.L\. \d{4}|\bStats\. \d{4}|\bc\. \d+, ?§')
NAV_TOKEN = re.compile(r'Skip to (?:main )?content|Skip to Menu|Main navigation|Contact Us|Privacy Policy|Accessibility|Site Map|Sign in|Log in|Search\b', re.I)
BLANK_RUN = re.compile(r'_{5,}')
FORM_CUE = re.compile(r'\b(?:Signature|Print Name|Case No\.?|Case Number|Plaintiff|Defendant|Petitioner|Respondent|Notary|Date:)', re.I)
REPEAL_STUB = re.compile(r'[\[(]?\b(?:Repealed|Reserved|Omitted|Expired|Renumbered|Transferred|Disapproved|Superseded|Vacant|Not in use)\b', re.I)
ORDER_CUE = re.compile(r'IT IS (?:HEREBY |THEREFORE )?ORDERED|ORDER (?:AMENDING|ADOPTING|APPROVING|PROMULGATING|RESCINDING)|'
                       r'IN RE:? .{0,80}(?:AMENDMENTS?|ADOPTION)|NOTICE OF (?:PROPOSED|PUBLIC|RULEMAKING|HEARING|INTENT)|'
                       r'REQUEST FOR (?:PUBLIC )?COMMENT', re.I)
SESSION_LAW = re.compile(r'Statutes of Nevada, Pages?|/Statutes/\d+\w*\d{4}/Stats\d{4}|\bSession Laws?\b|\bLaws of \d{4}\b|\bActs of \d{4}\b|/sessionlaws?/', re.I)
LAW_UNIT = re.compile(r'\b(?:chapter|title|rule|rules|article|section|statutes?|code|constitution)\b', re.I)
FAMILY_BY_CATEGORY = (('court_rules', 'court_rule_body'), ('administrative_code', 'regulation_body'),
                      ('constitution', 'constitution_body'), ('home_rule_act', 'statute_body'), ('statutes', 'statute_body'))


def text_features(text):
    """Counts taken from the saved extracted text only (page markers removed)."""
    text = PAGE_MARK.sub(' ', text or '')
    chars = len(text.strip())
    words = WORD.findall(text)
    per_k = (lambda n: round(n * 1000.0 / chars, 2)) if chars else (lambda n: 0.0)
    modal = len(MODAL.findall(text))
    sections = sum(len(p.findall(text)) for p in SECTION_PATTERNS)
    toc = len(TOC_ENTRY.findall(text))
    function = sum(1 for w in words if w.lower() in FUNCTION_WORDS)
    return {
        'text_chars': chars, 'words': len(words),
        'function_ratio': round(function / len(words), 3) if words else 0.0,
        'modal_count': modal, 'modal_per_k': per_k(modal),
        'section_markers': sections, 'section_per_k': per_k(sections),
        'subsection_markers': len(SUBSECTION.findall(text)),
        'toc_entries': toc, 'toc_per_k': per_k(toc),
        'history_notes': len(HISTORY.findall(text)),
        'nav_tokens': len(NAV_TOKEN.findall(text)),
        'blank_runs': len(BLANK_RUN.findall(text)), 'form_cues': len(FORM_CUE.findall(text)),
        'law_unit_words': len(LAW_UNIT.findall(text)),
    }


def html_link_features(raw_bytes):
    """Share of visible text inside <a> elements, measured on the saved original HTML bytes (lxml)."""
    try:
        import lxml.html
        tree = lxml.html.fromstring(raw_bytes)
    except Exception:
        return {}
    for node in tree.xpath('//script|//style|//noscript|//template'):
        node.drop_tree()
    total = len(' '.join(tree.text_content().split()))
    anchors = tree.xpath('//a')
    link_chars = sum(len(' '.join(a.text_content().split())) for a in anchors)
    for a in anchors:
        try: a.drop_tree()
        except Exception: pass
    rest = ' '.join(tree.text_content().split())
    words = WORD.findall(rest)
    function = sum(1 for w in words if w.lower() in FUNCTION_WORDS)
    return {'links': len(anchors), 'link_ratio': round(min(1.0, link_chars / total), 3) if total else None,
            'nonlink_chars': len(rest), 'nonlink_function_ratio': round(function / len(words), 3) if words else 0.0}


def body_family(categories, title, url):
    """Which law family a body belongs to: category evidence first, title/URL only to break a mixed category."""
    cats = [c for c in categories if c and c != 'ocr_text']
    hay = ((title or '') + ' ' + (url or '')).lower()
    families = [fam for token, fam in FAMILY_BY_CATEGORY if token in cats]
    if len(set(families)) > 1:
        if 'constitution_body' in families and 'constitution' in hay: return 'constitution_body', 'mixed category resolved by "constitution" in title/URL'
        if 'court_rule_body' in families and re.search(r'\brules?\b', hay): return 'court_rule_body', 'mixed category resolved by "rule" in title/URL'
        if 'statute_body' in families: return 'statute_body', 'mixed category; statutes token kept (no title/URL tie-breaker)'
    if families: return families[0], 'category=' + ';'.join(cats)
    if 'constitution' in hay: return 'constitution_body', 'no law category; "constitution" in title/URL'
    if re.search(r'\bcourt rules?\b|rules of (?:civil|criminal|appellate|evidence)', hay): return 'court_rule_body', 'no law category; court-rule wording in title/URL'
    if re.search(r'administrative code|admin\.? code|regulations?\b', hay): return 'regulation_body', 'no law category; regulation wording in title/URL'
    if re.search(r'statut|\bcode\b', hay): return 'statute_body', 'no law category; statute wording in title/URL'
    return None, 'no law-family evidence in category, title or URL'


def _result(cls, basis, confidence, detail=None):
    return {'law_body_class': cls, 'class_detail': detail, 'class_basis': basis, 'confidence': confidence,
            'legal_currency_asserted': False}


def classify(f, text, categories, title, url, fmt):
    """Return the label dict for one saved record. `f` = text_features (+ html_link_features for HTML originals)."""
    chars = f.get('text_chars', 0)
    if chars < 80:
        return _result('other', 'saved text has %d characters; too little to classify' % chars, 'low', 'insufficient_text')
    head = PAGE_MARK.sub(' ', text or '')[:1500]
    link_ratio = f.get('link_ratio')
    html = fmt == 'html' and link_ratio is not None
    function = f.get('nonlink_function_ratio') if html and f.get('nonlink_chars', 0) >= 400 else f.get('function_ratio', 0.0)
    prose_chars = f.get('nonlink_chars', chars) if html else chars
    hay = (title or '') + ' ' + (url or '')

    if chars < 1500 and REPEAL_STUB.search(head) and f['modal_count'] <= 2:
        return _result('notice', 'short text (%d chars) with a repealed/reserved/transferred marker and <=2 modal verbs' % chars,
                       'high' if chars < 600 else 'medium', 'repeal_or_reserved_stub')
    orders = ORDER_CUE.findall(head)
    if orders and chars < 60000 and f['section_markers'] < 40:
        return _result('notice', 'order/notice wording in the first 1,500 characters (%s)' % '; '.join(sorted({o.strip()[:40].upper() for o in orders})[:3]),
                       'high' if len(orders) >= 2 else 'medium', 'amendment_order_or_notice')
    if (f['blank_runs'] >= 8 and f['form_cues'] >= 2) or (f['blank_runs'] >= 3 and re.search(r'\bforms?\b', hay, re.I)):
        return _result('form', '%d blank-line runs and %d form cue words%s' % (f['blank_runs'], f['form_cues'],
                       '; "form" in title/URL' if re.search(r'\bforms?\b', hay, re.I) else ''),
                       'high' if f['blank_runs'] >= 15 else 'medium')

    family, family_basis = body_family(categories, title, url)
    strong = (function >= 0.30 and f['modal_per_k'] >= 1.5 and (f['section_markers'] >= 3 or f['subsection_markers'] >= 6)
              and chars >= 2000 and prose_chars >= 1500 and (not html or link_ratio < 0.5))
    medium = (function >= 0.27 and f['modal_count'] >= 1 and (f['section_markers'] >= 1 or f['subsection_markers'] >= 2)
              and chars >= 300 and prose_chars >= 250 and (not html or link_ratio < 0.5))
    signals = 'function-word ratio %.2f, modal verbs %.1f/1k chars, %d section markers, %d subsection markers, %d chars%s' % (
        function, f['modal_per_k'], f['section_markers'], f['subsection_markers'], chars,
        ', link-text ratio %.2f' % link_ratio if html else '')
    if strong or medium:
        if SESSION_LAW.search(hay):
            return _result('other', 'law-like prose (%s) but title/URL indicates a session-law compilation, not a codified body' % signals,
                           'medium', 'session_law_compilation')
        if family is None:
            return _result('other', 'law-like prose (%s) but %s' % (signals, family_basis), 'low', 'law_like_text_unknown_family')
        confidence = 'high' if strong and (f['history_notes'] >= 1 or f['section_markers'] >= 8) else ('medium' if strong or f['modal_count'] >= 3 else 'low')
        return _result(family, 'prose with legal structure: %s; family from %s' % (signals, family_basis), confidence)

    listing = f['toc_per_k'] + f['section_per_k']
    if function < 0.25 and listing >= 3 and (f['toc_entries'] + f['section_markers']) >= 8:
        return _result('hub_or_index', 'list of law units without prose: %.1f chapter/title/section headings per 1k chars, function-word ratio %.2f%s' % (
            listing, function, ', link-text ratio %.2f' % link_ratio if html else ''),
            'high' if (html and link_ratio >= 0.4) or (not html and function < 0.15) else 'medium')
    if html and link_ratio >= 0.45 and f.get('links', 0) >= 20 and f['law_unit_words'] >= 8 and (f['toc_entries'] + f['section_markers']) >= 4:
        return _result('hub_or_index', 'link-dominated page (link-text ratio %.2f, %d links) naming law units (%d headings)' % (
            link_ratio, f['links'], f['toc_entries'] + f['section_markers']), 'medium')
    if html and (link_ratio >= 0.55 or (f['nav_tokens'] >= 2 and prose_chars < 1500)):
        return _result('navigation', 'site chrome dominates: link-text ratio %.2f, %d navigation tokens, %d characters outside links' % (
            link_ratio, f['nav_tokens'], prose_chars), 'high' if link_ratio >= 0.7 and prose_chars < 1000 else 'medium')
    if not html and function < 0.2 and f['nav_tokens'] >= 2:
        return _result('navigation', 'menu-like text: function-word ratio %.2f with %d navigation tokens' % (function, f['nav_tokens']), 'low')
    return _result('other', 'no dominant structural signal (%s)' % signals, 'low', 'unclassified_text')


_ORDINAL = r'(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|\d{1,2}(?:st|nd|rd|th))'
RULE_SET_PATTERNS = (
    ('local', 'local', re.compile(r'\blocal\b|\b%s\s+judicial\s+(?:district|circuit)\b|\breglas locales\b' % _ORDINAL, re.I)),
    ('professional_conduct', 'judicial_conduct', re.compile(r'judicial conduct|code of judicial|judicial ethics|judicial disciplin|judicial standards', re.I)),
    ('professional_conduct', 'bar_admission_or_discipline', re.compile(r'disciplin|admission to (?:the )?(?:\w+ )?(?:bar|practice)\b|bar admission|\bbar rules\b|admission and practice|abogac[ií]a|'
                                                                       r'lawyer|attorney|client protection|continuing legal education|\bcle\b', re.I)),
    ('professional_conduct', 'professional_conduct', re.compile(r'professional (?:conduct|responsibility|practice|rules)|conducta profesional|client-lawyer', re.I)),
    ('evidence', None, re.compile(r'\bevidence\b|\bevid\.|\bevidencia\b', re.I)),
    ('appellate', None, re.compile(r'appellate|\bappeals?\b|\bapp\. ?p\b|apelaci', re.I)),
    ('criminal', None, re.compile(r'\bcriminal\b|\bcrim\.|\bpenal\b', re.I)),
    ('other', 'juvenile', re.compile(r'juvenile|delinquen|child(?:ren)?\b|menores', re.I)),
    ('other', 'family', re.compile(r'family|domestic relations|divorce|paternity|matrimonial', re.I)),
    ('other', 'probate', re.compile(r'probate|guardianship|surrogate|orphans|estates?\b|mental health|commitment', re.I)),
    ('other', 'small_claims_or_limited', re.compile(r'small claims|traffic|infraction|magistrate|justice court|justice of the peace|municipal court|housing|eviction|landlord', re.I)),
    ('civil_procedure', None, re.compile(r'civil procedure|rules? of civil|civil rules?|\bciv\. ?p\b|trial procedure|procedimiento civil|civil practice|civil proceedings', re.I)),
    ('other', 'administration', re.compile(r'administrat|superintendence|judicial administration|general rules(?: of practice)?\b|general application|court records|public access|'
                                           r'electronic (?:filing|procedure)|e-filing|interpreters?|jury (?:selection|management)|court reporters?|fees\b', re.I)),
    ('other', 'specialised_court', re.compile(r'tax court|water court|land court|workers.? compensation|chancery|business court|commercial|arbitra|mediat|'
                                              r'm[eé]todos alternos|alternative dispute|bankruptcy|admiralty|habeas|post-?conviction', re.I)),
)


def rule_set_type(fields):
    """Type a court-rule set from (field_name, value) pairs. Returns (rule_set, detail, basis) or (None, None, None)."""
    for rule_set, detail, pattern in RULE_SET_PATTERNS:
        for name, value in fields:
            if not value: continue
            hit = pattern.search(str(value))
            if hit:
                return rule_set, detail, '%s matches "%s"' % (name, hit.group(0).lower())
    return None, None, None
