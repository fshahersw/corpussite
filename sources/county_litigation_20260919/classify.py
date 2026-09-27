"""Deterministic, evidence-returning litigation classification; no network."""
import re
from urllib.parse import urlsplit,unquote
VERSION='county-litigation-classifier-4'
KINDS={'local_rule','court_form','standing_order','filing_guidance','fee_schedule','court_information','court_contact','court_staff','source_directory','unknown'}
COURT=re.compile(r'\b(?:(?:superior|circuit|district|municipal|probate|family|juvenile|civil|criminal|county)\s+court|clerk of (?:the )?court|court clerk|judicial circuit|judicial district)\b',re.I)
EXCLUDE=re.compile(r'\b(?:election|voter|candidate filing|driver.?s? licen[sc]e|business licen[sc]e|tax assessor|property tax|recreation|parks?|camping|animal control|building permit|employment application|job application|commissioners? (?:meeting|calendar))\b',re.I)
PROPOSED_ORDER=re.compile(r'\bproposed[\s_-]+orders?\b',re.I)
ORDER_GUIDANCE=re.compile(r'\b(?:submit(?:ting)?|submissions?|instructions?|training|memo(?:randum)?)\b',re.I)

def evidence(pattern,text,label):
    match=re.search(pattern,text,re.I)
    return {'field':label,'excerpt':text[max(0,match.start()-90):min(len(text),match.end()+180)],'offset':match.start()} if match else None

def classify_link(anchor,url):
    value=' '.join((anchor or '').split());combined=value+' '+unquote(urlsplit(url).path)
    if EXCLUDE.search(combined):return {'resource_type':'unknown','eligible':False,'reason':'Non-litigation administrative topic','priority':99}
    if PROPOSED_ORDER.search(combined) and ORDER_GUIDANCE.search(combined):
        return {'resource_type':'filing_guidance','eligible':True,'reason':'Observed proposed-order submission, instruction, or training link; destination content must be reviewed','priority':2}
    checks=[('local_rule',r'local.?rules?|rules? of (?:practice|court)',1),('standing_order',r'standing.?orders?|administrative.?orders?|general.?orders?',1),
            ('fee_schedule',r'(?:court|filing|civil).{0,20}fees?|fee.?schedule',2),('filing_guidance',r'e.?filing|filing.{0,20}(?:guide|procedure|instruction)|how to file',2),
            ('court_form',r'court.{0,25}\bforms?\b|(?:civil|criminal|family|probate|juvenile).{0,20}\bforms?\b|\bforms?\b.{0,20}(?:court|civil)|/forms?/',2),
            ('court_contact',r'(?:court|clerk).{0,25}contact|contact.{0,25}(?:court|clerk)',3),('court_information',r'court|circuit clerk|law.?justice',3)]
    for kind,pattern,priority in checks:
        if re.search(pattern,combined,re.I):return {'resource_type':kind,'eligible':True,'reason':'Observed anchor/path topic; destination content must be reviewed','priority':priority}
    return {'resource_type':'unknown','eligible':False,'reason':'No explicit litigation signal in observed anchor/path','priority':99}

def filing_guidance_landing(title,text,links):
    """Require a short page composed of linked document labels, not instructions.

    Links come from the saved source. Unmatched substantive lines prevent the
    downgrade; only the page heading and a specific court-site motto are ignored.
    """
    if not text.strip() or len(text)>1000:return None
    normalize=lambda value:' '.join(re.sub(r'^\s*#{1,6}\s*','',value).split()).casefold()
    linked={}
    for link in links or []:
        label=link.get('label') or '';target=link.get('url') or ''
        if not re.search(r'\.pdf$',urlsplit(target).path,re.I):continue
        if classify_link(label,target)['resource_type']=='filing_guidance':linked[normalize(label)]=target
    if not linked:return None
    matched=[];title_key=normalize(title)
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        key=normalize(line)
        if key in linked:
            # A linked operative sentence still contains guidance in its own right.
            if re.match(r'^(?:file|submit|serve|pay|deliver|provide|attach|include)\b',key):return None
            matched.append({'label':re.sub(r'^\s*#{1,6}\s*','',line),'url':linked[key]});continue
        if not matched and len(key)>4 and key in title_key:continue
        if re.fullmatch(r'Justice in [A-Za-z ]+ will be Accessible, Fair, Effective, Responsive, and Accountable\.',line,re.I):continue
        return None
    return {'field':'observed_link_only_guidance_landing','linked_documents':matched} if matched else None

