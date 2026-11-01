"""Download orchestration without external requests or real model weights."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from backend.knowledge import embedding_download as download
from backend.knowledge.embedding_config import EmbeddingOptions
from backend.knowledge.worker import Cancelled


class DownloadRules(unittest.TestCase):
    def test_download_repairs_incomplete_files_and_reuses_complete_cache(self):
        from backend.knowledge.embedding_cache import inventory
        profile=EmbeddingOptions().model_dump(mode='json')
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=False):
            root=Path(folder);cache=root/'models';os.environ['SHIXUE_KB_MODEL_CACHE']=str(cache)
            repo=cache/'models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q';snapshot=repo/'snapshots'/('b'*40)
            snapshot.mkdir(parents=True);(repo/'refs').mkdir();(repo/'refs'/'main').write_text('b'*40)
            (snapshot/'model_optimized.onnx').write_bytes(b'weights')
            def fetch(**kwargs):
                (snapshot/'tokenizer.json').write_text('{}');(snapshot/'tokenizer_config.json').write_text('{}')
                return str(snapshot)
            with patch('huggingface_hub.snapshot_download',side_effect=fetch) as network:
                download._populate_cache(root,profile)
                self.assertFalse(network.call_args.kwargs['force_download'])
                self.assertEqual(inventory(root,[profile])[profile['model']]['state'],'available')
                download._populate_cache(root,profile);self.assertEqual(network.call_count,1)
                (repo/'files_metadata.json').write_text(json.dumps({'snapshots/'+('b'*40)+'/model_optimized.onnx':{'size':100}}))
                download._populate_cache(root,profile)
                self.assertTrue(network.call_args.kwargs['force_download'])
    def test_child_verifies_dimension_before_reporting_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);request=root/'request.json';result=root/'result.json';(root/'heartbeat').touch()
            request.write_text(json.dumps({'root':str(root),'cache':str(root/'models'),'config':EmbeddingOptions().model_dump(mode='json')}))
            with patch.dict(os.environ,{},clear=False),patch.object(download,'_populate_cache'),patch('backend.knowledge.models.embedding_model'),patch('backend.knowledge.models.embed',return_value=([[1.]+[0.]*383],'verified-key')):
                self.assertEqual(download.child_main(request,result),0)
                self.assertEqual(json.loads(result.read_text())['result']['dimensions'],384)
            with patch.dict(os.environ,{},clear=False),patch.object(download,'_populate_cache'),patch('backend.knowledge.models.embedding_model'),patch('backend.knowledge.models.embed',return_value=([[1.]],'bad-key')):
                self.assertEqual(download.child_main(request,result),1)
                self.assertFalse(json.loads(result.read_text())['ok'])

    def test_supervisor_publishes_only_verified_child_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stages=[]
            script="import json,sys,time;from pathlib import Path;p=Path(sys.argv[1]);(p.parent/'stage.json').write_text(json.dumps({'stage':'verify_embedding'}));time.sleep(.6);p.write_text(json.dumps({'ok':True,'result':{'model_key':'child-key','dimensions':384}}))"
            with patch.object(download,'_command',side_effect=lambda request,result:[sys.executable,'-c',script,str(result)]):
                result=download.prepare(root,EmbeddingOptions().model_dump(mode='json'),lambda:None,lambda *args:stages.append(args))
            self.assertEqual(result['model_key'],'child-key');self.assertIn(('verify_embedding',90),stages)
            self.assertFalse(any((root/'private'/'model-preparation').iterdir()))

    def test_cancelling_job_terminates_its_download_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);children=[];checks=0;real_popen=subprocess.Popen
            def launch(*args,**kwargs):
                child=real_popen(*args,**kwargs)
                if args[0][0]==sys.executable:children.append(child)
                return child
            def check():
                nonlocal checks
                checks+=1
                if checks>=3:raise Cancelled()
            with patch.object(download,'_command',return_value=[sys.executable,'-c','import time;time.sleep(60)']),patch.object(download.subprocess,'Popen',side_effect=launch):
                with self.assertRaises(Cancelled):download.prepare(root,EmbeddingOptions().model_dump(mode='json'),check,lambda *args:None)
            self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
            self.assertFalse(any((root/'private'/'model-preparation').iterdir()))

    def test_child_failure_keeps_existing_model_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'models').mkdir();old=root/'models'/'existing-model.onnx';old.write_bytes(b'existing weights')
            with patch.object(download,'_command',return_value=[sys.executable,'-c','raise SystemExit(1)']):
                with self.assertRaises(HTTPException):download.prepare(root,EmbeddingOptions().model_dump(mode='json'),lambda:None,lambda *args:None)
            self.assertEqual(old.read_bytes(),b'existing weights')
