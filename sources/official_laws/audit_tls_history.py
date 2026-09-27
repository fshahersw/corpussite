"""Identify historical responses affected by concurrent truststore warnings.

No historical file is re-downloaded by this audit. An isolated, verified TLS
GET and SHA-256 comparison is needed to clear a candidate conclusively.
"""
import json,re
from urllib.parse import urlparse
import collect_official_laws as c

hosts=set()
logs=[]
for path in c.ROOT.glob('*.log'):
    found=re.findall(r"Unverified HTTPS request is being made to host '([^']+)'",path.read_text(encoding='utf-8',errors='replace'))
    if found:
        hosts.update(found); logs.append(path.name)
rows=[]
for path in (c.ROOT/'metadata').glob('*.json'):
    d=json.loads(path.read_text(encoding='utf-8'))
    if urlparse(d['source_url']).netloc in hosts and 'tls_verification' not in d and d.get('bytes'):
        rows.append({'source_url':d['source_url'],'format':d.get('format'),'evidence_path':d.get('evidence_path'),'sha256':d.get('sha256'),'bytes':d.get('bytes'),'status':'content_hash_revalidation_needed','reason':'A historical log reports an unverified-TLS warning for this host. Concurrent output does not identify the exact response, so all prior responses on affected hosts are conservatively flagged.'})
report={'as_of_utc':c.now(),'historical_warning_hosts':sorted(hosts),'evidence_logs':logs,'affected_saved_responses':len(rows),'affected_pdf_responses':sum(r['format']=='pdf' for r in rows),'responses':rows,'fix':'Each new request session now has its own Windows system-trust SSL context and uses verify=True. Warnings remain enabled.','required_followup':'Use isolated verified TLS GETs to compare SHA-256 for these responses; preserve originals and report mismatches. This audit performs no blanket re-download.'}
c.savejson(c.ROOT/'indexes'/'historical_tls_audit.json',report)
c.csvwrite(c.ROOT/'indexes'/'historical_tls_audit.csv',rows,['source_url','format','evidence_path','sha256','bytes','status','reason'])
print(json.dumps({k:v for k,v in report.items() if k!='responses'},indent=2))
