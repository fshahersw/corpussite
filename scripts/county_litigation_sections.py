"""Conservative, local document structure extraction with exact text evidence.

Offsets refer to the supplied clean reading copy. Observations are not findings
that a rule is currently in force, and per-rule dates are never document dates.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re

VERSION = 'county-document-structure-1'
MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
DATE = rf'(?:{MONTH}\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}[/-]\d{{1,2}}[/-](?:\d{{4}}|\d{{2}})|\d{{4}}-\d{{2}}-\d{{2}})'
DATE_LINE = re.compile(rf'^\s*\(?\s*(?P<label>issued|revised|revision date|effective(?: date)?|adopted(?: on)?)\s*[:\-]?\s*(?P<date>{DATE})\s*\)?\s*$', re.I)
HEADING = re.compile(r'^(?P<label>DIVISION|CHAPTER|PART|ARTICLE|RULE|LCR|LAR|LSPR|LR)\s+(?P<number>[A-Za-z0-9][A-Za-z0-9.:-]*)(?:\s*[-.–:]?\s*)(?P<title>.*)$', re.I)


def normalize_date(value):
    # A two-digit year stays unnormalized rather than assigning a century.
    formats = ('%B %d, %Y', '%B %d %Y', '%m/%d/%Y', '%m-%d-%Y', '%Y-%m-%d')
    if not re.search(r'\b\d{4}\b', value):
        return None
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def extract_structure(text, resource_type='unknown', max_sections=500):
    if not isinstance(text, str):
        raise TypeError('text must be a decoded reading copy')
    lines=[]; offset=0
    for raw in text.splitlines(keepends=True):
        content=raw.rstrip('\r\n'); stripped=content.strip()
        start=offset+len(content)-len(content.lstrip())
        if stripped:
            lines.append((stripped,start,start+len(stripped)))
        offset+=len(raw)
    def proof(line):
        value,start,end=line
        return {'excerpt':value,'start':start,'end':end,'basis':'exact_clean_text_slice'}
    dates=[]; seen_dates=set(); headings=[]; flags=[]
    legal=resource_type in {'local_rule','standing_order'}
    for index,line in enumerate(lines):
        value,start,end=line
        if start<1800:
            match=DATE_LINE.fullmatch(value)
            if match:
                label=match['label'].lower().split()[0]
                key=(label,match['date'])
                if key not in seen_dates:
                    dates.append({'label':label,'value':match['date'],'iso_date':normalize_date(match['date']),
                                  'scope':'document_header_candidate','evidence':proof(line),
                                  'currency_verified':False})
                    seen_dates.add(key)
            # A division caption is stronger evidence than a repealed entry in a TOC.
            if legal and re.search(r'\b(?:repealed|superseded|draft|proposed)\b',value,re.I):
                previous=lines[index-1][0] if index else ''
                if re.match(r'^DIVISION\s+[\dIVX]+\s*$',previous,re.I) or re.match(r'^(?:DRAFT|PROPOSED)\s+(?:LOCAL|COURT|RULE|ORDER)',value,re.I):
                    flags.append({'status':re.search(r'\b(repealed|superseded|draft|proposed)\b',value,re.I).group(1).lower(),
                                  'scope':'opening_caption','evidence':proof(line)})
        if not legal or len(value)>220 or len(headings)>=max_sections:
            continue
        heading=HEADING.fullmatch(value)
        if not heading:
            continue
        # Exclude sentences that merely cite another rule and obvious TOC leaders.
        if re.search(r'\.{4,}',value) or re.search(r'\b(?:provides|requires|states|permits|prohibits|of the California Rules)\b',heading['title'],re.I):
            continue
        headings.append({'label':heading['label'].lower(),'number':heading['number'].rstrip('.'),
                         'title':heading['title'].strip(),'evidence':proof(line),
                         'role':'outline_candidate','not_a_separate_rule_record':True})
    return {'version':VERSION,'text_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'text_characters':len(text),'outline':headings,'date_observations':dates,'status_observations':flags,
            'outline_truncated':len(headings)>=max_sections,
            'qualification':'Exact source observations; outline may include a contents entry. Dates and status are not a current-law determination.'}
