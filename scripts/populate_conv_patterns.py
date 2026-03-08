"""
Phase 4: Embed mined pairs → upsert ke ChromaDB collection 'siji_conv_patterns'
Hanya pairs dengan quality >= 0.6 dan intent bukan order_status
"""
import json, httpx, hashlib

CHROMA = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"
OLLAMA = "http://localhost:11434/api/embeddings"
COLLECTION = "siji_conv_patterns"
MIN_QUALITY = 0.6

with open("/tmp/siji_mined_pairs.json") as f:
    pairs = json.load(f)

# Filter: quality threshold
pairs = [p for p in pairs if p["quality"] >= MIN_QUALITY and p["intent"] != "order_status"]
print(f"Pairs to embed: {len(pairs)} (quality ≥ {MIN_QUALITY})")

# Stats
by_intent = {}
for p in pairs: by_intent[p["intent"]] = by_intent.get(p["intent"],0) + 1
for k,v in sorted(by_intent.items(), key=lambda x:-x[1]):
    print(f"  {k:<20} {v}")

# Delete old & create fresh collection
r = httpx.get(f"{CHROMA}/collections", timeout=10)
for c in r.json():
    if c["name"] == COLLECTION:
        httpx.delete(f"{CHROMA}/collections/{c['id']}", timeout=10)
        print(f"Deleted old {COLLECTION}")

r = httpx.post(f"{CHROMA}/collections",
    json={"name": COLLECTION, "metadata": {"hnsw:space": "cosine"}}, timeout=10)
cid = r.json()["id"]
print(f"Created collection: {cid}")

def embed(text):
    r = httpx.post(OLLAMA, json={"model":"nomic-embed-text","prompt":text}, timeout=20)
    return r.json()["embedding"]

# Embed & upsert in batches of 50
BATCH = 50
ids, embeddings, documents, metadatas = [], [], [], []

for i, p in enumerate(pairs):
    # Document: combined Q+A untuk semantic search
    doc = (
        f"Pertanyaan customer laundry SIJI: {p['customer']}\n"
        f"Jawaban karyawan SIJI: {p['staff']}"
    )
    doc_id = hashlib.md5(doc.encode()).hexdigest()

    ids.append(doc_id)
    embeddings.append(embed(doc))
    documents.append(doc)
    metadatas.append({
        "customer": p["customer"][:500],
        "staff": p["staff"][:500],
        "intent": p["intent"],
        "quality": p["quality"],
    })

    if (i+1) % BATCH == 0 or i+1 == len(pairs):
        r = httpx.post(f"{CHROMA}/collections/{cid}/upsert",
            json={"ids":ids,"embeddings":embeddings,"documents":documents,"metadatas":metadatas},
            timeout=60)
        print(f"  Upserted {i+1}/{len(pairs)} | status={r.status_code}")
        ids, embeddings, documents, metadatas = [], [], [], []

print(f"\n✅ Done! {len(pairs)} patterns in '{COLLECTION}'")
print(f"Collection ID: {cid}")

# Verify count
r = httpx.get(f"{CHROMA}/collections/{cid}", timeout=10)
print(f"Verified count: {r.json().get('count', '?')}")
