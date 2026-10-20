import re
import threading

import jieba
from opencc import OpenCC

_converter = OpenCC('t2s')
_lock = threading.Lock()
IDENTITY = 'jieba-0.42.1-opencc-0.1.7-char100-v1'


def normalize(value):
    with _lock:
        return _converter.convert(value).lower()


def tokenize(value):
    value = normalize(value)
    with _lock:
        words = [w for w in jieba.cut(value) if re.search(r'[\w\u3400-\u9fff]', w)]
    # Add single Han characters for rare/classical terms missed by dictionary segmentation.
    words.extend(c for c in value if '\u3400' <= c <= '\u9fff')
    return ' '.join(words)


def chunks(value, size=100, overlap=15):
    start = 0
    while start < len(value):
        end = min(start + size, len(value))
        if end < len(value):
            candidates = [value.rfind(p, start+size//2, end) for p in ('。','；','\n','. ')]
            boundary = max(candidates)
            if boundary >= start+size//2:
                end = boundary + 1
        part = value[start:end]
        if part.strip():
            yield start, end, part
        if end == len(value):
            break
        start = max(start+1, end-overlap)
