import hashlib,json,tempfile,unittest
from pathlib import Path
import large_text_assets as subject


class LargeTextAssets(unittest.TestCase):
    def setUp(self):
        subject.BASE.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='large_text_test_',dir=subject.BASE)
        self.root=Path(self.temp.name).resolve()
        assert self.root.is_relative_to(subject.BASE.resolve())
        self.source=self.root/'core';self.source.mkdir();self.output=self.root/'assets'
        body='Caf\u00e9\x00\n'+'A'*80
        self.rows=[{'id':str(i),'dataset':'focused','category':'guidance','state':'California','title':'Test source',
                    'source_url':'https://example.gov/source','text':text,'detail':{}}
                   for i,text in enumerate((body,body,'tiny'))]
        self.write_source()
    def tearDown(self):
        assert self.root.is_relative_to(subject.BASE.resolve())
        self.temp.cleanup()
    def write_source(self):
        raw=b''.join(subject.line(row) for row in self.rows)
        (self.source/'focused.jsonl').write_bytes(raw)
        (self.source/'dataset.json').write_text(json.dumps({'complete':True,'status':'passed','datasets':[
            {'id':'focused','path':'focused.jsonl','rows':len(self.rows),'sha256':hashlib.sha256(raw).hexdigest()}]}),'utf-8')
        (self.source/'display_groups.jsonl').write_text(json.dumps({'id':'document:group','preferred_id':'0'})+'\n','utf-8')
    def test_exact_bytes_deduplicate_and_preferred_alias(self):
        receipt=subject.export(self.source,self.output,32)
        self.assertEqual((receipt['records'],receipt['unique_objects'],receipt['route_aliases']),(2,1,3))
        rows=[json.loads(line) for line in (self.output/'large_text_assets.jsonl').read_text('utf-8').splitlines()]
        self.assertEqual(rows[0]['text'],'')
        artifact=rows[0]['artifacts'][0]
        self.assertEqual(Path(artifact['local_path']).read_bytes(),self.rows[0]['text'].encode('utf-8'))
        self.assertTrue(any('document%3Agroup' in a['url'] for a in rows[0]['artifacts']))
        self.assertEqual(rows[0]['category'],'guidance')
    def test_changed_source_is_not_published(self):
        receipt=json.loads((self.source/'dataset.json').read_text('utf-8'))
        receipt['datasets'][0]['sha256']='0'*64
        (self.source/'dataset.json').write_text(json.dumps(receipt),'utf-8')
        with self.assertRaisesRegex(ValueError,'hash/count mismatch'):subject.export(self.source,self.output,32)
        self.assertFalse((self.output/'large_text_assets.jsonl').exists())
    def test_unclassified_record_fails_closed(self):
        self.rows[0]['category']='other';self.write_source()
        with self.assertRaisesRegex(ValueError,'Unclassified'):subject.export(self.source,self.output,32)

if __name__=='__main__':unittest.main()
