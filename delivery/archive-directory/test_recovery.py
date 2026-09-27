import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import recovery

class RecoveryChecks(unittest.TestCase):
    def test_pilot_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(recovery,'FOLDER',Path(tmp)):
            (Path(tmp)/'validation.json').write_text('{"status":"pilot_passed"}')
            self.assertEqual(recovery.load(),{})
    def test_verified_text_hash_and_path_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'recovery';folder.mkdir();(folder/'text.txt').write_text('Rule 7. Twenty-one days.',encoding='utf-8')
            value={'id':'source1','status':'recovered','text_path':'recovery/text.txt','text_sha256':hashlib.sha256((folder/'text.txt').read_bytes()).hexdigest()}
            (folder/'metadata.json').write_text('{}')
            value.update(metadata_path='recovery/metadata.json',metadata_sha256=hashlib.sha256(b'{}').hexdigest())
            (folder/'resources.jsonl').write_text(json.dumps(value)+'\n')
            (folder/'validation.json').write_text(json.dumps({'status':'passed','overlay_ready':True,'resources_sha256':hashlib.sha256((folder/'resources.jsonl').read_bytes()).hexdigest()}))
            with patch.object(recovery,'ROOT',root),patch.object(recovery,'FOLDER',folder):
                self.assertEqual(recovery.load()['source1']['id'],'source1')
                (folder/'text.txt').write_text('changed')
                with self.assertRaises(ValueError):recovery.load()
    def test_overlay_preserves_original_and_checks_parent_identity(self):
        original={'id':'a','sha256':'parent','text_path':None,'quality':{'native_text':'missing'},'metadata':{'currency':'unknown'}}
        rec={'id':'a','raw_sha256':'parent','text_path':'pass/text.txt','text_sha256':'body','method':'word_com_native'}
        output=recovery.overlay(original,{'a':rec})
        self.assertIsNone(original['text_path']);self.assertEqual(output['metadata']['currency'],'unknown')
        self.assertIsNone(output['metadata']['prior_text_representation']['text_path'])
        self.assertEqual(output['metadata']['text_recovery']['method'],'word_com_native')
        with self.assertRaises(ValueError):recovery.overlay(original,{'a':dict(rec,raw_sha256='other')})

if __name__=='__main__':unittest.main(verbosity=2)
