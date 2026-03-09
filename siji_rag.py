"""
siji_rag.py — Hybrid RAG untuk SIJI Bintaro autoreply
Strategy: Vector search (nomic-embed-text) + BM25 keyword → RRF re-ranking

Collections:
  siji_qa_history    — 1,210 synthetic Q&A pairs
  siji_conv_patterns — 1,304 real staff-customer conversations

Hybrid approach (per PDF best practice):
  1. Vector search: cosine similarity via ChromaDB
  2. BM25 keyword search: exact + partial term matching
  3. Reciprocal Rank Fusion (RRF): merge rankings → best of both worlds
"""
import httpx
import threading
from typing import Optional
from rank_bm25 import BM25Okapi

OLLAMA_BASE = "http://localhost:11434"
CHROMA_BASE = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"

COLLECTION_QA       = "siji_qa_history"
COLLECTION_PATTERNS = "siji_conv_patterns"

RRF_K = 60          # RRF constant (standard value)
TOP_N = 4           # docs per collection per method
HYBRID_THRESHOLD = 0.015  # minimum RRF score to use context (tuned)

# ── Collection ID cache ────────────────────────────────────────────────────
_collection_ids: dict = {}
_bm25_lock = threading.Lock()

# ── BM25 corpus (loaded once at startup) ──────────────────────────────────
_bm25_corpus: dict = {}   # {collection_name: {"bm25": BM25Okapi, "docs": [...], "metas": [...]}}


def _get_collection_id(name: str) -> Optional[str]:
    if name in _collection_ids:
        return _collection_ids[name]
    try:
        resp = httpx.get(f"{CHROMA_BASE}/collections", timeout=5)
        for col in resp.json():
            _collection_ids[col["name"]] = col["id"]
        return _collection_ids.get(name)
    except Exception as e:
        print(f"[RAG] get_collection_id error: {e}")
        return None


def _load_bm25_corpus(collection_name: str, max_docs: int = 2000):
    """Load all documents from ChromaDB collection → build BM25 index."""
    cid = _get_collection_id(collection_name)
    if not cid:
        return
    try:
        resp = httpx.post(
            f"{CHROMA_BASE}/collections/{cid}/get",
            json={"limit": max_docs, "include": ["documents", "metadatas"]},
            timeout=30
        )
        data = resp.json()
        docs   = data.get("documents", []) or []
        metas  = data.get("metadatas", []) or []
        ids    = data.get("ids", []) or []

        # Tokenize for BM25 (lowercase, split on whitespace + punctuation)
        import re
        tokenized = [re.sub(r'[^\w\s]', ' ', d.lower()).split() for d in docs]
        bm25 = BM25Okapi(tokenized)

        with _bm25_lock:
            _bm25_corpus[collection_name] = {
                "bm25":  bm25,
                "docs":  docs,
                "metas": metas,
                "ids":   ids,
            }
        print(f"[RAG] BM25 index loaded: {collection_name} ({len(docs)} docs)")
    except Exception as e:
        print(f"[RAG] BM25 load error ({collection_name}): {e}")


def warmup_bm25():
    """Pre-load BM25 indexes for both collections. Call at startup."""
    print("[RAG] Loading BM25 indexes...")
    _load_bm25_corpus(COLLECTION_QA)
    _load_bm25_corpus(COLLECTION_PATTERNS)
    print("[RAG] BM25 ready.")


def embed_text(text: str) -> Optional[list]:
    """Embed via nomic-embed-text (dim 768)."""
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": "nomic-embed-text", "input": text},
            timeout=15
        )
        return resp.json().get("embeddings", [[]])[0]
    except Exception as e:
        print(f"[RAG] embed error: {e}")
        return None


def _vector_search(collection_name: str, embedding: list, n: int = TOP_N) -> list:
    """Vector search via ChromaDB. Returns [(score, doc, meta), ...]"""
    cid = _get_collection_id(collection_name)
    if not cid:
        return []
    try:
        resp = httpx.post(
            f"{CHROMA_BASE}/collections/{cid}/query",
            json={
                "query_embeddings": [embedding],
                "n_results": n,
                "include": ["documents", "distances", "metadatas"]
            },
            timeout=10
        )
        r = resp.json()
        docs  = r.get("documents", [[]])[0]
        dists = r.get("distances",  [[]])[0]
        metas = r.get("metadatas",  [[]])[0]
        return [(1 - d, doc, meta or {}) for doc, d, meta in zip(docs, dists, metas)]
    except Exception as e:
        print(f"[RAG] vector search error: {e}")
        return []


