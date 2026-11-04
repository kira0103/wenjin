"""Coalesce embedding work from concurrent document jobs into larger GPU calls.

Each chunk is still embedded independently (the model never sees two documents as
one sequence); only the number of texts handed to one ONNX call changes. This
fills otherwise half-empty batches from small documents without mixing text or
altering chunk/vector ownership.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from concurrent.futures import Future


class EmbeddingBatcher:
    def __init__(self):
        # 32 texts = two complete internal ONNX batches (the model batch size is
        # 16). Waiting only makes sense for requests that are smaller than one
        # internal batch; normal 32-text requests run immediately so the batch
        # window cannot create idle gaps between GPU bursts.
        self.max_texts = max(16, int(os.environ.get('SHIXUE_EMBED_BATCH_TEXTS', '32')))
        self.window_seconds = max(0.0, int(os.environ.get('SHIXUE_EMBED_BATCH_WINDOW_MS', '50')) / 1000.0)
        self.worker_count = max(1, min(4, int(os.environ.get('SHIXUE_EMBED_WORKERS', '2'))))
        self.requests = queue.Queue()
        self._threads = []
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if len(self._threads) == self.worker_count and all(thread.is_alive() for thread in self._threads):
                return
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            for index in range(len(self._threads), self.worker_count):
                thread = threading.Thread(target=self._loop, daemon=True, name=f'embedding-batcher-{index}')
                thread.start()
                self._threads.append(thread)

    def submit(self, root, config, texts, embed_callable):
        """Queue the text list and return a Future without blocking the caller."""
        future = Future()
        key = str(root) + '|' + json.dumps(config, sort_keys=True, ensure_ascii=False)
        self.start()
        self.requests.put((root, key, dict(config), list(texts), future, embed_callable))
        return future

    def _loop(self):
        from .models import embedding_context
        while True:
            try:
                first = self.requests.get(timeout=1.0)
            except queue.Empty:
                continue
            batch = [first]
            key = first[1]
            count = len(first[3])
            deferred = []
            # A request that already fills at least one model batch should not
            # wait: waiting would only add an idle gap. Tiny documents still
            # wait briefly so several of them can share one full GPU batch.
            deadline = (time.monotonic() + self.window_seconds) if count < 16 else None
            while deadline is not None and count < self.max_texts:
                timeout = max(0.0, deadline - time.monotonic())
                try:
                    item = self.requests.get(timeout=timeout)
                except queue.Empty:
                    break
                if item[1] != key or count + len(item[3]) > self.max_texts:
                    deferred.append(item)
                    break
                batch.append(item)
                count += len(item[3])
            for item in deferred:
                self.requests.put(item)
            root, _key, config, _texts, _future, embed_callable = first
            values, model_key = [], ''
            try:
                merged = [text for item in batch for text in item[3]]
                with embedding_context(config, 'document'):
                    values, model_key = embed_callable(root, merged)
                offset = 0
                for item in batch:
                    size = len(item[3])
                    item[4].set_result((values[offset:offset + size], model_key))
                    offset += size
            except Exception as error:
                # Isolate a bad input instead of failing every document sharing the batch.
                for item in batch:
                    try:
                        with embedding_context(item[2], 'document'):
                            vectors, key_for_item = embed_callable(item[0], item[3])
                        item[4].set_result((vectors, key_for_item))
                    except Exception as item_error:
                        item[4].set_exception(item_error)


embedding_batcher = EmbeddingBatcher()
