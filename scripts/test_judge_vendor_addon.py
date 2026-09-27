"""Offline failures that could misattribute or fabricate vendor measures."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('vendor_builder',Path(__file__).with_name('build_judge_vendor_addon.py'))
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
folder=b.ROOT/'reports/judges/vendor_probe_20260914/builder_fixtures'
folder.mkdir(parents=True,exist_ok=True)
raw=b'<h1>Example Judge</h1>\r\n<p>Example Judge was assigned 2,276 cases.</p>'
html=folder/'example.html';html.write_bytes(raw)
other=folder/'unowned.html';other.write_bytes(raw)
decoded=raw.decode().replace('\r\n','\n');start=decoded.index('<p>');fragment=decoded[start:]
ev={'source_path':b.rel(html),'source_sha256':b.sha(html),'html_character_start':start,'html_character_end':len(decoded),
    'html_fragment_sha256':b.hash_text(fragment),'literal_text':'Example Judge was assigned 2,276 cases.',
    'raw_byte_start':raw.index(b'<p>'),'raw_byte_end':len(raw)}
obs={'subject_type':'judge','record_class':'vendor_published'}
owned={b.rel(html):b.sha(html)}
base={'analysis_id':'a1','source_observation_id':'j1','analysis_type':'vendor_reported_count','metric':'assigned_cases',
      'metric_subject_type':'judge','label':'Assigned cases','value':2276,'unit':'cases','source_reported':True,
      'period':None,'cohort':None,'numerator':None,'denominator':None,'methodology_url':None,'evidence':ev}
checks=[]
assert b.plain('<span>2021 - Present</span><span>Chief Judge</span><span>United States</span>')=='2021 - Present Chief Judge United States'
checks.append({'check':'Adjacent visible column text nodes stay separated','passed':True})

def check_reject(label, action):
    try:action()
    except (ValueError,KeyError,IndexError,UnicodeError):checks.append({'check':label,'passed':True});return
    raise AssertionError('Unexpectedly accepted '+label)

assert b.validate_analysis(base,obs,b.EvidenceValidator(),owned)==ev['literal_text']
checks.append({'check':'LF character evidence with independent original CRLF byte spans','passed':True})
wrapped=folder/'embedded.json'
wrapped.write_text(json.dumps({'tool_result':{'content':[{'text':json.dumps({'html':fragment})}]}}),encoding='utf-8')
embedded={**ev,'source_path':b.rel(wrapped),'source_sha256':b.sha(wrapped),'json_pointer':'/tool_result/content/0/text',
          'embedded_json_pointer':'/html','html_character_start':0,'html_character_end':len(fragment)}
embedded.pop('raw_byte_start');embedded.pop('raw_byte_end')
assert b.EvidenceValidator().validate(embedded,{b.rel(wrapped):b.sha(wrapped)})==ev['literal_text']
checks.append({'check':'Explicit double-encoded JSON HTML evidence','passed':True})
entity_html=folder/'entity.html';entity_html.write_text('<p>Price, Postel &amp; Parma</p>',encoding='utf-8')
entity_text=entity_html.read_text(encoding='utf-8')
entity_ev={'source_path':b.rel(entity_html),'source_sha256':b.sha(entity_html),'start':0,'end':len(entity_text),'quote':entity_text,
           'evidence_format':'literal_html_fragment','fragment_sha256':b.hash_text(entity_text),'literal_text':'Price, Postel & Parma'}
assert b.EvidenceValidator().validate(entity_ev,{b.rel(entity_html):b.sha(entity_html)})=='Price, Postel & Parma'
checks.append({'check':'Declared HTML literal preserves exact fragment and decodes ampersand for claim comparison','passed':True})
check_reject('declared HTML wrong rendered value',lambda:b.EvidenceValidator().validate({**entity_ev,'literal_text':'Different law firm'},{b.rel(entity_html):b.sha(entity_html)}))
check_reject('unowned original',lambda:b.EvidenceValidator().validate({**ev,'source_path':b.rel(other)},owned))
check_reject('wrong original hash',lambda:b.EvidenceValidator().validate({**ev,'source_sha256':'0'*64},{b.rel(html):'0'*64}))
check_reject('wrong literal span',lambda:b.EvidenceValidator().validate({**ev,'html_character_start':start+1},owned))
check_reject('wrong rendered literal',lambda:b.EvidenceValidator().validate({**ev,'literal_text':'Example Judge was assigned 9,999 cases.'},owned))
check_reject('invented numeric value',lambda:b.validate_analysis({**base,'value':2277},obs,b.EvidenceValidator(),owned))
check_reject('court measure on a judge',lambda:b.validate_analysis({**base,'metric_subject_type':'court'},obs,b.EvidenceValidator(),owned))
check_reject('preview promoted to vendor published',lambda:b.validate_analysis({**base,'record_class':'vendor_published'},{**obs,'record_class':'public_ui_preview'},b.EvidenceValidator(),owned))
check_reject('independently computed ratio',lambda:b.validate_analysis({**base,'independently_computed':True},obs,b.EvidenceValidator(),owned))
check_reject('formula derived outcome measure',lambda:b.validate_analysis({**base,'formula':'wins/total'},obs,b.EvidenceValidator(),owned))
check_reject('unsupported denominator',lambda:b.validate_analysis({**base,'denominator':100},obs,b.EvidenceValidator(),owned))
check_reject('scale used as respondent denominator',lambda:b.validate_analysis({**base,'scale_maximum':2276,'denominator':2276},obs,b.EvidenceValidator(),owned))
check_reject('path escape',lambda:b.safe('../outside-fixture.html'))
assert base['period'] is None and base['cohort'] is None and base['numerator'] is None and base['denominator'] is None
assert b.canonical('context','a:b') != b.canonical('context','a%3Ab')
checks.append({'check':'Missing context stays null and namespace encoding does not collide','passed':True})
receipt={'validated':True,'checks':checks,'check_count':len(checks),'builder_sha256':b.sha(Path(b.__file__)),
         'query_sha256':b.sha(b.ROOT/'scripts/query_judge_vendor_addon.py'),'schema_sha256':b.sha(b.BASE/'SCHEMA.md'),
         'network_requests':0,'errors':[]}
b.write(b.ROOT/'reports/judges/vendor_probe_20260914/builder_test_results.json',receipt)
print(json.dumps({'validated':True,'checks':len(checks),'builder_sha256':receipt['builder_sha256']},indent=2))
