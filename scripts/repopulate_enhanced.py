"""Repopulate siji_services dengan document format lebih natural untuk brand awareness."""
import sqlite3, httpx

CHROMA = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"
OLLAMA = "http://localhost:11434/api/embeddings"
DB     = "/opt/siji-dashboard/siji_database.db"
CID    = "abfaff02-9809-4b30-9f1f-9ac0870f972b"

def embed(text):
    r = httpx.post(OLLAMA, json={"model":"nomic-embed-text","prompt":text}, timeout=20)
    return r.json()["embedding"]

def fmt(h, sat, dh, dj):
    p = f"Rp{h:,}".replace(",",".")
    d = f"{dh} hari" if dh > 0 else f"{dj} jam" if dj > 0 else ""
    return f"{p}/{sat.lower()}" + (f" ({d})" if d else "")

# Custom doc templates untuk kategori tertentu
CUSTOM_DOCS = {
    35: ("bag spa usa brand",
         "SIJI Bintaro terima cuci tas branded Amerika: Coach, Kate Spade, Fossil, Tory Burch, Aigner, Furla dan brand USA lainnya. "
         "Layanan bag spa / cuci tas mewah USA brand. Sinonim: tas coach, tas kate spade, tas fossil, tas tory burch, tas furla, tas branded, tas import Amerika. "
         "Harga {price_str}. Laundry branded bag Bintaro Emerald."),
    36: ("bag spa eropa brand",
         "SIJI Bintaro terima cuci tas branded Eropa mewah: LV Louis Vuitton, Gucci, Fendi, Prada, Balenciaga, Celine, Givenchy, Dior. "
         "Layanan bag spa / cuci tas luxury brand Eropa. Sinonim: tas lv, tas gucci, tas prada, tas fendi, tas dior, tas balenciaga, tas mewah eropa, luxury bag. "
         "Harga {price_str}. Laundry luxury bag Bintaro Emerald."),
    37: ("dompet usa brand",
         "SIJI Bintaro terima cuci dompet bermerek USA: Fossil, Coach, Kate Spade dan brand Amerika lainnya. "
         "Sinonim: dompet branded, dompet import, wallet USA. Harga {price_str}."),
    38: ("dompet eropa brand",
         "SIJI Bintaro terima cuci dompet mewah Eropa: LV, Gucci, Balenciaga, Fendi, Dior, Givenchy, Bottega. "
         "Sinonim: dompet lv, dompet gucci, dompet mewah, luxury wallet eropa. Harga {price_str}."),
    41: ("bag repair perbaikan tas",
         "SIJI Bintaro menerima perbaikan dan repair tas rusak: jahit tas, ganti resleting tas, perbaikan handle tas. "
         "Sinonim: tas jelek, tas rusak diperbaiki, repair bag, tas perlu diperbaiki. Harga {price_str}."),
}

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

    if row["id"] in CUSTOM_DOCS:
        _, doc_tmpl = CUSTOM_DOCS[row["id"]]
        doc = doc_tmpl.format(price_str=ps)
    else:
        doc = (f"SIJI Bintaro bisa cuci {nama.lower()}. "
               f"Layanan: {nama}. Sinonim: {kw}. "
               f"Harga {ps}. Laundry Bintaro Emerald.")

    ids.append(str(row["id"]))
    embeddings.append(embed(doc))
    documents.append(doc)
    metadatas.append({"nama_layanan":nama,"harga":h,"satuan":sat,
                      "durasi_hari":dh,"durasi_jam":dj,"price_str":ps,"keywords":kw})
    print(f"[{row['id']:2d}] {nama[:40]}")

r = httpx.post(f"{CHROMA}/collections/{CID}/upsert",
    json={"ids":ids,"embeddings":embeddings,"documents":documents,"metadatas":metadatas},
    timeout=60)
print(f"\nUpsert {r.status_code} | {len(ids)} items done")
