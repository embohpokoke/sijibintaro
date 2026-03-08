"""
Phase 1+2+3: Mine wa_messages → extract pairs → classify intent → quality score
Output: /tmp/siji_mined_pairs.json
"""
import sqlite3, json, re
from datetime import datetime, timezone

DB = "/opt/siji-dashboard/siji_database.db"
OUT = "/tmp/siji_mined_pairs.json"

# ── Filter rules ─────────────────────────────────────────────────────────────
SKIP_STAFF = [
    "FAKTUR ELEKTRONIK", "kertas.smartlink.id",
    "Halo! Terima kasih telah menghubungi *SIJI Bintaro*",
    "tidak beroperasi", "Terima kasih atas pesan Anda",
    "kami sedang tidak", "auto-reply", "autoresponder",
]
SKIP_CUSTOMER_PREFIX = ("[image:", "[video:", "[sticker", "[audio:")
SKIP_CONVOS = {"6281288783088@s.whatsapp.net"}  # nomor SIJI sendiri

# Intent classification (rule-based)
INTENT_RULES = [
    ("order_status",    ["sudah selesai", "sudah jadi", "kapan selesai", "kapan jadi",
                         "sudah dikirim", "sudah diambil", "status order", "cek order",
                         "laundry saya", "cucian saya", "besok selesai", "selesai belum"]),
    ("complaint",       ["rusak", "sobek", "hilang", "luntur", "kecewa", "komplain",
                         "tidak sesuai", "ga beres", "kurang bersih", "masih kotor",
                         "minta ganti", "tanggung jawab"]),
    ("price_inquiry",   ["berapa", "harga", "tarif", "biaya", "ongkos", "budget"]),
    ("service_inquiry", ["bisa cuci", "bisa laundry", "terima", "menerima", "ada layanan",
                         "bisa kerjain", "boleh", "layanan apa"]),
    ("pickup_delivery", ["jemput", "antar", "pickup", "delivery", "kurir", "ongkir",
                         "bisa ke", "ke rumah", "area "]),
    ("schedule_hours",  ["jam buka", "buka jam", "jam berapa", "hari ini buka", "libur",
                         "lebaran", "sabtu", "minggu", "tutup"]),
    ("greeting",        ["halo", "hai", "permisi", "assalamualaikum", "pagi", "siang", "malam"]),
    ("order_confirm",   ["oke", "ok", "siap", "iya", "baik", "makasih", "terima kasih",
                         "noted", "oke kak"]),
]

# ── PII anonymizer ─────────────────────────────────────────────────────────
def anonymize(text: str) -> str:
    if not text:
        return text
    # Nomor HP
    text = re.sub(r'(?<!\d)(08\d{8,11}|628\d{8,11}|62\d{9,12})(?!\d)', '[PHONE]', text)
    # Nama setelah label
    text = re.sub(r'(?i)(nama\s*[:：]\s*)\S+(\s+\S+)?', r'\1[NAME]', text)
    # Alamat
    text = re.sub(r'(?i)(alamat\s*[:：]\s*).+', r'\1[ADDR]', text)
    # URL
    text = re.sub(r'https?://\S+', '[URL]', text)
    return text.strip()

# ── Quality scorer ─────────────────────────────────────────────────────────
def quality_score(customer: str, staff: str) -> float:
    score = 0.0
    sl = staff.lower()
    # Informatif: ada angka / harga / waktu / konfirmasi
    if any(c.isdigit() for c in staff): score += 0.2
    if any(w in sl for w in ["rp", "ribu", "jam", "hari", "bisa", "siap", "oke", "ya"]): score += 0.2
    # Tone SIJI: ada kak/bu/pak, emoji wajar
    if any(w in sl for w in ["kak", " bu ", " pak ", "🙏", "😊", "terimakasih", "terima kasih"]): score += 0.2
    # Customer message jelas
    if len(customer.split()) >= 3: score += 0.2
    # Bukan satu kata doang
    if len(staff.split()) >= 5: score += 0.2
    return round(score, 2)

# ── Classify intent ─────────────────────────────────────────────────────────
def classify_intent(text: str) -> str:
    tl = text.lower()
    for intent, keywords in INTENT_RULES:
        if any(kw in tl for kw in keywords):
            return intent
    return "general"