def _bm25_search(collection_name: str, query: str, n: int = TOP_N) -> list:
    """BM25 keyword search. Returns [(score_norm, doc, meta), ...]"""
    import re
    with _bm25_lock:
        corpus = _bm25_corpus.get(collection_name)
    if not corpus:
        return []
    try:
        tokens = re.sub(r'[^\w\s]', ' ', query.lower()).split()
        scores = corpus["bm25"].get_scores(tokens)
        # Get top-n indices
        top_idx = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]
        # Normalize scores 0-1
        max_score = scores[top_idx[0]] if top_idx and scores[top_idx[0]] > 0 else 1
        results = []
        for idx in top_idx:
            if scores[idx] <= 0:
                continue
            norm_score = scores[idx] / max_score
            results.append((norm_score, corpus["docs"][idx], corpus["metas"][idx]))
        return results
    except Exception as e:
        print(f"[RAG] BM25 search error: {e}")
        return []


def _rrf_merge(vector_hits: list, bm25_hits: list, k: int = RRF_K) -> list:
    """
    Reciprocal Rank Fusion — combines two ranked lists.
    RRF(d) = Σ 1/(k + rank_i)
    Returns merged list sorted by RRF score desc: [(rrf_score, doc, meta)]
    """
    scores: dict = {}   # doc → rrf_score
    doc_map: dict = {}  # doc → meta

    for rank, (_, doc, meta) in enumerate(vector_hits, 1):
        scores[doc] = scores.get(doc, 0) + 1 / (k + rank)
        doc_map[doc] = meta

    for rank, (_, doc, meta) in enumerate(bm25_hits, 1):
        scores[doc] = scores.get(doc, 0) + 1 / (k + rank)
        doc_map[doc] = meta

    merged = sorted(scores.items(), key=lambda x: -x[1])
    return [(score, doc, doc_map[doc]) for doc, score in merged]


def find_context(query: str, threshold: float = 0.75) -> dict:
    """
    Hybrid RAG: Vector + BM25 → RRF merge → return best context for LLM.

    threshold parameter kept for backward compat but internal logic uses
    HYBRID_THRESHOLD on RRF score (different scale from pure cosine).
    """
    result = {
        "sop_context":  None,
        "qa_context":   None,
        "qa_answer":    None,
        "conv_context": None,
        "best_score":   0.0,
        "source":       None,
    }

    embedding = embed_text(query)
    if not embedding:
        return result

    best_rrf   = 0.0
    best_doc   = None
    best_meta  = {}
    best_src   = None

    for col_name in [COLLECTION_QA, COLLECTION_PATTERNS]:
        # Vector search
        vec_hits  = _vector_search(col_name, embedding, n=TOP_N)
        # BM25 keyword search
        bm25_hits = _bm25_search(col_name, query, n=TOP_N)
        # RRF merge
        merged = _rrf_merge(vec_hits, bm25_hits)

        # Log top result
        if merged:
            top_rrf, top_doc, _ = merged[0]
            vec_score = vec_hits[0][0] if vec_hits else 0
            bm25_score = bm25_hits[0][0] if bm25_hits else 0
            print(f"[RAG] {col_name[:15]} | vec={vec_score:.3f} bm25={bm25_score:.3f} rrf={top_rrf:.4f} | {top_doc[:55]}")

            if top_rrf > best_rrf:
                best_rrf  = top_rrf
                best_doc  = top_doc
                best_meta = merged[0][2]
                best_src  = "qa" if col_name == COLLECTION_QA else "conv"

    result["best_score"] = round(best_rrf, 4)
    result["source"]     = best_src

    # Use threshold as relative to max possible RRF score
    # At rank 1 from both: 1/(60+1) + 1/(60+1) ≈ 0.0328 (max)
    # Translate old threshold 0.75 cosine → ~0.020 RRF (conservative)
    rrf_threshold = 0.018

    if best_rrf >= rrf_threshold and best_doc:
        result["qa_context"] = best_doc[:300]
        if best_src == "qa":
            answer = best_meta.get("answer", "")
            result["qa_answer"] = answer[:400] if answer else None
        elif best_src == "conv":
            result["conv_context"] = best_doc[:400]
            staff_reply = best_meta.get("staff", "")
            result["qa_answer"] = staff_reply[:400] if staff_reply else None

    return result
