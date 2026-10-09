"""Embeddings: modelo multilíngue via fastembed (ONNX) e um embedder determinístico para testes."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence

import numpy as np


class FastEmbedEmbedder:
    def __init__(self, model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", cache_dir: str | None = None,
                 threads: int | None = None) -> None:
        from fastembed import TextEmbedding

        self.name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=threads)
        self.dim = len(next(iter(self._model.embed(["dim"]))))
        self._e5 = "e5" in model_name.lower()

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        items = [f"passage: {t}" for t in texts] if self._e5 else list(texts)
        return np.vstack(list(self._model.embed(items))).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        item = f"query: {text}" if self._e5 else text
        return next(iter(self._model.embed([item]))).astype(np.float32)


class HashingEmbedder:
    """Embedder determinístico (bag-of-words hashing) para testes sem rede nem modelo."""

    def __init__(self, dim: int = 256) -> None:
        self.name = f"hashing-{dim}"
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 20) & 1 else -1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)