# ── Parse ISO timestamp ──────────────────────────────────────────────────────
def parse_ts(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)

# ── Main mining logic ────────────────────────────────────────────────────────
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Load semua messages, group per conversation
rows = conn.execute("""
    SELECT conversation_jid, message_id, sender_jid, sender_name,
           message_text, is_from_me, timestamp, message_type
    FROM wa_messages
    WHERE message_text IS NOT NULL AND message_text != ''
    ORDER BY conversation_jid, timestamp ASC
""").fetchall()
conn.close()

# Group by conversation
convos = {}
for r in rows:
    jid = r["conversation_jid"]
    if jid in SKIP_CONVOS: continue
    convos.setdefault(jid, []).append(dict(r))

print(f"Loaded {len(rows)} messages across {len(convos)} conversations")

# Extract pairs: customer msg → next staff reply
pairs = []
skipped = {"image":0, "short":0, "noise_staff":0, "no_reply":0, "order_status":0}

for jid, msgs in convos.items():
    for i, msg in enumerate(msgs):
        if msg["is_from_me"]: continue  # skip staff messages as anchor

        cust_text = (msg["message_text"] or "").strip()

        # Skip image/media
        if cust_text.startswith(SKIP_CUSTOMER_PREFIX):
            skipped["image"] += 1; continue
        if len(cust_text) < 8:
            skipped["short"] += 1; continue

        # Find next staff reply (within 6 hours)
        cust_ts = parse_ts(msg["timestamp"])
        staff_reply = None
        for j in range(i+1, min(i+20, len(msgs))):
            if not msgs[j]["is_from_me"]: continue
            staff_text = (msgs[j]["message_text"] or "").strip()
            staff_ts   = parse_ts(msgs[j]["timestamp"])

            # Time window: 6 jam
            diff_h = (staff_ts - cust_ts).total_seconds() / 3600
            if diff_h > 6: break

            # Filter noise staff
            if any(n in staff_text for n in SKIP_STAFF): 
                skipped["noise_staff"] += 1; break
            if len(staff_text) < 15: continue

            staff_reply = staff_text
            break

        if not staff_reply:
            skipped["no_reply"] += 1; continue

        # Classify intent
        intent = classify_intent(cust_text)
        if intent == "order_status":
            skipped["order_status"] += 1; continue

        # Quality score
        qs = quality_score(cust_text, staff_reply)
        if qs < 0.4: continue  # low bar dulu, nanti filter di populate

        # Anonymize
        cust_anon  = anonymize(cust_text)
        staff_anon = anonymize(staff_reply)

        pairs.append({
            "customer": cust_anon,
            "staff":    staff_anon,
            "intent":   intent,
            "quality":  qs,
            "jid":      jid,
            "ts":       msg["timestamp"],
        })

# Stats
intent_counts = {}
for p in pairs: intent_counts[p["intent"]] = intent_counts.get(p["intent"], 0) + 1

print(f"\n✅ Valid pairs extracted: {len(pairs)}")
print(f"   Skipped — image: {skipped['image']}, short: {skipped['short']}, "
      f"noise_staff: {skipped['noise_staff']}, no_reply: {skipped['no_reply']}, "
      f"order_status: {skipped['order_status']}")
print(f"\nIntent distribution:")
for k, v in sorted(intent_counts.items(), key=lambda x: -x[1]):
    print(f"  {k:<20} {v}")
print(f"\nQuality distribution:")
qs_hi = sum(1 for p in pairs if p["quality"] >= 0.8)
qs_md = sum(1 for p in pairs if 0.6 <= p["quality"] < 0.8)
qs_lo = sum(1 for p in pairs if p["quality"] < 0.6)
print(f"  High (≥0.8):  {qs_hi}")
print(f"  Medium (0.6-0.8): {qs_md}")
print(f"  Low (<0.6): {qs_lo}")

print(f"\nSample pairs:")
for p in pairs[:5]:
    print(f"  [{p['intent']}] Q={p['quality']} | {p['customer'][:60]}")
    print(f"         → {p['staff'][:80]}")
    print()

with open(OUT, "w") as f:
    json.dump(pairs, f, ensure_ascii=False, indent=2)
print(f"Saved to {OUT}")
