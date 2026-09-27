import contextlib, hashlib, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'delivery/archive-directory'))
import county_filing
import build_gap_enrichment_20260927 as builder

class BuildTests(unittest.TestCase):
    def test_ids_are_lane_scoped_and_distinct_evidence_is_retained(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'input';out=root/'output';index=root/'county';index.mkdir();(index/'county_sources.jsonl').write_text('')
            for lane in ['one','two']:
                folder=source/lane;folder.mkdir(parents=True);raw=folder/'a.txt';raw.write_text(lane)
                record={'id':'shared-id','title':lane,'source_url':'https://example.gov/'+lane,'raw_path':str(raw),'raw_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'resource_type':'rules'}
                (folder/'manifest.jsonl').write_text(json.dumps(record)+'\n')
                edges=[{'source':'shared-id','target':'mdl:1','relation':'cites_mdl','evidence':{'quote':str(page),'page':page}} for page in [1,2]]
                (folder/'edges.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in edges))
            with patch.multiple(builder,ROOT=root,INPUT=source,OUTPUT=out),patch.object(county_filing,'DATA',index),patch.object(county_filing,'_state',return_value={'counties':{}}),contextlib.redirect_stdout(io.StringIO()):builder.build()
            bundle=json.loads((out/'bundle.json').read_text());cites=[e for e in bundle['edges'] if e['relation']=='cites_mdl']
            self.assertEqual(len(bundle['resources']),2);self.assertEqual(len(cites),4);self.assertEqual(len({e['source'] for e in cites}),2)

    def test_held_document_relationships_are_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'input';out=root/'output';index=root/'county';index.mkdir();(index/'county_sources.jsonl').write_text('')
            lane=source/'one';lane.mkdir(parents=True)
            (lane/'manifest.jsonl').write_text(json.dumps({'id':'missing','title':'missing','source_url':'https://example.gov/missing','raw_path':'absent','raw_sha256':'0'*64})+'\n')
            (lane/'edges.jsonl').write_text(json.dumps({'source':'missing','target':'mdl:1','relation':'cites_mdl','evidence':{'quote':'MDL1'}})+'\n')
            with patch.multiple(builder,ROOT=root,INPUT=source,OUTPUT=out),patch.object(county_filing,'DATA',index),patch.object(county_filing,'_state',return_value={'counties':{}}),contextlib.redirect_stdout(io.StringIO()):builder.build()
            bundle=json.loads((out/'bundle.json').read_text());self.assertEqual(bundle['edges'],[]);self.assertEqual(bundle['summary']['held'],2)

if __name__=='__main__':unittest.main()
