"""
populate_qa_chroma.py — Populate ChromaDB siji_qa_history dari percakapan real
Mengekstrak Q&A pairs (customer question + karyawan reply) dari wa_messages
Karyawan = Ocha, Filean, Kasir (outbound messages, is_from_me=1)
"""
import sqlite3
import httpx
import hashlib
import json
from datetime import datetime, timedelta

DB_PATH    = "/opt/siji-dashboard/siji_database.db"
OLLAMA     = "http://localhost:11434"
CHROMA     = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"
COL_NAME   = "siji_qa_history"

# --- Filters ---
SKIP_KEYWORDS = [
    "FAKTUR ELEKTRONIK", "kertas.smartlink.id", "smartlink.id/nota",
    "Syarat & ketentuan", "Pembayaran transfer hanya melalui",
    "Nama:\nPilih:", "Nama: \nPilih:",
    "SIJI Bintaro\nJl. Raya Emerald",
]

def is_junk(text: str) -> bool:
    if not text or len(text.strip()) < 5:
        return True
    for kw in SKIP_KEYWORDS:
        if kw in text:
            return True
    return False


def embed(text: str) -> list:
    resp = httpx.post(f"{OLLAMA}/api/embed",
                      json={"model": "nomic-embed-text", "input": text[:500]},
                      timeout=20)
    return resp.json().get("embeddings", [[]])[0]


def get_or_create_collection() -> str:
    """Get or create siji_qa_history collection, return collection id"""
    resp = httpx.get(f"{CHROMA}/collections", timeout=5)
    for col in resp.json():
        if col["name"] == COL_NAME:
            print(f"[CHROMA] Collection '{COL_NAME}' exists, id={col['id']}")
            return col["id"]

    # Create with cosine space + dim 768
    resp = httpx.post(f"{CHROMA}/collections", json={
        "name": COL_NAME,
        "configuration": {
            "hnsw": {"space": "cosine"}
        },
        "metadata": {"description": "SIJI Q&A pairs dari percakapan real karyawan"}
    }, timeout=10)
    col_id = resp.json()["id"]
    print(f"[CHROMA] Created '{COL_NAME}', id={col_id}")
    return col_id


def extract_qa_pairs(conn) -> list:
    """Extract clean Q&A pairs from wa_messages"""
    cursor = conn.cursor()

    # Get all customer messages (is_from_me=0), text only
    cursor.execute("""
        SELECT m.rowid, m.conversation_jid, m.sender_name, m.message_text,
               m.is_from_me, m.timestamp
        FROM wa_messages m
        WHERE m.is_from_me = 0
          AND m.message_type = 'text'
          AND length(m.message_text) >= 8
          AND m.timestamp < '2026-03-08'
        ORDER BY m.conversation_jid, m.timestamp
    """)
    customer_msgs = cursor.fetchall()
    print(f"[EXTRACT] Customer messages: {len(customer_msgs)}")

    pairs = []
    seen_ids = set()

    for row in customer_msgs:
        rowid, jid, cust_name, question, _, ts_str = row

        if is_junk(question):
            continue

        # Find next staff reply within 10 minutes in same conversation
        try:
            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00').replace('+00:00', ''))
            ts_limit = (ts + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            ts_limit = ts_str[:16] + ":00"

        cursor.execute("""
            SELECT message_text FROM wa_messages
            WHERE conversation_jid = ?
              AND is_from_me = 1
              AND message_type = 'text'
              AND timestamp > ?
              AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT 1
        """, (jid, ts_str, ts_limit))

        staff_row = cursor.fetchone()
        if not staff_row:
            continue

        answer = staff_row[0]
        if is_junk(answer) or len(answer) < 10 or len(answer) > 600:
            continue

        # Dedup
        pair_id = hashlib.md5(f"{question}{answer}".encode()).hexdigest()
        if pair_id in seen_ids:
            continue
        seen_ids.add(pair_id)

        pairs.append({
            "id": pair_id,
            "question": question.strip(),
            "answer": answer.strip(),
            "customer_name": cust_name or "",
            "timestamp": ts_str
        })

    print(f"[EXTRACT] Clean Q&A pairs: {len(pairs)}")
    return pairs


def upsert_to_chroma(col_id: str, pairs: list, batch_size: int = 20):
    """Embed and upsert pairs to ChromaDB in batches"""
    total = 0
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i+batch_size]
        ids, embeddings, documents, metadatas = [], [], [], []

        for p in batch:
            emb = embed(p["question"])
            if not emb:
                print(f"  [SKIP] embed failed for: {p['question'][:40]}")
                continue
            ids.append(p["id"])
            embeddings.append(emb)
            documents.append(p["question"])
            metadatas.append({
                "answer": p["answer"],
                "customer_name": p["customer_name"],
                "timestamp": p["timestamp"]
            })

        if not ids:
            continue

        resp = httpx.post(
            f"{CHROMA}/collections/{col_id}/upsert",
            json={"ids": ids, "embeddings": embeddings,
                  "documents": documents, "metadatas": metadatas},
            timeout=60
        )
        if resp.status_code in (200, 201):
            total += len(ids)
            print(f"  [UPSERT] batch {i//batch_size + 1}: {len(ids)} pairs → total {total}")
        else:
            print(f"  [ERROR] batch {i//batch_size + 1}: {resp.status_code} {resp.text[:100]}")

    return total


def main():
    print("=== Populate siji_qa_history ChromaDB ===")

    conn = sqlite3.connect(DB_PATH)
    pairs = extract_qa_pairs(conn)
    conn.close()

    if not pairs:
        print("[DONE] No pairs to insert.")
        return

    col_id = get_or_create_collection()
    total = upsert_to_chroma(col_id, pairs)

    # Verify count
    resp = httpx.get(f"{CHROMA}/collections/{col_id}/count", timeout=5)
    print(f"\n=== DONE: {total} pairs upserted, ChromaDB count: {resp.text} ===")


if __name__ == "__main__":
    main()
