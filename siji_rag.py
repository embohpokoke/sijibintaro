"""
siji_rag.py — RAG module untuk SIJI Bintaro autoreply
Dual-collection search: siji_qa_history (synthetic Q&A) + siji_conv_patterns (real conversations)
Uses nomic-embed-text (dim 768) via Ollama
"""
import httpx
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"
CHROMA_BASE = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"

COLLECTION_QA       = "siji_qa_history"      # 1,210 synthetic Q&A pairs
COLLECTION_PATTERNS = "siji_conv_patterns"    # 1,304 real staff-customer pairs

# Cache collection IDs
_collection_ids: dict = {}


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


def embed_text(text: str) -> Optional[list]:
    """Embed text using nomic-embed-text via Ollama (dim 768)"""
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


def query_collection(collection_id: str, embedding: list, n_results: int = 2) -> list:
    """Query ChromaDB, return list of (score, doc, meta) sorted by score desc."""
    try:
        resp = httpx.post(
            f"{CHROMA_BASE}/collections/{collection_id}/query",
            json={
                "query_embeddings": [embedding],
                "n_results": n_results,
                "include": ["documents", "distances", "metadatas"]
            },
            timeout=10
        )
        r = resp.json()
        docs   = r.get("documents", [[]])[0]
        dists  = r.get("distances",  [[]])[0]
        metas  = r.get("metadatas",  [[]])[0]
        results = []
        for doc, dist, meta in zip(docs, dists, metas):
            results.append((1 - dist, doc, meta or {}))
        return sorted(results, key=lambda x: -x[0])
    except Exception as e:
        print(f"[RAG] query error: {e}")
        return []


def find_context(query: str, threshold: float = 0.75) -> dict:
    """
    Dual-collection RAG:
    1. siji_qa_history — synthetic Q&A (structured knowledge)
    2. siji_conv_patterns — real staff-customer conversations (tone + edge cases)

    Returns best match across both collections.
    Threshold 0.75 untuk Q&A (lebih presisi), 0.78 untuk conv_patterns (lebih banyak noise).
    """
    result = {
        "sop_context":  None,   # always None (SOP internal, tidak dipakai customer)
        "qa_context":   None,
        "qa_answer":    None,
        "conv_context": None,   # NEW: from siji_conv_patterns
        "best_score":   0.0,
        "source":       None,   # "qa" | "conv" | None
    }

    embedding = embed_text(query)
    if not embedding:
        return result

    best_score = 0.0
    best_source = None
    best_doc = None
    best_meta = {}

    # ── Search 1: siji_qa_history (synthetic, structured) ──
    qa_id = _get_collection_id(COLLECTION_QA)
    if qa_id:
        qa_hits = query_collection(qa_id, embedding, n_results=2)
        if qa_hits:
            score, doc, meta = qa_hits[0]
            print(f"[RAG] QA score: {score:.3f} | {doc[:60]}")
            if score > best_score:
                best_score, best_source, best_doc, best_meta = score, "qa", doc, meta

    # ── Search 2: siji_conv_patterns (real conversations) ──
    conv_id = _get_collection_id(COLLECTION_PATTERNS)
    if conv_id:
        conv_hits = query_collection(conv_id, embedding, n_results=2)
        if conv_hits:
            score, doc, meta = conv_hits[0]
            print(f"[RAG] CONV score: {score:.3f} | {doc[:60]}")
            if score > best_score:
                best_score, best_source, best_doc, best_meta = score, "conv", doc, meta

    result["best_score"] = round(best_score, 4)
    result["source"] = best_source

    if best_score >= threshold and best_doc:
        if best_source == "qa":
            result["qa_context"] = best_doc[:300]
            answer = best_meta.get("answer", "")
            result["qa_answer"] = answer[:400] if answer else None
        elif best_source == "conv":
            # Extract staff reply from real conversation pattern
            result["conv_context"] = best_doc[:400]
            staff_reply = best_meta.get("staff", "")
            # Use real staff reply as QA answer hint for LLM
            result["qa_answer"] = staff_reply[:400] if staff_reply else None
            result["qa_context"] = best_doc[:300]

    return result
