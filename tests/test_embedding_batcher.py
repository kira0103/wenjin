"""Offline regression test for cross-document embedding batching."""
import threading
import time
import unittest

from backend.knowledge.embedding_batcher import EmbeddingBatcher

CONFIG = {
    "model": "intfloat/multilingual-e5-large", "base_url": "", "provider": "local",
    "revision": "", "batch_size": 16, "dimensions": 1024,
    "api_key_env": "SHIXUE_EMBEDDING_API_KEY", "query_prefix": "query: ",
    "credential_id": None, "document_prefix": "passage: ", "send_dimensions": False,
    "timeout_seconds": 60,
}


class EmbeddingBatcherTest(unittest.TestCase):
    def test_tiny_requests_share_one_call_without_mixing_results(self):
        batcher = EmbeddingBatcher()
        batcher.worker_count = 1
        batcher.max_texts = 32
        batcher.window_seconds = 0.2
        seen = []

        def fake_embed(root, texts):
            seen.append(len(texts))
            return ([([float(index)] * 4) for index, _ in enumerate(texts)], 'fake-model')

        results = {}

        def submit(index):
            texts = [f'{index}-{item}' for item in range(4)]
            results[index] = batcher.submit('/tmp/batcher-test', CONFIG, texts, fake_embed).result(timeout=5)

        threads = [threading.Thread(target=submit, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(seen, [16])
        self.assertEqual({key: len(value[0]) for key, value in results.items()}, {0: 4, 1: 4, 2: 4, 3: 4})
        self.assertTrue(all(value[1] == 'fake-model' for value in results.values()))


if __name__ == '__main__':
    unittest.main()
