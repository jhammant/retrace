"""A dependency-free, fully local text embedding for platforms without a system one.

macOS ships NaturalLanguage sentence embeddings; Windows has no built-in
equivalent, and downloading a neural model would break the on-device, no-network
promise. This hashes words and character trigrams into a fixed-size vector (the
"hashing trick"). It is lexical rather than truly semantic: it finds typos,
inflections and partial words ("deploy" ~ "deployment"), not synonyms. Hybrid
search still blends it with full-text ranking.
"""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter

import numpy as np

DIM = 384
MODEL = f"hash-ngram-{DIM}"
_MAX_TOKENS = 4000
_WORD = re.compile(r"\w+", re.UNICODE)


def _slot(feature: str) -> tuple[int, float]:
    h = zlib.crc32(feature.encode("utf-8"))  # stable across processes, unlike hash()
    return h % DIM, (1.0 if (h >> 16) & 1 else -1.0)


def embed(text: str) -> dict:
    """Same JSON shape as the macOS ``retrace-embed`` helper."""
    tokens = _WORD.findall((text or "").lower())[:_MAX_TOKENS]
    if not tokens:
        return {"ok": False, "error": "empty text"}
    features: Counter[str] = Counter()
    for tok in tokens:
        features["w:" + tok] += 2
        padded = f"#{tok}#"
        for i in range(len(padded) - 2):
            features["c:" + padded[i:i + 3]] += 1
    vec = np.zeros(DIM, dtype=np.float32)
    for feature, count in features.items():
        idx, sign = _slot(feature)
        vec[idx] += sign * (1.0 + math.log(count))  # damp repeated boilerplate
    norm = float(np.linalg.norm(vec))
    if norm < 1e-9:
        return {"ok": False, "error": "no features"}
    vec /= norm
    return {"ok": True, "dim": DIM, "model": MODEL, "vec": vec.tolist()}
