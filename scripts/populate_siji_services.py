import sqlite3, httpx

CHROMA = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"
OLLAMA = "http://localhost:11434/api/embeddings"
DB     = "/opt/siji-dashboard/siji_database.db"
CID    = "abfaff02-9809-4b30-9f1f-9ac0870f972b"  # existing siji_services

def embed(text):
    r = httpx.post(OLLAMA, json={"model":"nomic-embed-text","prompt":text}, timeout=20)
    return r.json()["embedding"]

def fmt(h, sat, dh, dj):
    p = f"Rp{h:,}".replace(",",".")
    d = f"{dh} hari" if dh > 0 else f"{dj} jam" if dj > 0 else ""
    return f"{p}/{sat.lower()}" + (f" ({d})" if d else "")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM service_catalog ORDER BY id").fetchall()
conn.close()

ids, embeddings, documents, metadatas = [], [], [], []
for row in rows:
    nama = row["nama_layanan"]
    kw   = row["keywords"] or ""
    sat  = row["satuan"] or "pcs"
    h, dh, dj = row["harga"] or 0, row["durasi_hari"] or 0, row["durasi_jam"] or 0
    ps = fmt(h, sat, dh, dj)

    doc = (f"SIJI Bintaro bisa cuci {nama.lower()}. "
           f"Layanan: {nama}. Sinonim: {kw}. "
           f"Harga {ps}. Laundry Bintaro Emerald.")

    ids.append(str(row["id"]))
    embeddings.append(embed(doc))
    documents.append(doc)
    metadatas.append({"nama_layanan":nama,"harga":h,"satuan":sat,
                      "durasi_hari":dh,"durasi_jam":dj,"price_str":ps,"keywords":kw})
    print(f"[{row['id']:2d}] {nama[:40]}")

# Upsert (overwrite existing)
r = httpx.post(f"{CHROMA}/collections/{CID}/upsert",
    json={"ids":ids,"embeddings":embeddings,"documents":documents,"metadatas":metadatas},
    timeout=60)
print(f"\nUpsert {r.status_code} | {len(ids)} items done")
