import os,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from backend.knowledge import inference_runtime as runtime

class InferenceRuntimeTests(unittest.TestCase):
    def test_gpu_selection_and_cpu_override_do_not_change_the_vector_model(self):
        ort=Mock();ort.get_available_providers.return_value=['CUDAExecutionProvider','CPUExecutionProvider']
        with patch.dict('sys.modules',{'onnxruntime':ort}),patch.dict(os.environ,{'SHIXUE_EMBEDDING_DEVICE':'auto','SHIXUE_EMBEDDING_THREADS':'8'}),patch.object(runtime,'activate'):
            options,provider=runtime.options();self.assertEqual(provider,'CUDAExecutionProvider');self.assertEqual(options['threads'],8)
            self.assertEqual(options['providers'][0][1]['gpu_mem_limit'],6*1024**3)
        with patch.dict('sys.modules',{'onnxruntime':ort}),patch.dict(os.environ,{'SHIXUE_EMBEDDING_DEVICE':'cpu'}),patch.object(runtime,'activate'):
            options,provider=runtime.options();self.assertEqual(provider,'CPUExecutionProvider');self.assertEqual(options['providers'],['CPUExecutionProvider'])

    def test_record_does_not_replace_another_workers_device_state(self):
        import json
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'worker-status.json').write_text(json.dumps({'pid':-1}))
            runtime.record(root,'model',Mock(),8)
            self.assertFalse((root/'embedding-execution.json').exists())
