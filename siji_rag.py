"""
siji_rag.py — RAG module untuk SIJI Bintaro autoreply
Queries ChromaDB v2 (siji_memory + siji_qa_history) via HTTP
Uses nomic-embed-text (dim 768) for embeddings via Ollama
"""
import httpx
import hashlib
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"
CHROMA_BASE = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"

COLLECTION_SOP = "siji_memory"
COLLECTION_QA  = "siji_qa_history"

# Cache collection IDs to avoid repeated lookups
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
        result = resp.json()
        return result.get("embeddings", [[]])[0]
    except Exception as e:
        print(f"[RAG] embed error: {e}")
        return None


def query_collection(collection_id: str, embedding: list, n_results: int = 3) -> dict:
    """Query ChromaDB collection with embedding vector"""
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
        return resp.json()
    except Exception as e:
        print(f"[RAG] query error: {e}")
        return {}


def find_context(query: str, threshold: float = 0.72) -> dict:
    """
    Main RAG function: embed query, search siji_memory + siji_qa_history
    Returns dict with sop_context, qa_context, qa_answer, best_score
    """
    result = {
        "sop_context": None,
        "qa_context": None,
        "qa_answer": None,
        "best_score": 0.0
    }

    embedding = embed_text(query)
    if not embedding:
        return result

    # --- Search SOP knowledge (siji_memory) ---
    sop_id = _get_collection_id(COLLECTION_SOP)
    if sop_id:
        sop_results = query_collection(sop_id, embedding, n_results=2)
        docs = sop_results.get("documents", [[]])[0]
        dists = sop_results.get("distances", [[]])[0]
        if docs and dists:
            score = 1 - dists[0]  # cosine distance → similarity
            if score > result["best_score"]:
                result["best_score"] = score
            if score >= threshold:
                result["sop_context"] = docs[0][:600]
            print(f"[RAG] SOP score: {score:.3f} | {docs[0][:60]}")

    # --- Search Q&A history (siji_qa_history) ---
    qa_id = _get_collection_id(COLLECTION_QA)
    if qa_id:
        qa_results = query_collection(qa_id, embedding, n_results=2)
        docs = qa_results.get("documents", [[]])[0]
        dists = qa_results.get("distances", [[]])[0]
        metas = qa_results.get("metadatas", [[]])[0]
        if docs and dists:
            score = 1 - dists[0]
            if score > result["best_score"]:
                result["best_score"] = score
            if score >= threshold:
                result["qa_context"] = docs[0][:300]
                answer = metas[0].get("answer", "") if metas else ""
                result["qa_answer"] = answer[:400] if answer else None
            print(f"[RAG] QA score: {score:.3f} | {docs[0][:60]}")

    return result
