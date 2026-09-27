"""Save one literally observed public Trellis sample report, without credentials."""
import datetime
import hashlib
import json
import io
from pathlib import Path
import urllib.request
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'sources/judges/focus_20260913/report_sample'
PARENT = 'https://trellis.law/judge-dashboard/terry.l.bannon/case-milestone/preview'
URL = 'https://static-web-assets.vuean5bcdnek.trellis.law/static-assets/v2026-09-11T00-13-37Z-master-0c2e55e/marketing/pdf/trellis_judge_report_sample.pdf'
TEXT = '''Judge Terry L. Bannon: Professional Background and Legal Expertise
Track Judge's New Cases
Generate Report
Cochise County Superior Court, Department Division VI
Bio
At a Glance
NEW
Motions
NEW
Outcome
NEW
Case Milestones
NEW
Judge Terry L. Bannon: Professional Background and Legal Expertise

Gain valuable insights on timing and duration of important milestones.How long do cases sit before the judge? How long do they take to movea case all the way through trial? What’s the average time a case sitsbefore settlement or dismissal?Help set client expectations and make better staffing decisions basedon in depth timing analysis.

Download a Sample Report
Request Access Now Request a Demo'''

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    parent = {'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'capture_kind': 'literal visible DOM text read using supported Codex browser API; not raw HTTP',
        'source_url': PARENT, 'title': 'Judge Terry L. Bannon - Professional Background & Legal Expertise | Trellis.Law',
        'selector': '#bodyWrapper', 'text': TEXT, 'text_sha256': hashlib.sha256(TEXT.encode()).hexdigest(),
        'sample_link': {'label': 'Download a Sample Report', 'href': URL},
        'generate_report_disabled': True,
        'account_access': 'Browser is signed in; full Judge Analytics prompts for an additional80USD/month subscription',
        'numeric_judge_analytics_visible': False, 'credentials_or_cookies_exported': False}
    (OUT / 'preview_visible_dom.json').write_text(json.dumps(parent, indent=2) + '\n', encoding='utf-8')
    with urllib.request.urlopen(urllib.request.Request(URL, headers={'User-Agent': 'LegalCorpusResearch/1.0'}), timeout=60) as response:
        assert response.geturl() == URL, 'Unexpected redirect; review before proceeding'
        data = response.read(30 * 1024 * 1024 + 1)
        assert len(data) <= 30 * 1024 * 1024 and data.startswith(b'%PDF-')
        status = response.status
        content_type = response.headers.get('Content-Type')
    pdf = OUT / 'trellis_judge_report_sample.pdf'
    if pdf.exists():
        assert pdf.read_bytes() == data, 'Keep the already saved original unchanged'
    else:
        pdf.write_bytes(data)
    doc = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or '' for page in doc.pages]
    text = '\n\n'.join(pages)
    (OUT / 'trellis_judge_report_sample.txt').write_text(text, encoding='utf-8')
    receipt = {'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source_url': URL, 'parent_url': PARENT, 'literal_parent_link_label': 'Download a Sample Report',
        'http_status': status, 'content_type': content_type, 'bytes': len(data),
        'raw_sha256': hashlib.sha256(data).hexdigest(), 'pages': len(pages),
        'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text_characters': len(text),
        'document_class': 'publisher marketing sample report; not a newly acquired report for Terry L. Bannon',
        'analytics_promotion': 'No sample values added to actual-judge metrics; illustrative report content is separate',
        'verified_tls': True, 'credentials_sent': False}
    (OUT / 'capture.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt))

if __name__ == '__main__':
    main()
