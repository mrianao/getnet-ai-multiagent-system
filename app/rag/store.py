"""Embeddings + ChromaDB vector store + retriever."""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Protocol

import chromadb
import numpy as np
from chromadb.config import Settings as ChromaSettings

from app.config import Settings

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Local multilingual ONNX embeddings (no API, no GPU)."""

    def __init__(self, model: str, cache_dir: str):
        from fastembed import TextEmbedding  # lazy: heavy import

        self._m = TextEmbedding(model, cache_dir=cache_dir)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._m.embed(texts, batch_size=32)]


class VectorStore:
    def __init__(self, path: str | None, collection: str, embedder: Embedder):
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        cs = ChromaSettings(anonymized_telemetry=False, allow_reset=True)
        self._client = chromadb.PersistentClient(path=path, settings=cs) if path else chromadb.EphemeralClient(settings=cs)
        self._name = collection
        self._embedder = embedder
        self._col = self._client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})

    def count(self) -> int:
        return self._col.count()

    def reset(self) -> None:
        self._client.delete_collection(self._name)
        self._col = self._client.get_or_create_collection(self._name, metadata={"hnsw:space": "cosine"})

    def upsert(self, chunks: list[dict]) -> None:
        if not chunks:
            return
        for i in range(0, len(chunks), 64):
            batch = chunks[i : i + 64]
            ids = [hashlib.sha1(f"{c['url']}#{c['chunk']}#{c['text']}".encode()).hexdigest() for c in batch]
            self._col.upsert(
                ids=ids,
                documents=[c["text"] for c in batch],
                embeddings=self._embedder.embed([c["text"] for c in batch]),
                metadatas=[{"url": c["url"], "title": c["title"], "chunk": c["chunk"]} for c in batch],
            )

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Dense (vector) search. Returns hits with cosine similarity as `score`."""
        if self.count() == 0:
            return []
        res = self._col.query(query_embeddings=self._embedder.embed([query]), n_results=min(k, self.count()))
        out = []
        for id_, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            out.append({"id": id_, "text": doc, "url": meta["url"], "title": meta["title"], "score": round(1 - float(dist), 4)})
        return out

    def all_documents(self) -> dict:
        return self._col.get(include=["documents", "metadatas", "embeddings"])

    def embed_query(self, query: str) -> list[float]:
        return self._embedder.embed([query])[0]


class Retriever:
    """Hybrid retriever: dense top-N ∪ BM25 top-N, fused with Reciprocal Rank Fusion.

    `score` reported per hit is the cosine similarity (interpretable, used for the
    confidence threshold); ordering comes from RRF.
    """

    def __init__(self, store: VectorStore, top_k: int = 5, min_score: float = 0.35, candidates: int = 30):
        self.store, self.top_k, self.min_score, self.candidates = store, top_k, min_score, candidates
        self._bm25 = None
        self._docs: dict | None = None
        self._indexed_count = -1

    def _lexical_index(self):
        from app.rag.hybrid import BM25

        if self._bm25 is None or self._indexed_count != self.store.count():
            self._docs = self.store.all_documents()
            self._bm25 = BM25(self._docs["documents"])
            self._indexed_count = self.store.count()
        return self._bm25, self._docs

    def retrieve(self, query: str) -> list[dict]:
        from app.rag.hybrid import rrf

        if self.store.count() == 0:
            return []
        bm25, docs = self._lexical_index()
        qv = np.asarray(self.store.embed_query(query))
        embs = np.asarray(docs["embeddings"])
        cos = embs @ qv / (np.linalg.norm(embs, axis=1) * np.linalg.norm(qv) + 1e-9)
        dense_rank = [docs["ids"][i] for i in np.argsort(-cos)[: self.candidates]]
        lex = bm25.scores(query)
        lex_rank = [docs["ids"][i] for i in np.argsort(-np.asarray(lex))[: self.candidates] if lex[i] > 0]
        fused = rrf([dense_rank, lex_rank])
        idx = {id_: i for i, id_ in enumerate(docs["ids"])}
        hits = []
        for id_ in sorted(fused, key=fused.get, reverse=True):
            i = idx[id_]
            score = float(cos[i])
            if score < self.min_score:
                continue
            m = docs["metadatas"][i]
            hits.append({"id": id_, "text": docs["documents"][i], "url": m["url"], "title": m["title"],
                         "score": round(score, 4), "rrf": round(fused[id_], 5), "bm25": round(lex[i], 3)})
            if len(hits) == self.top_k:
                break
        return hits


def build_vector_store(s: Settings, embedder: Embedder | None = None) -> VectorStore:
    emb = embedder or FastEmbedder(s.embedding_model, s.embedding_cache_dir)
    return VectorStore(s.chroma_dir, s.chroma_collection, emb)