def classify(title,text,url='',mime_type='',links=None):
    title=title or '';text=text or '';head=title+'\n'+text[:10000];binary=mime_type in {'application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document','application/rtf'}
    # Page purpose comes from the title/path, not incidental citations or a shared
    # navigation menu. Native documents may additionally use their opening caption.
    primary_topic=title+'\n'+urlsplit(url).path.replace('_',' ').replace('-',' ')
    topic=primary_topic+(('\n'+text[:650]) if binary else '')
    court=bool(COURT.search(head))
    # A citation embedded in a paragraph or linked index caption is not a rule
    # heading. Actual line-start headings are necessary for a rule-body claim.
    headings=re.findall(r'(?im)^\s*(?:RULE|LCR|LAR|LSPR|CrR|LR)\s*\d[.\d]*(?:\s|[.:(])[^\n]{0,130}',text)
    rule_structure=bool(headings)
    index_topic=bool(re.search(r'\b(?:table of contents|index)\b',title,re.I) or re.search(r'/(?:index|table.of.contents)\.pdf$',url,re.I))
    retention_topic=bool(re.search(r'(?:court )?(?:file|record)s? retention|retention time frames',topic,re.I))
    blank_form=binary and re.search(r'FOR COURT USE ONLY|ATTORNEY OR PARTY WITHOUT ATTORNEY',text[:2500],re.I) and re.search(r'\(Name|TELEPHONE NO|_{4,}',text[:2500],re.I)
    form_packet=binary and re.search(r'\b(?:self[- ]help\s+)?form\s+packet\b',title+'\n'+text[:700],re.I) and re.search(r'Case (?:Number|Name)|Fill in court name|FOR COURT USE ONLY',text[:2500],re.I)
    rule_topic=r'local\s+(?:court\s+)?rules|rules of (?:practice|court)|rules court|/rules(?:/|$)|/local.rules/'
    rule_caption=binary and re.search(r'(?im)^\s*(?:LOCAL\s+(?:COURT\s+)?RULES\b|[^\n.;:]{0,150}\bCOURT\s+LOCAL\s+(?:COURT\s+)?RULES(?:\s+\d+)?\s*$|RULES OF (?:PRACTICE|COURT)\b)',text[:650])
    official_rule_directory=(urlsplit(url).hostname in {'www.courts.wa.gov','courts.wa.gov'} and urlsplit(url).path.startswith('/court_rules/pdf/LCR/'))
    kind='unknown';shape='unclassified';reason=None
    if re.search(r'(?:verify you are human|access denied|checking your browser|just a moment)',text[:400],re.I) and len(text)<2500:
        return {'resource_type':'unknown','document_shape':'access_error','legal_status':'unknown','status':'needs_review','substantive':False,'evidence':[{'excerpt':text[:400],'field':'access_barrier'}],'version':VERSION}
    if EXCLUDE.search(title) and not re.search(r'local rules|standing order',title,re.I):reason='Non-litigation administrative title'
    elif court and retention_topic:
        kind='court_information';shape='records_retention_guidance';reason=evidence(r'(?:file|record)s? retention|retention time frames',head,'document_purpose')
    elif court and (blank_form or form_packet):
        kind='court_form';shape='form_document';reason=evidence(r'(?:self[- ]help\s+)?form\s+packet|FOR COURT USE ONLY|ATTORNEY OR PARTY WITHOUT ATTORNEY',head,'blank_form_template')
        if re.search(r'\bFILED\s+(?:\d{1,2}[/-]|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))',text[:800],re.I):
            kind='unknown';shape='possible_filed_pleading'
    elif re.search(rule_topic,primary_topic,re.I) or (binary and court and (rule_structure or rule_caption or official_rule_directory)):
        kind='local_rule';shape='rule_index' if index_topic else 'rule_body' if rule_structure and len(text)>200 and (binary or len(headings)>=2) else 'rule_index'
        if re.search(r'rules affected by|memo.local.rules',title+' '+url,re.I):shape='rule_change_notice'
        elif re.search(r'preface|civility guidelines',title,re.I):shape='rule_preface'
        reason=evidence(r'local\s+(?:court\s+)?rules|rules of (?:practice|court)|\bRULE\s+\d',head,'rule_topic')
        if not reason and official_rule_directory:reason={'field':'official_publisher_local_rule_directory','excerpt':url}
    elif court and re.search(r'standing order|administrative order|general order|notices orders',topic,re.I):
        kind='standing_order';shape='order_body' if binary or re.search(r'IT IS (?:HEREBY )?ORDERED',text,re.I) else 'order_index';reason=evidence(r'standing order|administrative order|general order',head,'order_topic')
    elif court and re.search(r'fee schedule|filing fees?|court fees?',topic,re.I):
        kind='fee_schedule';shape='fee_table' if re.search(r'\$\s*\d',text) else 'fee_information';reason=evidence(r'fee schedule|filing fees?|court fees?',head,'fee_topic')
    elif court and re.search(r'e.?filing|filing (?:instructions|procedures|requirements)|how to file|filing guide',topic,re.I):
        kind='filing_guidance';shape='guide';reason=evidence(r'e.?filing|filing (?:instructions|procedures|requirements)|how to file|filing guide',head,'filing_guidance_topic')
        landing=filing_guidance_landing(title,text,links) if not binary else None
        if landing:shape='filing_guidance_index';reason=landing
    elif court and ((re.search(r'\bforms?\b|petition|summons|motion',topic,re.I) and (re.search(r'/forms?(?:/|-)',url,re.I) or re.search(r'\bforms?\b|blank|template',title,re.I))) or
                    (binary and re.search(r'FOR COURT USE ONLY|ATTORNEY OR PARTY WITHOUT ATTORNEY',text[:2500],re.I) and re.search(r'\(Name|TELEPHONE NO|_{4,}',text[:2500],re.I))):
        kind='court_form';shape='form_document' if binary else 'form_directory';reason=evidence(r'forms?|petition|summons|motion',head,'form_topic')
        if binary and re.search(r'\bFILED\s+(?:\d{1,2}[/-]|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))',text[:800],re.I):
            kind='unknown';shape='possible_filed_pleading';reason={'excerpt':text[:800],'field':'filed_mark_requires_review'}
    elif court and (re.search(r'contact|phone|telephone|hours of operation|office hours|clerk',topic,re.I)):
        kind='court_contact';shape='contact_page';reason=evidence(r'contact|phone|telephone|hours of operation|office hours|clerk of court',head,'contact_topic')
    elif court and re.search(r'staff directory|court staff|judicial staff',topic,re.I):
        kind='court_staff';shape='staff_directory';reason=evidence(r'staff directory|court staff|judicial staff',head,'staff_topic')
    elif court:
        kind='court_information';shape='records_access_guidance' if re.search(r'\brecords\b',title,re.I) else 'information_page';reason=evidence(COURT.pattern,head,'court_context')
    elif re.search(r'county|government|law.?justice',title,re.I):kind='source_directory';shape='directory';reason={'excerpt':title,'field':'source_title'}
    if isinstance(reason,str):reason={'excerpt':title,'field':'exclusion','note':reason}
    if not reason:reason={'excerpt':title,'field':'insufficient_topic_evidence'}
    legal_status='unknown';status_evidence=[]
    if kind in {'local_rule','standing_order'}:
        repealed=re.search(r'(?im)^\s*(?!rule\b)(?:[A-Z][A-Z ()\d,$/–—-]+)\s*[–—-]\s*Repealed\s*$',text[:600])
        if repealed:
            legal_status='repealed_as_published';status_evidence=[{'field':'opening_document_caption','excerpt':repealed.group(0).strip(),'offset':repealed.start()}]
        elif re.search(r'\b(?:draft|proposed|request for comments|public comment)\b',title,re.I):
            legal_status='draft';status_evidence=[{'field':'document_title','excerpt':title}]
        elif shape in {'rule_body','order_body'}:
            match=re.search(r'(?im)^\s*(effective|adopted)(?:\s+(?:date|on|as of))?\s*[:\-]?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|\d)[^\n]{0,65}',text[:1800])
            if match:
                legal_status='effective_as_published' if match.group(1).lower()=='effective' else 'adopted'
                status_evidence=[{'field':'explicit_document_header_date_label','excerpt':match.group(0).strip(),'offset':match.start()}]
    return {'resource_type':kind,'document_shape':shape,'legal_status':legal_status,'legal_status_evidence':status_evidence,'status':'deterministic_content_classification' if kind!='unknown' else 'needs_review','substantive':shape in {'rule_body','order_body','fee_table','guide','form_document'},'evidence':[reason],'version':VERSION}
