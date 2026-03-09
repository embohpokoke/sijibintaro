"""
WhatsApp Webhook for Fonnte - SIJI.Bintaro
Handles inbound messages from WA1 (0812-8878-3088)
Logs all customer communications to database.
"""

from fastapi import APIRouter, Request, HTTPException
from datetime import datetime, timedelta
import os
import json
import re
import sqlite3
import asyncio
import httpx

# Customer context lookup (Phase 3)
try:
    from customer_context import get_customer_context, format_customer_greeting
    CUSTOMER_CONTEXT_ENABLED = True
    print("[AUTOREPLY] Customer context enabled (customer_context loaded)")
except ImportError as _ce:
    CUSTOMER_CONTEXT_ENABLED = False
    def get_customer_context(phone): return {"found": False, "nama": "", "segment": "Baru"}
    def format_customer_greeting(ctx, fallback=""): return fallback or "Kak"

# RAG + LLM modules (Phase 2)
try:
    from siji_rag import find_context, warmup_bm25
    from siji_llm import generate_reply_async, warmup_model
    RAG_ENABLED = True
    print("[AUTOREPLY] RAG + LLM enabled (siji_rag + siji_llm loaded)")
    # Warm up model + BM25 indexes in background
    import threading
    threading.Thread(target=warmup_model, daemon=True).start()
    threading.Thread(target=warmup_bm25, daemon=True).start()
except ImportError as _e:
    RAG_ENABLED = False
    print(f"[AUTOREPLY] RAG disabled: {_e}")

router = APIRouter(prefix="/api/wa", tags=["WhatsApp"])

# Fonnte config
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN", "")
FONNTE_DEVICE = "6281288783088"
AUTOREPLY_ENABLED = False  # Disabled by Erik 23 Feb 2026
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
FONNTE_API_URL = "https://api.fonnte.com/send"
TELEGRAM_BOT_TOKEN = "8510158455:AAHT5gd5xKtrCtzl3kAXuMVUsyCYTAyacjc"
TELEGRAM_ADMIN_CHAT_ID = "5309429603"
TELEGRAM_API_URL = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"


# === CONDITIONAL ROUTING CONFIG ===
ALLOWED_NUMBERS = [
    "62811319003",    # Erik
    "628118606999",   # Ocha SIJI
    "62811309991",    # Ocha Property
    "6282124046283",  # Filean
    "6281288783088",  # Kasir SIJI
    "6281227760808",  # Rizky (Kurir)
    "6285892726416",  # Denisa (Produksi & Setrika)
    "6285715247073",  # Unaesih (Kasir & Produksi)
]

ADMIN_NUMBERS = [
    "62811319003",   # Erik
    "628118606999",  # Ocha SIJI
]
# === GOWA AUTOREPLY CONFIG ===
GOWA_AUTOREPLY_ENABLED = True   # Diaktifkan 2026-03-08
GOWA_BASE = "http://127.0.0.1:3002"
GOWA_AUTH = ("siji", "SijiBintaro2026!")
GOWA_DEVICE_ID = "73834210-3694-43bf-a14d-c75d487b18cb"

# Numbers that should NEVER receive autoreply (admin + staff)
SKIP_AUTOREPLY_NUMBERS = [
    # "62811319003",  # Erik — sementara dikeluarkan untuk TESTING MODE
    "628118606999",   # Ocha (Owner SIJI — hanya eskalasi)
    "6282124046283",  # Filean (Owner/Manajer — hanya eskalasi)
    "62811309991",    # Ocha Livinin (Manager)
    "6281288783088",  # Karyawan SIJI / nomor outlet SIJI
    "6281227760808",  # Rizky (Karyawan)
    "6285892726416",  # Denisa (Karyawan)
    "6285715247073",  # Unaesih (Karyawan)
    # Vendor / Supplier
    "6281314155208",  # Laris Jaya Pasmod (supplier)
    "6282186554606",  # Tukang Karpet 2 (vendor)
]

# === TEST MODE ===
# Aktif: hanya TEST_NUMBERS yang dapat autoreply, customer lain dilewati
# Nonaktifkan (GOWA_TEST_MODE = False) saat siap production
GOWA_TEST_MODE = True
GOWA_TEST_NUMBERS = [
    "62811319003",    # Erik — testing sebagai pelanggan
]

ESCALATION_NUMBERS = [
    "628118606999",   # Ocha (Owner) — terima notif eskalasi
    "62811319003",    # Erik (owner) — acknowledge complaint
]

# Dedup cache: cegah GOWA webhook retry menyebabkan double/triple reply
# {msg_id_wa: timestamp} — entri dihapus setelah 5 menit
import time as _time
_PROCESSED_MSG_IDS: dict = {}
_DEDUP_TTL = 300  # 5 menit

def _is_duplicate(msg_id: str) -> bool:
    """Return True jika msg_id sudah diproses dalam 5 menit terakhir"""
    if not msg_id:
        return False
    now = _time.time()
    expired = [k for k, v in _PROCESSED_MSG_IDS.items() if now - v > _DEDUP_TTL]
    for k in expired:
        del _PROCESSED_MSG_IDS[k]
    if msg_id in _PROCESSED_MSG_IDS:
        return True
    _PROCESSED_MSG_IDS[msg_id] = now
    return False

# Staff-handled tracker: kalau karyawan sudah balas ke JID ini, bot diam dulu
# {chat_jid: timestamp_last_staff_reply}
_STAFF_LAST_REPLY: dict = {}
STAFF_COOLDOWN_SEC = 1800  # 30 menit — bot diam setelah karyawan reply

# Default reply cooldown: jangan kirim default reply berulang ke nomor yg sama
_DEFAULT_REPLY_SENT: dict = {}  # {sender: timestamp}
DEFAULT_REPLY_COOLDOWN = 600  # 10 menit

def _can_send_default(sender: str) -> bool:
    """Return True jika belum kirim default reply ke sender dalam 10 menit"""
    now = _time.time()
    last = _DEFAULT_REPLY_SENT.get(sender, 0)
    if now - last < DEFAULT_REPLY_COOLDOWN:
        return False
    _DEFAULT_REPLY_SENT[sender] = now
    return True

def _mark_staff_replied(jid: str):
    """Catat bahwa karyawan baru saja reply ke JID ini"""
    _STAFF_LAST_REPLY[jid] = _time.time()

def _staff_is_handling(jid: str) -> bool:
    """Return True jika karyawan reply ke JID ini dalam 30 menit terakhir"""
    last = _STAFF_LAST_REPLY.get(jid, 0)
    return (_time.time() - last) < STAFF_COOLDOWN_SEC

# Keywords indikasi komplain pelanggan → trigger eskalasi
COMPLAINT_KEYWORDS = [
    # Ekspresi kekecewaan
    "komplain", "kecewa", "kecewa", "tidak puas", "ga puas", "gak puas",
    "nggak puas", "ngga puas",
    # Masalah hasil laundry
    "rusak", "sobek", "hilang", "luntur", "bau", "kotor", "belum bersih",
    "masih kotor", "masih bau", "tidak bersih", "gak bersih",
    # Masalah waktu / layanan
    "lama", "lambat", "telat", "terlambat", "belum selesai", "belum jadi",
    "belum datang", "belum diantar", "belum dijemput", "kapan selesai",
    "kapan jadi", "kapan diantar",
    # Masalah harga / tagihan
    "kemahalan", "terlalu mahal", "salah tagih", "tagihan salah",
    "harga beda", "harga tidak sesuai",
    # Ekspresi keras
    "kecewa banget", "sangat kecewa", "tidak profesional", "gak profesional",
    "buruk", "jelek", "mengecewakan", "bohong", "tipu", "menipu",
    "mau refund", "kembalikan uang", "cancel", "batalkan",
]

# Reply default untuk pesan non-keyword, non-komplain
AUTO_REPLY_DEFAULT = (
    "Halo Kak! 👋 Pesan kamu sudah kami terima.\n"
    "Tim kami akan segera membalas ya 🙏"
)

# Keywords status order — intercept sebelum LLM (LLM tidak bisa akses DB order)
ORDER_STATUS_KEYWORDS = [
    "sudah selesai", "sudah jadi", "sudah beres", "udah selesai", "udah jadi",
    "laundry saya", "cucian saya", "order saya", "pesanan saya",
    "kapan selesai", "kapan jadi", "kapan bisa diambil", "kapan bisa dijemput",
    "selesai belum", "jadi belum", "beres belum", "sudah bisa",
    "cek order", "cek pesanan", "status order", "status laundry",
    "sudah dikirim", "sudah diantar", "sudah di antar",
    "besok selesai", "selesaikah", "bisa besok", "besok bisa",
    "besok jadi", "besok sudah", "kapan siap", "siap besok",
    "bisa diambil besok", "besok bisa diambil",
]

# Reply untuk pesan yang tidak jelas itemnya ("bisa cuci ini?", kirim foto)
ASK_ITEM_REPLY = (
    "Halo Kak! 😊 Boleh disebutkan barang apa yang mau dicuci/dilaundry?\n"
    "Nanti kami langsung cek layanan dan harganya ya!"
)

ASK_ITEM_KEYWORDS = [
    "cuci ini", "laundry ini", "ini bisa", "bisa dicuci", "bisa dilaundry",
    "ini laundry", "ini cuci", "bisa cuci gak", "bisa laundry gak",
    "cuci apa ini", "ini apa bisa", "terima ini", "bisa terima ini",
]


def is_vague_item_query(message: str) -> bool:
    """Deteksi pertanyaan item tidak jelas — customer tidak sebut nama barang."""
    msg_lower = message.lower().strip()
    # Cocok dengan keyword vague
    if any(kw in msg_lower for kw in ASK_ITEM_KEYWORDS):
        return True
    # Pesan sangat pendek dengan "ini" + kata tanya (max 6 kata)
    words = msg_lower.split()
    if len(words) <= 6 and "ini" in words and any(q in msg_lower for q in ["bisa", "boleh", "cuci", "laundry"]):
        return True
    return False


ORDER_STATUS_REPLY = (
    "Halo Kak! 👋 Untuk cek status laundry, tim kami akan segera konfirmasi ya.\n"
    "Mohon ditunggu sebentar 🙏"
)


def is_order_status_query(message: str) -> bool:
    """Deteksi pertanyaan status order — jangan sampai LLM yang jawab (bisa halusinasi)"""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in ORDER_STATUS_KEYWORDS)


def is_complaint(message: str) -> bool:
    """Detect complaint indicators in customer message"""
    msg_lower = message.lower().strip()
    return any(kw in msg_lower for kw in COMPLAINT_KEYWORDS)


def get_time_greeting() -> str:
    """Return sapaan berdasarkan jam WIB (UTC+7)"""
    from datetime import datetime, timezone, timedelta
    wib = datetime.now(timezone(timedelta(hours=7)))
    hour = wib.hour
    if 5 <= hour < 12:
        return "Selamat pagi"
    elif 12 <= hour < 15:
        return "Selamat siang"
    elif 15 <= hour < 19:
        return "Selamat sore"
    else:
        return "Selamat malam"


def _extract_salutation(name: str) -> str:
    """Ekstrak salutation Pak/Bu dari nama, atau Kak sebagai default."""
    if not name:
        return "Kak"
    n = name.strip()
    nl = n.lower()
    # Nama sudah ada prefix Pak/Bu/Ibu/Bapak
    for prefix in ["ibu ", "bu "]:
        if nl.startswith(prefix):
            rest = n[len(prefix):].strip().split()
            short = rest[0] if rest else ""
            return f"Bu {short}" if short else "Bu"
    for prefix in ["bapak ", "pak "]:
        if nl.startswith(prefix):
            rest = n[len(prefix):].strip().split()
            short = rest[0] if rest else ""
            return f"Pak {short}" if short else "Pak"
    # Nama polos — pakai Kak (gender neutral)
    parts = n.split()
    short = parts[0] if parts else n
    return f"Kak {short}"


def build_greeting(cust_name: str, segment: str) -> str:
    """
    Return greeting line untuk customer dikenal.
    VIP: nama lengkap + emoji khusus.
    Reguler/Baru: sapaan standar.
    Sapaan: Pak/Bu jika ada prefix di nama, Kak jika tidak.
    """
    sapa = get_time_greeting()
    if not cust_name:
        return ""
    salut = _extract_salutation(cust_name)
    if segment == "VIP":
        return f"{sapa} {salut}! 😊✨"
    return f"{sapa} {salut}! 😊"


# Landing page karir
KARIR_URL = "https://sijibintaro.id/karir"

# Job application keywords
JOB_KEYWORDS = ["lamar", "kerja", "lowongan", "pelamar", "apply", "hiring", "rekrut", "karyawan baru"]

# Auto-reply untuk nomor tidak dikenal
AUTO_REPLY_UNKNOWN = (
    "Halo! Terima kasih sudah menghubungi SIJI.Bintaro 👋\n\n"
    "Untuk layanan laundry dan pertanyaan umum, silakan chat ke nomor customer service kami.\n\n"
    "Sedang mencari info lowongan kerja? Cek di sini:\n"
    "👉 {karir_url}\n\n"
    "Tim kami akan segera menghubungi Anda. Terima kasih! 🙏"
).format(karir_url=KARIR_URL)

AUTO_REPLY_JOB = (
    "Halo! Terima kasih sudah tertarik bergabung dengan SIJI.Bintaro 🙌\n\n"
    "Silakan lengkapi form lamaran di sini:\n"
    "👉 {karir_url}\n\n"
    "Tim kami akan menghubungi Anda jika ada posisi yang sesuai. Terima kasih! 💪"
).format(karir_url=KARIR_URL)

# === KATALOG LAYANAN (Layer 2.5) ===
# Pakai ChromaDB similarity search dari collection siji_services (61 item dari DB)
# Lebih robust: handle variasi bahasa, typo, bahasa Inggris, sinonim
COLLECTION_SERVICES = "siji_services"
SERVICES_COLLECTION_ID = None  # di-cache saat pertama kali dipanggil
SERVICE_SIMILARITY_THRESHOLD = 0.70  # min score untuk dianggap match

# === KATALOG LAYANAN (Layer 2.5) ===
# Deteksi "bisa cuci X?" → jawab langsung dari katalog, tanpa LLM
# SERVICE_CATALOG hardcode di bawah masih ada sebagai reference,
# tapi check_service_catalog() sekarang pakai ChromaDB similarity.
# Update layanan cukup di service_catalog DB + re-populate siji_services collection.
SERVICE_CATALOG = {  # DEPRECATED — gunakan siji_services ChromaDB
    # Kiloan
    "kiloan":    ("cuci kering setrika reguler", "Rp16.000/kg (min 3kg, 3 hari)"),
    "setrika":   ("setrika kiloan reguler", "Rp12.000/kg (min 3kg, 3 hari)"),
    # Household
    "karpet":    ("karpet", "Rp35.000/m² (10 hari)"),
    "carpet":    ("karpet", "Rp35.000/m² (10 hari)"),
    "permadani": ("karpet", "Rp35.000/m² (10 hari)"),
    "gordyn":    ("gordyn", "Rp16.000/m² (tebal/blackout), Rp10.000/m² (tipis/vetrase)"),
    "gorden":    ("gordyn", "Rp16.000/m² (tebal/blackout), Rp10.000/m² (tipis/vetrase)"),
    "sofa":      ("sarung sofa", "Rp30.000/m²"),
    # Bedding
    "stroller":  ("baby stroller", "Rp250.000/unit (6 hari)"),
    "bedcover":  ("bedcover", "Rp70.000/lembar (3 hari), Express 24 jam Rp115.000"),
    "bed cover": ("bedcover", "Rp70.000/lembar (3 hari), Express 24 jam Rp115.000"),
    "sprei":     ("sprei 1 set", "Rp35.000/set (3 hari), Express 24 jam Rp55.000"),
    "bantal":    ("bantal/guling", "Rp40.000 (kecil), Rp60.000 (besar/guling)"),
    "guling":    ("bantal/guling", "Rp40.000 (kecil), Rp60.000 (besar/guling)"),
    "kasur":     ("kasur/matras", "Rp95.000/unit (matras tipis), Rp400.000 (kasur lipat)"),
    "matras":    ("kasur/matras", "Rp95.000/unit (matras tipis), Rp400.000 (kasur lipat)"),
    "boneka":    ("boneka", "Rp40.000 (kecil), Rp100.000 (besar)"),
    # Sepatu
    "sepatu":    ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "shoes":     ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "shoe":      ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "sneakers":  ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "boot":      ("sepatu boot", "Rp150.000/pcs (4 hari)"),
    "helm":      ("helm", "Rp80.000/pcs (3 hari)"),
    # Tas
    "tas":       ("tas", "Rp140.000 (reguler), Rp250.000 (USA brand), Rp500.000 (EU brand/LV/Gucci)"),
    "bag":       ("tas", "Rp140.000 (reguler), Rp250.000 (USA brand), Rp500.000 (EU brand/LV/Gucci)"),
    "handbag":   ("tas", "Rp140.000 (reguler), Rp250.000 (USA brand), Rp500.000 (EU brand/LV/Gucci)"),
    "dompet":    ("dompet", "Rp100.000 (reguler), Rp200.000 (USA brand), Rp350.000 (EU brand)"),
    "ransel":    ("tas gunung/ransel", "Rp200.000/pcs (5 hari)"),
    # Dry clean / Pakaian
    "blazer":    ("blazer/jaket", "Rp65.000/pcs (3 hari)"),
    "jaket":     ("blazer/jaket", "Rp65.000/pcs biasa, Rp150.000 (kulit, 12 hari)"),
    "jas":       ("dry clean blazer/jas", "Rp80.000/pcs (4 hari)"),
    "kulit":     ("pakaian/jaket kulit", "Rp150.000/pcs (12 hari)"),
    "dress":     ("dress/kebaya/brokat", "Rp100.000/pcs (4 hari)"),
    "kebaya":    ("dress/kebaya/brokat", "Rp100.000/pcs (4 hari)"),
    "topi":      ("cuci topi", "Rp65.000/pcs (4 hari)"),
    # Lainnya
    "koper":     ("koper", "Rp190.000/unit (4 hari)"),
    "sleeping":  ("sleeping bag", "Rp90.000/pcs (5 hari)"),
}

# Kata tanya / pertanyaan yang menandakan customer butuh info layanan
_QUESTION_WORDS = [
    "berapa", "harga", "tarif", "biaya", "treatment", "treatmentnya",
    "bisa", "boleh", "ada", "terima", "menerima", "laundry", "cuci",
    "layanan", "jenis", "apa saja", "gimana", "bagaimana", "cara",
    "spa", "service", "servis",
]



# English → Indonesian normalizer untuk catalog queries
# nomic-embed-text lemah cross-lingual, jadi kita translate dulu sebelum embed
_EN_ID_MAP = {
    # Tas
    "bag":          "tas",     "bags":        "tas",
    "duffel":       "tas",     "duffel bag":  "tas ransel",
    "duffle":       "tas ransel", "travel bag": "tas ransel",
    "backpack":     "ransel",  "tote":        "tas",
    "handbag":      "tas",     "purse":       "tas dompet",
    "wallet":       "dompet",  "pouch":       "tas kecil",
    "clutch":       "tas kecil",
    # Sepatu
    "shoes":        "sepatu",  "shoe":        "sepatu",
    "sneakers":     "sepatu",  "sneaker":     "sepatu",
    "boots":        "sepatu boot", "boot":    "sepatu boot",
    "heels":        "sepatu",  "sandals":     "sandal",
    # Pakaian
    "jacket":       "jaket",   "blazer":      "blazer",
    "dress":        "dress",   "kebaya":      "kebaya",
    "leather":      "kulit",   "coat":        "jaket",
    # Rumah tangga
    "carpet":       "karpet",  "rug":         "karpet",
    "curtain":      "gordyn",  "curtains":    "gordyn",
    "mattress":     "kasur",   "pillow":      "bantal",
    "bolster":      "guling",  "blanket":     "selimut",
    "bedsheet":     "sprei",   "duvet":       "bedcover",
    "comforter":    "bedcover","quilt":       "bedcover",
    "bedcover":     "bedcover",
    # Bayi & lainnya
    "stroller":     "stroller","helmet":      "helm",
    "suitcase":     "koper",   "luggage":     "koper",
    "sleeping bag": "sleeping bag",
    "sofa":         "sofa",    "stool":       "kursi sofa",
}


def _normalize_query_for_catalog(text: str) -> str:
    """Translate common English product terms to Indonesian sebelum embed."""
    result = text.lower()
    # Multi-word dulu
    for en, id_ in sorted(_EN_ID_MAP.items(), key=lambda x: -len(x[0])):
        if en in result and en != id_:
            result = result.replace(en, id_)
    return result

# English direct-match: bypass ChromaDB, langsung return catalog name
# Key: English keyword (lowercase), Value: nama_layanan di service_catalog (exact/partial)
_EN_DIRECT_CATALOG = {
    "duffel bag":   "TAS REGULAR",
    "duffel":       "TAS REGULAR",
    "duffle":       "TAS REGULAR",
    "travel bag":   "Tas Gunung/Ransel besar",
    "backpack":     "Tas Gunung/Ransel besar",
    "hiking bag":   "Tas Gunung/Ransel besar",
    "carrier":      "Tas Gunung/Ransel besar",
    "shoes spa":    "Treatment Sepatu (Hilangkan jamur,Noda)",
    "shoe spa":     "Treatment Sepatu (Hilangkan jamur,Noda)",
    "sneakers":     "SEPATU REGULER",
    "sneaker":      "SEPATU REGULER",
    "curtain":      "GORDYN TEBAL/BLACKOUT",
    "curtains":     "GORDYN TEBAL/BLACKOUT",
    "comforter":    "BEDCOVER",
    "duvet":        "BEDCOVER",
    "quilt":        "BEDCOVER",
    "mattress":     "KASUR/MATRAS TIPIS",
    "pillow":       "BANTAL/BONEKA KECIL",
    "bolster":      "BANTAL BESAR/GULING",
    "suitcase":     "KOPER",
    "luggage":      "KOPER",
    "helmet":       "HELM",
    "stroller":     "BABY STROLLER",
    "car seat":     "BABY CAR SEATER",
    "baby seat":    "BABY CAR SEATER",
    "sleeping bag": "Sleeping bag",
    "wallet":       "Dompet regular",
    "purse":        "TAS REGULAR",
    "handbag":      "TAS REGULAR",
    "tote bag":     "TAS REGULAR",
}

_EN_DIRECT_PRICE = {}  # cache: nama_layanan → price_str


def _get_price_for_nama(nama: str) -> str:
    """Lookup price_str dari service_catalog DB."""
    if nama in _EN_DIRECT_PRICE:
        return _EN_DIRECT_PRICE[nama]
    try:
        import sqlite3 as _sqlite3
        TX_DB = "/opt/siji-dashboard/siji_database.db"
        conn = _sqlite3.connect(TX_DB)
        row = conn.execute(
            "SELECT nama_layanan, harga, satuan, durasi_hari, durasi_jam FROM service_catalog "
            "WHERE nama_layanan LIKE ? LIMIT 1", (f"%{nama}%",)
        ).fetchone()
        conn.close()
        if row:
            _, h, sat, dh, dj = row
            p = f"Rp{h:,}".replace(",",".")
            d = f"{dh} hari" if dh > 0 else f"{dj} jam" if dj > 0 else ""
            ps = f"{p}/{sat.lower()}" + (f" ({d})" if d else "")
            _EN_DIRECT_PRICE[nama] = ps
            return ps
    except Exception:
        pass
    return ""


def _check_english_catalog(message: str):
    """Layer khusus English product terms — bypass ChromaDB, langsung match."""
    msg_lower = message.lower()
    has_question = any(q in msg_lower for q in _QUESTION_WORDS)
    if not has_question:
        return None
    # Cek multi-word dulu (terpanjang)
    for en_kw in sorted(_EN_DIRECT_CATALOG.keys(), key=len, reverse=True):
        if en_kw in msg_lower:
            nama = _EN_DIRECT_CATALOG[en_kw]
            price = _get_price_for_nama(nama)
            print(f"[CATALOG] EN direct match: {en_kw!r} → {nama}")
            return (
                f"Bisa Kak! SIJI menerima *{nama.title()}* \U0001f64c\n\n"
                f"\U0001f4b0 Harga: {price}\n\n"
                f"Mau dijemput kurir kami, atau langsung antar ke toko ya Kak? \U0001f60a"
            )
    return None


def _get_services_collection_id() -> str | None:
    """Cache collection ID siji_services."""
    global SERVICES_COLLECTION_ID
    if SERVICES_COLLECTION_ID:
        return SERVICES_COLLECTION_ID
    try:
        import httpx as _httpx
        CHROMA_BASE = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"
        r = _httpx.get(f"{CHROMA_BASE}/collections", timeout=5)
        for c in r.json():
            if c["name"] == COLLECTION_SERVICES:
                SERVICES_COLLECTION_ID = c["id"]
                return SERVICES_COLLECTION_ID
    except Exception as e:
        print(f"[CATALOG] ChromaDB error: {e}")
    return None


# Brand → service catalog mapping (nama brand tidak ter-embed dengan baik di nomic)
_BRAND_MAP = {
    # USA brands → BAG SPA USA BRAND
    "coach": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    "kate spade": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    "fossil": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    "michael kors": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    "mk bag": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    "tory burch": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    "aigner": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    "furla": (35, "BAG SPA USA BRAND", "Rp250.000/pcs (7 hari)"),
    # Direct match untuk item common — embedding kadang lemah karena angka/noise
    "karpet": (18, "KARPET", "Rp35.000/m² (10 hari)"),
    "carpet": (18, "KARPET", "Rp35.000/m² (10 hari)"),
    "kasur lipat": (56, "Kasur Lipat", "Rp400.000/pcs (10 hari)"),
    "sleeping bag": (59, "Sleeping Bag", "Rp90.000/pcs (5 hari)"),
    "car seat": (52, "BABY CAR SEATER", "Rp250.000/unit (6 hari)"),
    "baby bed": (53, "Baby Bed/Kasur Bayi", "Rp250.000/pcs (7 hari)"),
    # English product aliases
    "duffel bag": (34, "TAS REGULAR", "Rp140.000/unit (4 hari)"),
    "duffle bag": (34, "TAS REGULAR", "Rp140.000/unit (4 hari)"),
    "duffel":     (34, "TAS REGULAR", "Rp140.000/unit (4 hari)"),
    "tote bag":   (34, "TAS REGULAR", "Rp140.000/unit (4 hari)"),
    "handbag":    (34, "TAS REGULAR", "Rp140.000/unit (4 hari)"),
    "purse":      (34, "TAS REGULAR", "Rp140.000/unit (4 hari)"),
    "backpack":   (40, "Tas Gunung/Ransel Besar", "Rp200.000/pcs (5 hari)"),
    "travel bag": (40, "Tas Gunung/Ransel Besar", "Rp200.000/pcs (5 hari)"),
    "sneakers":   (23, "SEPATU REGULER", "Rp90.000/pasang (3 hari)"),
    "sneaker":    (23, "SEPATU REGULER", "Rp90.000/pasang (3 hari)"),
    "shoes spa":  (29, "Treatment Sepatu", "Rp150.000/pasang (5 hari)"),
    "shoe spa":   (29, "Treatment Sepatu", "Rp150.000/pasang (5 hari)"),
    "curtain":    (20, "GORDYN TEBAL/BLACKOUT", "Rp16.000/m² (5 hari)"),
    "curtains":   (20, "GORDYN TEBAL/BLACKOUT", "Rp16.000/m² (5 hari)"),
    "comforter":  (11, "BEDCOVER", "Rp70.000/lembar (3 hari)"),
    "duvet":      (11, "BEDCOVER", "Rp70.000/lembar (3 hari)"),
    "blanket":    (11, "BEDCOVER", "Rp70.000/lembar (3 hari)"),
    "mattress":   (55, "KASUR/MATRAS TIPIS", "Rp95.000/unit (5 hari)"),
    "pillow":     (16, "BANTAL/BONEKA KECIL", "Rp40.000/pcs (4 hari)"),
    "bolster":    (17, "BANTAL BESAR/GULING", "Rp60.000/pcs (4 hari)"),
    "suitcase":   (54, "KOPER", "Rp190.000/unit (4 hari)"),
    "luggage":    (54, "KOPER", "Rp190.000/unit (4 hari)"),
    "helmet":     (50, "HELM", "Rp80.000/pcs (3 hari)"),
    "stroller":   (51, "BABY STROLLER", "Rp250.000/unit (6 hari)"),
    "wallet":     (39, "Dompet Regular", "Rp100.000/pcs (6 hari)"),
    "jacket":     (44, "BLAZER/JAKET", "Rp65.000/pcs (3 hari)"),
    "leather jacket": (45, "PAKAIAN/JAKET KULIT", "Rp150.000/pcs (12 hari)"),
    # EU brands → BAG SPA EROPA
    "louis vuitton": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    " lv ": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "tas lv": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "cuci lv": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "laundry lv": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "gucci": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "prada": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "fendi": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "dior": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "balenciaga": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "celine": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
    "givenchy": (36, "BAG SPA BRAND EROPA", "Rp500.000/pcs (7 hari)"),
}


def check_service_catalog(message: str) -> str | None:
    """
    Deteksi pertanyaan layanan:
    1. Brand name keyword mapping (tas luxury)
    2. ChromaDB similarity (siji_services, 61 layanan)
    """
    msg_lower = message.lower().strip()

    # Harus ada kata tanya/konteks laundry
    has_question = any(q in msg_lower for q in _QUESTION_WORDS)
    if not has_question:
        return None

    # ── Pre-check: brand name mapping ────────────────────────────────────────
    for brand, (_, nama, price_str) in _BRAND_MAP.items():
        if brand in msg_lower:
            return (
                f"Bisa Kak! SIJI menerima *{nama}* \U0001f64c\n\n"
                f"\U0001f4b0 Harga: {price_str}\n\n"
                f"Mau dijemput kurir kami, atau langsung antar ke toko ya Kak? \U0001f60a"
            )

    try:
        import httpx as _httpx
        CHROMA_BASE = "http://localhost:32769/api/v2/tenants/default_tenant/databases/default_database"

        # Normalize English terms → Indonesian sebelum embed
        normalized = _normalize_query_for_catalog(message)
        if normalized != message.lower():
            print(f"[CATALOG] normalized: {message!r} → {normalized!r}")

        # Embed query
        emb_r = _httpx.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": normalized},
            timeout=10
        )
        embedding = emb_r.json().get("embedding")
        if not embedding:
            return None

        # Search siji_services
        cid = _get_services_collection_id()
        if not cid:
            return None

        qr = _httpx.post(
            f"{CHROMA_BASE}/collections/{cid}/query",
            json={"query_embeddings": [embedding], "n_results": 1,
                  "include": ["documents", "distances", "metadatas"]},
            timeout=10
        )
        result = qr.json()
        dists = result.get("distances", [[]])[0]
        metas = result.get("metadatas", [[]])[0]

        if not dists or not metas:
            return None

        score = 1 - dists[0]  # cosine distance → similarity
        print(f"[CATALOG] similarity={score:.3f} | {metas[0].get('nama_layanan','?')[:40]}")

        if score < SERVICE_SIMILARITY_THRESHOLD:
            return None

        meta = metas[0]
        nama = meta.get("nama_layanan", "layanan ini")
        price_str = meta.get("price_str", "")

        return (
            f"Bisa Kak! SIJI menerima *{nama.title()}* 🙌\n\n"
            f"💰 Harga: {price_str}\n\n"
            f"Mau dijemput kurir kami, atau langsung antar ke toko ya Kak? 😊"
        )

    except Exception as e:
        print(f"[CATALOG] Error: {e}")
        return None


# Keyword auto-replies

"""
SIJI Bintaro
Handles inbound messages from WA1 (0812-8878-3088)
Logs all customer communications to database.
"""

from fastapi import APIRouter, Request, HTTPException
from datetime import datetime, timedelta
import os
import json
import re
import sqlite3
import asyncio
import httpx

# Customer context lookup (Phase 3)
try:
    from customer_context import get_customer_context, format_customer_greeting
    CUSTOMER_CONTEXT_ENABLED = True
    print("[AUTOREPLY] Customer context enabled (customer_context loaded)")
except ImportError as _ce:
    CUSTOMER_CONTEXT_ENABLED = False
    def get_customer_context(phone): return {"found": False, "nama": "", "segment": "Baru"}
    def format_customer_greeting(ctx, fallback=""): return fallback or "Kak"

# RAG + LLM modules (Phase 2)
try:
    from siji_rag import find_context, warmup_bm25
    from siji_llm import generate_reply_async, warmup_model
    RAG_ENABLED = True
    print("[AUTOREPLY] RAG + LLM enabled (siji_rag + siji_llm loaded)")
    # Warm up model + BM25 indexes in background
    import threading
    threading.Thread(target=warmup_model, daemon=True).start()
    threading.Thread(target=warmup_bm25, daemon=True).start()
except ImportError as _e:
    RAG_ENABLED = False
    print(f"[AUTOREPLY] RAG disabled: {_e}")

router = APIRouter(prefix="/api/wa", tags=["WhatsApp"])

# Fonnte config
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN", "")
FONNTE_DEVICE = "6281288783088"
AUTOREPLY_ENABLED = False  # Disabled by Erik 23 Feb 2026
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
FONNTE_API_URL = "https://api.fonnte.com/send"
TELEGRAM_BOT_TOKEN = "8510158455:AAHT5gd5xKtrCtzl3kAXuMVUsyCYTAyacjc"
TELEGRAM_ADMIN_CHAT_ID = "5309429603"
TELEGRAM_API_URL = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"


# === CONDITIONAL ROUTING CONFIG ===
ALLOWED_NUMBERS = [
    "62811319003",    # Erik
    "628118606999",   # Ocha SIJI
    "62811309991",    # Ocha Property
    "6282124046283",  # Filean
    "6281288783088",  # Kasir SIJI
    "6281227760808",  # Rizky (Kurir)
    "6285892726416",  # Denisa (Produksi & Setrika)
    "6285715247073",  # Unaesih (Kasir & Produksi)
]

ADMIN_NUMBERS = [
    "62811319003",   # Erik
    "628118606999",  # Ocha SIJI
]
# === GOWA AUTOREPLY CONFIG ===
GOWA_AUTOREPLY_ENABLED = True   # Diaktifkan 2026-03-08
GOWA_BASE = "http://127.0.0.1:3002"
GOWA_AUTH = ("siji", "SijiBintaro2026!")
GOWA_DEVICE_ID = "73834210-3694-43bf-a14d-c75d487b18cb"

# Numbers that should NEVER receive autoreply (admin + staff)
SKIP_AUTOREPLY_NUMBERS = [
    # "62811319003",  # Erik — sementara dikeluarkan untuk TESTING MODE
    "628118606999",   # Ocha (Owner SIJI — hanya eskalasi)
    "6282124046283",  # Filean (Owner/Manajer — hanya eskalasi)
    "62811309991",    # Ocha Livinin (Manager)
    "6281288783088",  # Karyawan SIJI / nomor outlet SIJI
    "6281227760808",  # Rizky (Karyawan)
    "6285892726416",  # Denisa (Karyawan)
    "6285715247073",  # Unaesih (Karyawan)
    # Vendor / Supplier
    "6281314155208",  # Laris Jaya Pasmod (supplier)
    "6282186554606",  # Tukang Karpet 2 (vendor)
]

# === TEST MODE ===
# Aktif: hanya TEST_NUMBERS yang dapat autoreply, customer lain dilewati
# Nonaktifkan (GOWA_TEST_MODE = False) saat siap production
GOWA_TEST_MODE = True
GOWA_TEST_NUMBERS = [
    "62811319003",    # Erik — testing sebagai pelanggan
]

ESCALATION_NUMBERS = [
    "628118606999",   # Ocha (Owner) — terima notif eskalasi
    "62811319003",    # Erik (owner) — acknowledge complaint
]

# Dedup cache: cegah GOWA webhook retry menyebabkan double/triple reply
# {msg_id_wa: timestamp} — entri dihapus setelah 5 menit
import time as _time
_PROCESSED_MSG_IDS: dict = {}
_DEDUP_TTL = 300  # 5 menit

def _is_duplicate(msg_id: str) -> bool:
    """Return True jika msg_id sudah diproses dalam 5 menit terakhir"""
    if not msg_id:
        return False
    now = _time.time()
    expired = [k for k, v in _PROCESSED_MSG_IDS.items() if now - v > _DEDUP_TTL]
    for k in expired:
        del _PROCESSED_MSG_IDS[k]
    if msg_id in _PROCESSED_MSG_IDS:
        return True
    _PROCESSED_MSG_IDS[msg_id] = now
    return False

# Staff-handled tracker: kalau karyawan sudah balas ke JID ini, bot diam dulu
# {chat_jid: timestamp_last_staff_reply}
_STAFF_LAST_REPLY: dict = {}
STAFF_COOLDOWN_SEC = 1800  # 30 menit — bot diam setelah karyawan reply

# Default reply cooldown: jangan kirim default reply berulang ke nomor yg sama
_DEFAULT_REPLY_SENT: dict = {}  # {sender: timestamp}
DEFAULT_REPLY_COOLDOWN = 600  # 10 menit

def _can_send_default(sender: str) -> bool:
    """Return True jika belum kirim default reply ke sender dalam 10 menit"""
    now = _time.time()
    last = _DEFAULT_REPLY_SENT.get(sender, 0)
    if now - last < DEFAULT_REPLY_COOLDOWN:
        return False
    _DEFAULT_REPLY_SENT[sender] = now
    return True

def _mark_staff_replied(jid: str):
    """Catat bahwa karyawan baru saja reply ke JID ini"""
    _STAFF_LAST_REPLY[jid] = _time.time()

def _staff_is_handling(jid: str) -> bool:
    """Return True jika karyawan reply ke JID ini dalam 30 menit terakhir"""
    last = _STAFF_LAST_REPLY.get(jid, 0)
    return (_time.time() - last) < STAFF_COOLDOWN_SEC

# Keywords indikasi komplain pelanggan → trigger eskalasi
COMPLAINT_KEYWORDS = [
    # Ekspresi kekecewaan
    "komplain", "kecewa", "kecewa", "tidak puas", "ga puas", "gak puas",
    "nggak puas", "ngga puas",
    # Masalah hasil laundry
    "rusak", "sobek", "hilang", "luntur", "bau", "kotor", "belum bersih",
    "masih kotor", "masih bau", "tidak bersih", "gak bersih",
    # Masalah waktu / layanan
    "lama", "lambat", "telat", "terlambat", "belum selesai", "belum jadi",
    "belum datang", "belum diantar", "belum dijemput", "kapan selesai",
    "kapan jadi", "kapan diantar",
    # Masalah harga / tagihan
    "kemahalan", "terlalu mahal", "salah tagih", "tagihan salah",
    "harga beda", "harga tidak sesuai",
    # Ekspresi keras
    "kecewa banget", "sangat kecewa", "tidak profesional", "gak profesional",
    "buruk", "jelek", "mengecewakan", "bohong", "tipu", "menipu",
    "mau refund", "kembalikan uang", "cancel", "batalkan",
]

# Reply default untuk pesan non-keyword, non-komplain
AUTO_REPLY_DEFAULT = (
    "Halo Kak! 👋 Pesan kamu sudah kami terima.\n"
    "Tim kami akan segera membalas ya 🙏"
)

# Keywords status order — intercept sebelum LLM (LLM tidak bisa akses DB order)
ORDER_STATUS_KEYWORDS = [
    "sudah selesai", "sudah jadi", "sudah beres", "udah selesai", "udah jadi",
    "laundry saya", "cucian saya", "order saya", "pesanan saya",
    "kapan selesai", "kapan jadi", "kapan bisa diambil", "kapan bisa dijemput",
    "selesai belum", "jadi belum", "beres belum", "sudah bisa",
    "cek order", "cek pesanan", "status order", "status laundry",
    "sudah dikirim", "sudah diantar", "sudah di antar",
    "besok selesai", "selesaikah", "bisa besok", "besok bisa",
    "besok jadi", "besok sudah", "kapan siap", "siap besok",
    "bisa diambil besok", "besok bisa diambil",
]

# Reply untuk pesan yang tidak jelas itemnya ("bisa cuci ini?", kirim foto)
ASK_ITEM_REPLY = (
    "Halo Kak! 😊 Boleh disebutkan barang apa yang mau dicuci/dilaundry?\n"
    "Nanti kami langsung cek layanan dan harganya ya!"
)

ASK_ITEM_KEYWORDS = [
    "cuci ini", "laundry ini", "ini bisa", "bisa dicuci", "bisa dilaundry",
    "ini laundry", "ini cuci", "bisa cuci gak", "bisa laundry gak",
    "cuci apa ini", "ini apa bisa", "terima ini", "bisa terima ini",
]


def is_vague_item_query(message: str) -> bool:
    """Deteksi pertanyaan item tidak jelas — customer tidak sebut nama barang."""
    msg_lower = message.lower().strip()
    # Cocok dengan keyword vague
    if any(kw in msg_lower for kw in ASK_ITEM_KEYWORDS):
        return True
    # Pesan sangat pendek dengan "ini" + kata tanya (max 6 kata)
    words = msg_lower.split()
    if len(words) <= 6 and "ini" in words and any(q in msg_lower for q in ["bisa", "boleh", "cuci", "laundry"]):
        return True
    return False


ORDER_STATUS_REPLY = (
    "Halo Kak! 👋 Untuk cek status laundry, tim kami akan segera konfirmasi ya.\n"
    "Mohon ditunggu sebentar 🙏"
)


def is_order_status_query(message: str) -> bool:
    """Deteksi pertanyaan status order — jangan sampai LLM yang jawab (bisa halusinasi)"""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in ORDER_STATUS_KEYWORDS)


def is_complaint(message: str) -> bool:
    """Detect complaint indicators in customer message"""
    msg_lower = message.lower().strip()
    return any(kw in msg_lower for kw in COMPLAINT_KEYWORDS)


def get_time_greeting() -> str:
    """Return sapaan berdasarkan jam WIB (UTC+7)"""
    from datetime import datetime, timezone, timedelta
    wib = datetime.now(timezone(timedelta(hours=7)))
    hour = wib.hour
    if 5 <= hour < 12:
        return "Selamat pagi"
    elif 12 <= hour < 15:
        return "Selamat siang"
    elif 15 <= hour < 19:
        return "Selamat sore"
    else:
        return "Selamat malam"


def _extract_salutation(name: str) -> str:
    """Ekstrak salutation Pak/Bu dari nama, atau Kak sebagai default."""
    if not name:
        return "Kak"
    n = name.strip()
    nl = n.lower()
    # Nama sudah ada prefix Pak/Bu/Ibu/Bapak
    for prefix in ["ibu ", "bu "]:
        if nl.startswith(prefix):
            rest = n[len(prefix):].strip().split()
            short = rest[0] if rest else ""
            return f"Bu {short}" if short else "Bu"
    for prefix in ["bapak ", "pak "]:
        if nl.startswith(prefix):
            rest = n[len(prefix):].strip().split()
            short = rest[0] if rest else ""
            return f"Pak {short}" if short else "Pak"
    # Nama polos — pakai Kak (gender neutral)
    parts = n.split()
    short = parts[0] if parts else n
    return f"Kak {short}"


def build_greeting(cust_name: str, segment: str) -> str:
    """
    Return greeting line untuk customer dikenal.
    VIP: nama lengkap + emoji khusus.
    Reguler/Baru: sapaan standar.
    Sapaan: Pak/Bu jika ada prefix di nama, Kak jika tidak.
    """
    sapa = get_time_greeting()
    if not cust_name:
        return ""
    salut = _extract_salutation(cust_name)
    if segment == "VIP":
        return f"{sapa} {salut}! 😊✨"
    return f"{sapa} {salut}! 😊"


# Landing page karir
KARIR_URL = "https://sijibintaro.id/karir"

# Job application keywords
JOB_KEYWORDS = ["lamar", "kerja", "lowongan", "pelamar", "apply", "hiring", "rekrut", "karyawan baru"]

# Auto-reply untuk nomor tidak dikenal
AUTO_REPLY_UNKNOWN = (
    "Halo! Terima kasih sudah menghubungi SIJI.Bintaro 👋\n\n"
    "Untuk layanan laundry dan pertanyaan umum, silakan chat ke nomor customer service kami.\n\n"
    "Sedang mencari info lowongan kerja? Cek di sini:\n"
    "👉 {karir_url}\n\n"
    "Tim kami akan segera menghubungi Anda. Terima kasih! 🙏"
).format(karir_url=KARIR_URL)

AUTO_REPLY_JOB = (
    "Halo! Terima kasih sudah tertarik bergabung dengan SIJI.Bintaro 🙌\n\n"
    "Silakan lengkapi form lamaran di sini:\n"
    "👉 {karir_url}\n\n"
    "Tim kami akan menghubungi Anda jika ada posisi yang sesuai. Terima kasih! 💪"
).format(karir_url=KARIR_URL)

# === KATALOG LAYANAN (Layer 2.5) ===
# Pakai ChromaDB similarity search dari collection siji_services (61 item dari DB)
# Lebih robust: handle variasi bahasa, typo, bahasa Inggris, sinonim
COLLECTION_SERVICES = "siji_services"
SERVICES_COLLECTION_ID = None  # di-cache saat pertama kali dipanggil
SERVICE_SIMILARITY_THRESHOLD = 0.70  # min score untuk dianggap match

# === KATALOG LAYANAN (Layer 2.5) ===
# Deteksi "bisa cuci X?" → jawab langsung dari katalog, tanpa LLM
# SERVICE_CATALOG hardcode di bawah masih ada sebagai reference,
# tapi check_service_catalog() sekarang pakai ChromaDB similarity.
# Update layanan cukup di service_catalog DB + re-populate siji_services collection.
SERVICE_CATALOG = {  # DEPRECATED — gunakan siji_services ChromaDB
    # Kiloan
    "kiloan":    ("cuci kering setrika reguler", "Rp16.000/kg (min 3kg, 3 hari)"),
    "setrika":   ("setrika kiloan reguler", "Rp12.000/kg (min 3kg, 3 hari)"),
    # Household
    "karpet":    ("karpet", "Rp35.000/m² (10 hari)"),
    "carpet":    ("karpet", "Rp35.000/m² (10 hari)"),
    "permadani": ("karpet", "Rp35.000/m² (10 hari)"),
    "gordyn":    ("gordyn", "Rp16.000/m² (tebal/blackout), Rp10.000/m² (tipis/vetrase)"),
    "gorden":    ("gordyn", "Rp16.000/m² (tebal/blackout), Rp10.000/m² (tipis/vetrase)"),
    "sofa":      ("sarung sofa", "Rp30.000/m²"),
    # Bedding
    "stroller":  ("baby stroller", "Rp250.000/unit (6 hari)"),
    "bedcover":  ("bedcover", "Rp70.000/lembar (3 hari), Express 24 jam Rp115.000"),
    "bed cover": ("bedcover", "Rp70.000/lembar (3 hari), Express 24 jam Rp115.000"),
    "sprei":     ("sprei 1 set", "Rp35.000/set (3 hari), Express 24 jam Rp55.000"),
    "bantal":    ("bantal/guling", "Rp40.000 (kecil), Rp60.000 (besar/guling)"),
    "guling":    ("bantal/guling", "Rp40.000 (kecil), Rp60.000 (besar/guling)"),
    "kasur":     ("kasur/matras", "Rp95.000/unit (matras tipis), Rp400.000 (kasur lipat)"),
    "matras":    ("kasur/matras", "Rp95.000/unit (matras tipis), Rp400.000 (kasur lipat)"),
    "boneka":    ("boneka", "Rp40.000 (kecil), Rp100.000 (besar)"),
    # Sepatu
    "sepatu":    ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "shoes":     ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "shoe":      ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "sneakers":  ("sepatu", "Rp90.000/pasang (reguler, 3 hari), Rp150.000 (kulit/boot, 4 hari)"),
    "boot":      ("sepatu boot", "Rp150.000/pcs (4 hari)"),
    "helm":      ("helm", "Rp80.000/pcs (3 hari)"),
    # Tas
    "tas":       ("tas", "Rp140.000 (reguler), Rp250.000 (USA brand), Rp500.000 (EU brand/LV/Gucci)"),
    "bag":       ("tas", "Rp140.000 (reguler), Rp250.000 (USA brand), Rp500.000 (EU brand/LV/Gucci)"),
    "handbag":   ("tas", "Rp140.000 (reguler), Rp250.000 (USA brand), Rp500.000 (EU brand/LV/Gucci)"),
    "dompet":    ("dompet", "Rp100.000 (reguler), Rp200.000 (USA brand), Rp350.000 (EU brand)"),
    "ransel":    ("tas gunung/ransel", "Rp200.000/pcs (5 hari)"),
    # Dry clean / Pakaian
    "blazer":    ("blazer/jaket", "Rp65.000/pcs (3 hari)"),
    "jaket":     ("blazer/jaket", "Rp65.000/pcs biasa, Rp150.000 (kulit, 12 hari)"),
    "jas":       ("dry clean blazer/jas", "Rp80.000/pcs (4 hari)"),
    "kulit":     ("pakaian/jaket kulit", "Rp150.000/pcs (12 hari)"),
    "dress":     ("dress/kebaya/brokat", "Rp100.000/pcs (4 hari)"),
    "kebaya":    ("dress/kebaya/brokat", "Rp100.000/pcs (4 hari)"),
    "topi":      ("cuci topi", "Rp65.000/pcs (4 hari)"),
    # Lainnya
    "koper":     ("koper", "Rp190.000/unit (4 hari)"),
    "sleeping":  ("sleeping bag", "Rp90.000/pcs (5 hari)"),
}

# Kata tanya / pertanyaan yang menandakan customer butuh info layanan
# Keyword auto-replies
KEYWORD_REPLIES = {
    "harga": (
        "Halo! Ini daftar harga SIJI.Bintaro 👕\n\n"
        "*KILOAN (min. 3kg):*\n"
        "🧺 Cuci Kering Setrika Reguler: Rp 16.000/kg (3 hari)\n"
        "👔 Cuci Kering Lipat Reguler: Rp 12.000/kg (3 hari)\n"
        "🔥 Setrika Kiloan Reguler: Rp 12.000/kg (3 hari)\n\n"
        "*EXPRESS:*\n"
        "⚡ Cuci Kering Setrika Express 24 jam: Rp 30.000/kg\n"
        "🚀 Same Day 10 jam: Rp 36.000/kg\n\n"
        "*SATUAN:*\n"
        "👗 Laundry Satuan Reguler: Rp 40.000/pcs\n"
        "🛏️ Bedcover: Rp 70.000/lembar\n"
        "🛏️ Sprei 1 Set: Rp 35.000/paket\n"
        "🥿 Sepatu Reguler: Rp 90.000/pasang\n\n"
        "Info lengkap & order: wa.me/6281288783088 😊"
    ),
    "jam": (
        "Jam operasional SIJI.Bintaro ⏰\n\n"
        "Senin - Sabtu: 08.00 - 20.00\n"
        "Minggu: 08.00 - 16.00\n\n"
        "Tersedia layanan antar jemput oleh kurir kami! 😊"
    ),
    "lokasi": (
        "📍 SIJI.Bintaro\n"
        "Jl. Raya Emerald Boulevard, BLOK CE/A1 No.5\n"
        "(Ruko PHD, Sebelah Marchand), Bintaro Jaya\n\n"
        "Google Maps: https://maps.app.goo.gl/sijibintaro\n"
        "Ditunggu ya Kak! 😊"
    ),
    "promo": (
        "Promo SIJI.Bintaro bulan ini 🎁\n\n"
        "Cek update terbaru di Instagram kami:\n"
        "@siji.bintaro\n\n"
        "Atau tanya langsung aja ya Kak! 😊"
    ),
    "antar_jemput": (
        "Halo Kak! SIJI Bintaro ada layanan *antar jemput FREE* "
        "untuk area dalam radius *3 km dari outlet* kami (via jalur jalan).\n\n"
        "Emerald, Discovery, Kebayoran dan sekitarnya — *FREE* ya Kak! 🎉\n"
        "Nanti kurir kami yang akan pick up & antar ke Kak 😊\n"
        "Di luar area itu? Konfirmasi dulu ya, kami bantu cek.\n\n"
        "📍 Outlet: Jl. Raya Emerald Boulevard, BLOK CE/A1 No.5 (Ruko PHD)\n"
        "📞 Chat kami: wa.me/6281288783088"
    ),
}

# Keywords that trigger each reply
# Urutan penting: lebih spesifik dulu (jam/lokasi sebelum harga)
KEYWORD_MAP = {
    "jam": ["jam buka", "jam tutup", "buka jam", "tutup jam", "jam operasional",
            "jam kerja", "buka pukul", " buka ", "masih buka", "sudah tutup",
            "hari ini buka", "buka hari", "jam berapa buka"],
    "lokasi": ["lokasi", "alamat", "dimana", "di mana", "maps", "map",
               "google maps", "tempat", "di bintaro"],
    "harga": ["harga", "price", "tarif", "biaya", "berapa harga", "berapa tarif",
              "berapa biaya", "harga cuci", "harga laundry", "harga kiloan",
              "harga bedcover", "harga sepatu", "harga tas", "daftar harga"],
    "promo": ["promo", "diskon", "discount", "voucher", "promo apa"],
    "antar_jemput": ["antar jemput", "antar-jemput", "pickup", "jemput laundry",
                     "ambil laundry", "diantar", "dijemput", "layanan antar",
                     "bisa antar", "bisa jemput", "ada jemput", "ada antar",
                     "free antar", "gratis antar", "gratis gak", "free gak",
                     "wilayah emerald", "area emerald", "emerald gratis",
                     "ke emerald", "jemput gratis", "antar gratis",
                     "wilayah bintaro", "area bintaro", "ongkir", "biaya antar",
                     "biaya jemput", "kena ongkos", "bayar ongkos"],
}


def match_keyword(message: str) -> str | None:
    """Match message to keyword category — checks in order, returns first match"""
    msg_lower = " " + message.lower().strip() + " "  # pad for word-boundary check
    for category, keywords in KEYWORD_MAP.items():
        for kw in keywords:
            if kw in msg_lower:
                return category
    return None


def is_whitelisted(sender: str) -> bool:
    """Check if sender is in the whitelist"""
    # Normalize: strip +, spaces
    normalized = sender.replace("+", "").replace(" ", "").strip()
    return normalized in ALLOWED_NUMBERS


def is_job_application(message: str) -> bool:
    """Detect if message is about job application"""
    msg_lower = message.lower().strip()
    return any(kw in msg_lower for kw in JOB_KEYWORDS)


# ─── Presensi (Attendance) Handler ───────────────────────────────────────────

PRESENSI_MASUK_KW  = ["hadir", "masuk", "checkin", "check in", "absen masuk", "absen"]
PRESENSI_PULANG_KW = ["pulang", "keluar", "checkout", "check out", "absen pulang"]
PRESENSI_IZIN_KW   = ["izin"]
PRESENSI_SAKIT_KW  = ["sakit"]


def normalize_wa(number: str) -> str:
    """Normalize WA number to 62xxx format"""
    n = number.replace("+", "").replace("-", "").replace(" ", "").strip()
    if n.startswith("0"):
        n = "62" + n[1:]
    return n


async def handle_presensi(conn, sender: str, message: str) -> bool:
    """
    Check if sender is an active karyawan and message is attendance-related.
    Returns True if handled (presensi recorded + reply sent).
    """
    from datetime import datetime as dt
    msg_lower = message.lower().strip()

    is_masuk  = any(kw in msg_lower for kw in PRESENSI_MASUK_KW)
    is_pulang = any(kw in msg_lower for kw in PRESENSI_PULANG_KW)
    is_izin   = any(kw in msg_lower for kw in PRESENSI_IZIN_KW)
    is_sakit  = any(kw in msg_lower for kw in PRESENSI_SAKIT_KW)

    if not any([is_masuk, is_pulang, is_izin, is_sakit]):
        return False

    # Check if sender is a registered active karyawan
    normalized = normalize_wa(sender)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, nama, posisi FROM karyawan
           WHERE status_kerja='aktif'
           AND REPLACE(REPLACE(REPLACE(whatsapp,'+',''),'-',''),' ','') = ?""",
        (normalized,)
    )
    k = cursor.fetchone()
    if not k:
        return False  # Not a karyawan, skip presensi handling

    karyawan_id = k["id"]
    nama        = k["nama"]
    today       = dt.now().strftime("%Y-%m-%d")
    now_time    = dt.now().strftime("%H:%M")
    reply       = None

    if is_masuk and not is_pulang:
        cursor.execute(
            "SELECT id FROM presensi WHERE karyawan_id=? AND tanggal=? AND tipe='hadir'",
            (karyawan_id, today)
        )
        existing = cursor.fetchone()
        if existing:
            reply = (
                f"Halo {nama}! Absen masuk kamu hari ini sudah tercatat ✅\n"
                f"Selamat bekerja! 💛"
            )
        else:
            conn.execute(
                "INSERT INTO presensi (karyawan_id, tanggal, jam_masuk, tipe, sumber) VALUES (?,?,?,?,?)",
                (karyawan_id, today, now_time, "hadir", "wa")
            )
            conn.commit()
            reply = (
                f"✅ *Absen Masuk Tercatat!*\n\n"
                f"👤 {nama}\n"
                f"💼 {k['posisi']}\n"
                f"🕐 Jam masuk: *{now_time}*\n"
                f"📅 {today}\n\n"
                f"Selamat bekerja! 💪"
            )

    elif is_pulang and not is_masuk:
        cursor.execute(
            "SELECT id, jam_masuk FROM presensi WHERE karyawan_id=? AND tanggal=? AND tipe='hadir' AND jam_keluar IS NULL",
            (karyawan_id, today)
        )
        existing = cursor.fetchone()
        if existing:
            conn.execute(
                "UPDATE presensi SET jam_keluar=? WHERE id=?",
                (now_time, existing["id"])
            )
            conn.commit()
            reply = (
                f"✅ *Absen Pulang Tercatat!*\n\n"
                f"👤 {nama}\n"
                f"🕐 Masuk: {existing['jam_masuk']} → Pulang: *{now_time}*\n"
                f"📅 {today}\n\n"
                f"Hati-hati di jalan ya! 🙏"
            )
        else:
            reply = (
                f"Halo {nama}! Belum ada absen masuk hari ini.\n"
                f"Kirim 'hadir' atau 'masuk' dulu ya 😊"
            )

    elif is_izin:
        cursor.execute(
            "SELECT id FROM presensi WHERE karyawan_id=? AND tanggal=? AND tipe='izin'",
            (karyawan_id, today)
        )
        if not cursor.fetchone():
            catatan_izin = message if len(message) > 4 else None
            conn.execute(
                "INSERT INTO presensi (karyawan_id, tanggal, tipe, sumber, catatan) VALUES (?,?,?,?,?)",
                (karyawan_id, today, "izin", "wa", catatan_izin)
            )
            conn.commit()
        reply = (
            f"✅ *Izin Tercatat!*\n\n"
            f"👤 {nama}\n"
            f"📅 {today}\n\n"
            f"Semoga lancar urusannya ya! 🙏"
        )

    elif is_sakit:
        cursor.execute(
            "SELECT id FROM presensi WHERE karyawan_id=? AND tanggal=? AND tipe='sakit'",
            (karyawan_id, today)
        )
        if not cursor.fetchone():
            conn.execute(
                "INSERT INTO presensi (karyawan_id, tanggal, tipe, sumber, catatan) VALUES (?,?,?,?,?)",
                (karyawan_id, today, "sakit", "wa", message)
            )
            conn.commit()
        reply = (
            f"✅ *Sakit Tercatat!*\n\n"
            f"👤 {nama}\n"
            f"📅 {today}\n\n"
            f"Semoga cepat sembuh ya! 🤒\n"
            f"Istirahat yang cukup!"
        )

    if reply:
        await send_fonnte_message(sender, reply)
        log_message(
            conn=conn,
            wa_number=FONNTE_DEVICE,
            sender=FONNTE_DEVICE,
            recipient=sender,
            direction="outbound",
            message=reply,
            category="presensi",
            replied_by="auto"
        )
        return True

    return False

# ─── End Presensi Handler ────────────────────────────────────────────────────


async def forward_to_admins(sender: str, message: str, media_url: str = None):
    """Forward unknown sender message to admin numbers"""
    notif = (
        f"⚠️ *Pesan dari nomor tidak dikenal*\n\n"
        f"Dari: +{sender}\n"
        f"Pesan: {message[:300]}"
    )
    if media_url:
        notif += f"\nMedia: {media_url}"

    for admin in ADMIN_NUMBERS:
        await send_fonnte_message(admin, notif)


async def notify_telegram(sender: str, message: str, category: str = "", routing: str = ""):
    """Send Telegram notification to admin when inbound WA message arrives"""
    try:
        route_label = {
            "whitelist": "Whitelist",
            "job": "Lamaran kerja",
            "unknown": "Nomor tidak dikenal"
        }.get(routing, routing or "-")

        cat_label = " | " + category if category else ""
        now_str = datetime.now().strftime("%d %b %Y %H:%M")

        lines = [
            "WA Masuk - SIJI.Bintaro",
            "",
            "Dari: +" + sender,
            "Pesan: " + message[:300],
            "Routing: " + route_label + cat_label,
            "Waktu: " + now_str + " WIB",
        ]
        text = "\n".join(lines)

        async with httpx.AsyncClient() as client:
            await client.post(
                TELEGRAM_API_URL,
                json={"chat_id": TELEGRAM_ADMIN_CHAT_ID, "text": text},
                timeout=10.0
            )
    except Exception as e:
        print(f"[Telegram notify error] {e}")



async def send_gowa_message(phone: str, message: str) -> dict:
    """Send WA message via GOWA API (self-hosted, port 3002)"""
    url = f"{GOWA_BASE}/send/message"
    payload = {"phone": phone, "message": message}
    headers = {"X-Device-Id": GOWA_DEVICE_ID}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, json=payload, headers=headers,
                auth=GOWA_AUTH, timeout=10
            )
            result = resp.json()
            print(f"[GOWA Send] → {phone}: {message[:50]} | resp: {result}")
            return result
    except Exception as e:
        print(f"[GOWA Send Error] {phone}: {e}")
        return {"error": str(e)}

async def send_fonnte_message(target: str, message: str, url: str = None) -> dict:
    """Send message via Fonnte API"""
    if not FONNTE_TOKEN:
        return {"error": "FONNTE_TOKEN not configured"}
    
    payload = {
        "target": target,
        "message": message,
        "delay": "2",
    }
    if url:
        payload["url"] = url
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FONNTE_API_URL,
            headers={"Authorization": FONNTE_TOKEN},
            data=payload,
            timeout=30.0
        )
        return resp.json()


def init_wa_tables(conn):
    """Create WA-related tables if not exist"""
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wa_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wa_number TEXT NOT NULL,
            sender TEXT NOT NULL,
            recipient TEXT,
            direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
            message TEXT,
            media_url TEXT,
            media_filename TEXT,
            media_extension TEXT,
            wa_timestamp TEXT,
            inbox_id TEXT,
            group_member TEXT,
            category TEXT,
            replied_by TEXT,
            response_time_sec INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrate existing tables: add new columns if missing
    cursor.execute("PRAGMA table_info(wa_conversations)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    for col, coltype in [("media_extension", "TEXT"), ("wa_timestamp", "TEXT"),
                         ("inbox_id", "TEXT"), ("group_member", "TEXT")]:
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE wa_conversations ADD COLUMN {col} {coltype}")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wa_customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            no_hp TEXT UNIQUE NOT NULL,
            nama TEXT,
            alamat TEXT,
            segment TEXT DEFAULT 'Baru',
            total_messages INTEGER DEFAULT 0,
            first_contact TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_contact TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Indexes — wrap individually to handle schema mismatch gracefully
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_conv_sender ON wa_conversations(sender)")
    except Exception:
        pass  # Column may not exist in new-schema DB
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_conv_date ON wa_conversations(created_at)")
    except Exception:
        pass
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wa_cust_hp ON wa_customers(no_hp)")
    except Exception:
        pass
    
    conn.commit()


def log_message(conn, wa_number: str, sender: str, recipient: str,
                direction: str, message: str, media_url: str = None,
                media_filename: str = None, category: str = None,
                replied_by: str = None, media_extension: str = None,
                wa_timestamp: str = None, inbox_id: str = None,
                group_member: str = None):
    """Log a message to wa_conversations"""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO wa_conversations
        (wa_number, sender, recipient, direction, message, media_url, media_filename,
         media_extension, wa_timestamp, inbox_id, group_member, category, replied_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (wa_number, sender, recipient, direction, message, media_url, media_filename,
          media_extension, wa_timestamp, inbox_id, group_member, category, replied_by))
    conn.commit()
    return cursor.lastrowid


def upsert_customer(conn, no_hp: str, name: str = ""):
    """Find or create customer by phone number"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM wa_customers WHERE no_hp = ?", (no_hp,))
    customer = cursor.fetchone()

    if customer:
        if name and not customer["nama"]:
            cursor.execute("""
                UPDATE wa_customers
                SET total_messages = total_messages + 1, last_contact = CURRENT_TIMESTAMP, nama = ?
                WHERE no_hp = ?
            """, (name, no_hp))
        else:
            cursor.execute("""
                UPDATE wa_customers
                SET total_messages = total_messages + 1, last_contact = CURRENT_TIMESTAMP
                WHERE no_hp = ?
            """, (no_hp,))
    else:
        cursor.execute("""
            INSERT INTO wa_customers (no_hp, nama, total_messages) VALUES (?, ?, 1)
        """, (no_hp, name if name else None))
    
    conn.commit()
    cursor.execute("SELECT * FROM wa_customers WHERE no_hp = ?", (no_hp,))
    return cursor.fetchone()


# === ROUTES ===

@router.api_route("/webhook/{token}", methods=["GET", "POST"])
async def fonnte_webhook(request: Request, token: str):
    # Validate webhook token
    if not WEBHOOK_SECRET or token != WEBHOOK_SECRET:
        return {"status": "unauthorized"}
    import sqlite3
    
    try:
        # Fonnte sends JSON (not form data) — try JSON first, fallback to form
        try:
            data = await request.json()
        except Exception:
            data = dict(await request.form())

        sender = data.get("sender", "")
        message = data.get("message", "")
        name = data.get("name", "")
        device = data.get("device", "")
        media_url = data.get("url", "")
        filename = data.get("filename", "")
        # Fonnte "all feature" package fields
        extension = data.get("extension", "")
        timestamp = data.get("timestamp", "")
        inboxid = data.get("inboxid", "")
        member = data.get("member", "")

        # Display-friendly message for non-text (media-only) messages
        if not message and media_url:
            message = f"[{extension or 'file'}: {filename or 'attachment'}]"

        if not sender or not message:
            # Could be status callback, ignore
            return {"status": "ignored", "reason": "no sender or message"}
        
        # Connect to DB
        conn = sqlite3.connect("/opt/siji-dashboard/siji_database.db")
        conn.row_factory = sqlite3.Row
        init_wa_tables(conn)
        
        try:
            # 1. Upsert customer
            customer = upsert_customer(conn, sender, name=name)

            # 2. === CONDITIONAL ROUTING ===
            whitelisted = is_whitelisted(sender)
            job_inquiry = is_job_application(message)
            category = match_keyword(message)
            reply_sent = False
            routing = "whitelist" if whitelisted else ("job" if job_inquiry else "unknown")

            # 3. Log inbound message
            msg_id = log_message(
                conn=conn,
                wa_number=device or FONNTE_DEVICE,
                sender=sender,
                recipient=device or FONNTE_DEVICE,
                direction="inbound",
                message=message,
                media_url=media_url if media_url else None,
                media_filename=filename if filename else None,
                media_extension=extension if extension else None,
                wa_timestamp=timestamp if timestamp else None,
                inbox_id=inboxid if inboxid else None,
                group_member=member if member else None,
                category=category or routing
            )

            # 4. === PRESENSI CHECK (priority: runs before whitelist routing) ===
            if AUTOREPLY_ENABLED:
                presensi_handled = await handle_presensi(conn, sender, message)
                if presensi_handled:
                    reply_sent = True

            if reply_sent:
                pass  # presensi handled — skip further routing
            elif whitelisted:
                # === WHITELIST: full interaction, keyword auto-reply only ===
                if AUTOREPLY_ENABLED and category and category in KEYWORD_REPLIES:
                    reply_text = KEYWORD_REPLIES[category]
                    await send_fonnte_message(sender, reply_text)
                    log_message(
                        conn=conn,
                        wa_number=FONNTE_DEVICE,
                        sender=FONNTE_DEVICE,
                        recipient=sender,
                        direction="outbound",
                        message=reply_text,
                        category=category,
                        replied_by="auto"
                    )
                    reply_sent = True

            elif job_inquiry:
                # === PELAMAR: auto-reply ke landing page karir ===
                if AUTOREPLY_ENABLED:
                    await send_fonnte_message(sender, AUTO_REPLY_JOB)
                    log_message(
                        conn=conn,
                        wa_number=FONNTE_DEVICE,
                        sender=FONNTE_DEVICE,
                        recipient=sender,
                        direction="outbound",
                        message=AUTO_REPLY_JOB,
                        category="job_application",
                        replied_by="auto"
                    )
                    await forward_to_admins(sender, message, media_url or None)
                    reply_sent = True

            else:
                # === NOMOR TIDAK DIKENAL: auto-reply + forward ke admin ===
                if AUTOREPLY_ENABLED:
                    await send_fonnte_message(sender, AUTO_REPLY_UNKNOWN)
                    log_message(
                        conn=conn,
                        wa_number=FONNTE_DEVICE,
                        sender=FONNTE_DEVICE,
                        recipient=sender,
                        direction="outbound",
                        message=AUTO_REPLY_UNKNOWN,
                        category="unknown",
                        replied_by="auto"
                    )
                    await forward_to_admins(sender, message, media_url or None)
                    reply_sent = True

            return {
                "status": "ok",
                "message_id": msg_id,
                "customer_hp": sender,
                "routing": routing,
                "category": category,
                "auto_replied": reply_sent
            }
        
        finally:
            conn.close()
    
    except Exception as e:
        # Log error but don't crash — Fonnte needs 200 response
        print(f"[WA Webhook Error] {e}")
        return {"status": "error", "detail": str(e)}


@router.post("/send")
async def send_wa_message(request: Request):
    """
    Send outbound message via Fonnte + log to DB.
    Body: { "target": "628xxx", "message": "...", "url": "optional media", "sent_by": "staff|system" }
    """
    import sqlite3
    
    data = await request.json()
    target = data.get("target")
    message = data.get("message")
    media_url = data.get("url")
    sent_by = data.get("sent_by", "staff")
    
    if not target or not message:
        raise HTTPException(status_code=400, detail="target and message required")
    
    # Send via Fonnte
    result = await send_fonnte_message(target, message, url=media_url)
    
    # Log to DB
    conn = sqlite3.connect("/opt/siji-dashboard/siji_database.db")
    conn.row_factory = sqlite3.Row
    init_wa_tables(conn)
    
    try:
        log_message(
            conn=conn,
            wa_number=FONNTE_DEVICE,
            sender=FONNTE_DEVICE,
            recipient=target,
            direction="outbound",
            message=message,
            media_url=media_url,
            replied_by=sent_by
        )
    finally:
        conn.close()
    
    return {"status": "ok", "fonnte_response": result}


@router.get("/conversations/{phone}")
async def get_conversations(phone: str, limit: int = 50):
    """Get conversation history for a phone number"""
    import sqlite3
    
    conn = sqlite3.connect("/opt/siji-dashboard/siji_database.db")
    conn.row_factory = sqlite3.Row
    init_wa_tables(conn)
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM wa_conversations 
            WHERE sender = ? OR recipient = ?
            ORDER BY created_at DESC 
            LIMIT ?
        """, (phone, phone, limit))
        
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/customers")
async def get_wa_customers(limit: int = 100):
    """Get all WA customers"""
    import sqlite3
    
    conn = sqlite3.connect("/opt/siji-dashboard/siji_database.db")
    conn.row_factory = sqlite3.Row
    init_wa_tables(conn)
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM wa_customers 
            ORDER BY last_contact DESC 
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/stats")
async def get_wa_stats():
    """Get WA messaging statistics"""
    import sqlite3
    
    conn = sqlite3.connect("/opt/siji-dashboard/siji_database.db")
    conn.row_factory = sqlite3.Row
    init_wa_tables(conn)
    
    try:
        cursor = conn.cursor()
        
        # Total messages today
        cursor.execute("""
            SELECT COUNT(*) as total FROM wa_conversations 
            WHERE date(created_at) = date('now')
        """)
        today_total = cursor.fetchone()["total"]
        
        # Inbound vs outbound today
        cursor.execute("""
            SELECT direction, COUNT(*) as count FROM wa_conversations 
            WHERE date(created_at) = date('now')
            GROUP BY direction
        """)
        direction_stats = {row["direction"]: row["count"] for row in cursor.fetchall()}
        
        # Total customers
        cursor.execute("SELECT COUNT(*) as total FROM wa_customers")
        total_customers = cursor.fetchone()["total"]
        
        # Auto-reply rate today
        cursor.execute("""
            SELECT COUNT(*) as total FROM wa_conversations 
            WHERE date(created_at) = date('now') AND replied_by = 'auto'
        """)
        auto_replies = cursor.fetchone()["total"]
        
        # Category breakdown today
        cursor.execute("""
            SELECT category, COUNT(*) as count FROM wa_conversations 
            WHERE date(created_at) = date('now') AND direction = 'inbound'
            GROUP BY category
        """)
        categories = {row["category"]: row["count"] for row in cursor.fetchall()}
        
        return {
            "today": {
                "total_messages": today_total,
                "inbound": direction_stats.get("inbound", 0),
                "outbound": direction_stats.get("outbound", 0),
                "auto_replies": auto_replies,
            },
            "total_customers": total_customers,
            "categories_today": categories,
        }
    finally:
        conn.close()


# === DB PATHS ===
WA_DB = "/opt/siji-dashboard/siji_database.db"  # FIXED: was siji.db
TX_DB = "/opt/siji-dashboard/siji_database.db"


def normalize_phone(phone) -> str:
    """Normalize phone number: strip .0 suffix, +62 prefix, convert 08xx to 628xx"""
    if phone is None:
        return ""
    s = str(phone).strip()
    # Remove trailing .0 (float artifact from Excel)
    if s.endswith(".0"):
        s = s[:-2]
    # Strip non-digit except leading +
    s = re.sub(r"[^\d+]", "", s)
    # Remove +
    s = s.replace("+", "")
    # Convert 08xx to 628xx
    if s.startswith("0"):
        s = "62" + s[1:]
    return s


def _classify_customer(orders, today):
    """Classify a customer into exactly one pipeline bucket based on their orders.
    Priority: belum_lunas > proses > siap_ambil > churn_risk > returning > selesai
    Returns bucket name string.
    """
    if not orders:
        return "lead"

    has_unpaid = False
    has_in_progress = False
    has_ready_pickup = False
    total_completed = 0
    last_order_date = None

    for o in orders:
        odate = o.get("date_of_transaction") or ""
        try:
            d = datetime.strptime(odate[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            d = None

        if last_order_date is None or (d and d > last_order_date):
            last_order_date = d

        pembayaran = (o.get("pembayaran") or "").strip()
        progress = (o.get("progress_status") or "").strip()
        pengambilan = (o.get("pengambilan") or "").strip()

        if pembayaran == "Belum Lunas":
            has_unpaid = True
        if progress and progress != "100%":
            has_in_progress = True
        if progress == "100%" and pengambilan in ("Belum Diambil", "Diambil Sebagian"):
            if d and (today - d).days <= 90:
                has_ready_pickup = True
        if progress == "100%" and pengambilan == "Diambil Semua" and pembayaran == "Lunas":
            total_completed += 1

    # Priority classification
    if has_unpaid:
        return "belum_lunas"
    if has_in_progress:
        return "proses"
    if has_ready_pickup:
        return "siap_ambil"

    days_since = (today - last_order_date).days if last_order_date else 9999
    total_orders = len(orders)

    if total_orders >= 2 and days_since > 60:
        return "churn_risk"
    if total_orders >= 2 and days_since <= 60:
        return "returning"
    return "selesai"


@router.get("/pipeline")
async def get_pipeline(mode: str = "wa"):
    """
    CRM Pipeline: classify customers into funnel buckets.
    mode=wa  — only WA contacts, cross-referenced with transactions
    mode=all — all transaction customers + WA data
    """
    today = datetime.now().date()

    bucket_config = {
        "lead":        {"label": "Lead",        "color": "#C17E1A"},
        "belum_lunas": {"label": "Belum Lunas", "color": "#E53935"},
        "proses":      {"label": "Proses",      "color": "#FF9800"},
        "siap_ambil":  {"label": "Siap Ambil",  "color": "#2196F3"},
        "returning":   {"label": "Returning",   "color": "#4CAF50"},
        "churn_risk":  {"label": "Churn Risk",  "color": "#E53935"},
        "selesai":     {"label": "Selesai",     "color": "#777777"},
    }

    wa_conn = sqlite3.connect(WA_DB)
    wa_conn.row_factory = sqlite3.Row
    init_wa_tables(wa_conn)

    tx_conn = sqlite3.connect(TX_DB)
    tx_conn.row_factory = sqlite3.Row

    try:
        wa_cur = wa_conn.cursor()
        tx_cur = tx_conn.cursor()

        # Build WA contact map
        wa_cur.execute("SELECT * FROM wa_customers")
        wa_map = {}
        for row in wa_cur.fetchall():
            p = normalize_phone(row["no_hp"])
            if p:
                wa_map[p] = dict(row)

        # Build phone list depending on mode
        if mode == "all":
            tx_cur.execute("""
                SELECT DISTINCT customer_phone FROM transactions
                WHERE customer_phone IS NOT NULL AND customer_phone != ''
            """)
            phone_set = set()
            for row in tx_cur.fetchall():
                p = normalize_phone(row["customer_phone"])
                if p:
                    phone_set.add(p)
            phone_set.update(wa_map.keys())
        else:
            phone_set = set(wa_map.keys())

        # Fetch all transactions grouped by normalized phone
        tx_cur.execute("""
            SELECT customer_phone, customer_name, customer_address,
                   date_of_transaction, progress_status, pembayaran, pengambilan,
                   total_tagihan, no_nota, nama_layanan
            FROM transactions
            WHERE customer_phone IS NOT NULL AND customer_phone != ''
            ORDER BY date_of_transaction DESC
        """)
        orders_by_phone = {}
        name_by_phone = {}
        addr_by_phone = {}
        for row in tx_cur.fetchall():
            p = normalize_phone(row["customer_phone"])
            if not p:
                continue
            orders_by_phone.setdefault(p, []).append(dict(row))
            if p not in name_by_phone and row["customer_name"]:
                name_by_phone[p] = row["customer_name"]
            if p not in addr_by_phone and row["customer_address"]:
                addr_by_phone[p] = row["customer_address"]

        # Get last WA message per phone
        wa_cur.execute("""
            SELECT sender, recipient, message, direction, created_at
            FROM wa_conversations ORDER BY created_at DESC
        """)
        last_msg_by_phone = {}
        for row in wa_cur.fetchall():
            pk = normalize_phone(row["sender"]) if row["direction"] == "inbound" else normalize_phone(row["recipient"])
            if pk and pk not in last_msg_by_phone:
                last_msg_by_phone[pk] = dict(row)

        # Classify each customer
        buckets = {k: [] for k in bucket_config}
        summary = {k: 0 for k in bucket_config}

        for phone in phone_set:
            orders = orders_by_phone.get(phone, [])
            bucket = _classify_customer(orders, today)

            wa_info = wa_map.get(phone)
            name = (wa_info or {}).get("nama") or name_by_phone.get(phone) or phone
            total_orders = len(orders)
            total_spent = sum(o.get("total_tagihan") or 0 for o in orders)
            last_order_date = orders[0]["date_of_transaction"] if orders else None
            unpaid = sum(1 for o in orders if (o.get("pembayaran") or "") == "Belum Lunas")
            last_msg = last_msg_by_phone.get(phone)

            days_since = None
            if last_order_date:
                try:
                    days_since = (today - datetime.strptime(str(last_order_date)[:10], "%Y-%m-%d").date()).days
                except (ValueError, TypeError):
                    pass

            card = {
                "phone": phone,
                "name": name,
                "address": addr_by_phone.get(phone, ""),
                "total_orders": total_orders,
                "total_spent": total_spent,
                "unpaid_orders": unpaid,
                "days_since_order": days_since,
                "last_order_date": last_order_date,
                "has_wa": phone in wa_map,
                "last_message": last_msg.get("message", "")[:80] if last_msg else None,
                "last_message_time": last_msg.get("created_at") if last_msg else None,
                "wa_total_messages": (wa_info or {}).get("total_messages", 0),
            }

            buckets[bucket].append(card)
            summary[bucket] += 1

        return {
            "mode": mode,
            "total_customers": len(phone_set),
            "summary": summary,
            "bucket_config": bucket_config,
            "buckets": buckets,
        }

    finally:
        wa_conn.close()
        tx_conn.close()


@router.get("/pipeline/customer/{phone}")
async def get_pipeline_customer(phone: str):
    """Detailed customer view: WA info + conversation + transaction history."""
    phone = normalize_phone(phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    today = datetime.now().date()

    wa_conn = sqlite3.connect(WA_DB)
    wa_conn.row_factory = sqlite3.Row
    init_wa_tables(wa_conn)

    tx_conn = sqlite3.connect(TX_DB)
    tx_conn.row_factory = sqlite3.Row

    try:
        wa_cur = wa_conn.cursor()
        tx_cur = tx_conn.cursor()

        # WA customer info
        wa_cur.execute("SELECT * FROM wa_customers WHERE no_hp = ?", (phone,))
        wa_row = wa_cur.fetchone()
        wa_info = dict(wa_row) if wa_row else None

        # WA conversations (last 30)
        wa_cur.execute("""
            SELECT * FROM wa_conversations
            WHERE sender = ? OR recipient = ?
            ORDER BY created_at DESC LIMIT 30
        """, (phone, phone))
        conversations = [dict(r) for r in wa_cur.fetchall()]

        # Transaction history
        tx_cur.execute("""
            SELECT customer_phone, customer_name, customer_address,
                   no_nota, date_of_transaction, nama_layanan, group_layanan,
                   progress_status, pembayaran, pengambilan,
                   total_tagihan, jenis, tgl_selesai, tgl_pengambilan
            FROM transactions
            WHERE customer_phone IS NOT NULL AND customer_phone != ''
            ORDER BY date_of_transaction DESC
        """)
        orders = []
        customer_name = None
        customer_address = None
        for row in tx_cur.fetchall():
            if normalize_phone(row["customer_phone"]) == phone:
                orders.append(dict(row))
                if not customer_name and row["customer_name"]:
                    customer_name = row["customer_name"]
                if not customer_address and row["customer_address"]:
                    customer_address = row["customer_address"]

        total_spent = sum(o.get("total_tagihan") or 0 for o in orders)
        unpaid = [o for o in orders if (o.get("pembayaran") or "") == "Belum Lunas"]
        unpaid_amount = sum(o.get("total_tagihan") or 0 for o in unpaid)
        bucket = _classify_customer(orders, today)

        name = (wa_info or {}).get("nama") or customer_name or phone

        return {
            "phone": phone,
            "name": name,
            "address": customer_address or (wa_info or {}).get("alamat", ""),
            "bucket": bucket,
            "wa_info": wa_info,
            "summary": {
                "total_orders": len(orders),
                "total_spent": total_spent,
                "unpaid_count": len(unpaid),
                "unpaid_amount": unpaid_amount,
                "last_order_date": orders[0]["date_of_transaction"] if orders else None,
            },
            "orders": orders[:15],
            "conversations": conversations,
        }

    finally:
        wa_conn.close()
        tx_conn.close()

# ============================================================
# GOWA Webhook — go-whatsapp-web-multidevice
# Menerima events dari GOWA (message in/out, read receipt, etc)
# Payload docs: /opt/gowa/docs/webhook-payload.md
# HMAC secret must match WHATSAPP_WEBHOOK_SECRET in GOWA .env
# ============================================================
GOWA_WEBHOOK_SECRET = "secret"  # GOWA default
TX_DB_PATH = "/opt/siji-dashboard/siji_database.db"


def _detect_message_type(payload: dict) -> tuple[str, str]:
    """Detect message type and media path from GOWA webhook payload.
    Returns (message_type, media_path_or_url)."""
    for mtype in ("image", "video", "audio", "document", "sticker", "video_note"):
        val = payload.get(mtype)
        if val is not None:
            if isinstance(val, str):
                return mtype, val
            elif isinstance(val, dict):
                return mtype, val.get("path") or val.get("url", "")
    if payload.get("contact") or payload.get("contacts_array"):
        return "contact", ""
    if payload.get("location") or payload.get("live_location"):
        return "location", ""
    return "text", ""


@router.post("/gowa-webhook")
async def gowa_webhook(request: Request):
    import sqlite3 as _sqlite3
    import hmac as _hmac
    import hashlib as _hashlib

    # Verify HMAC signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    body = await request.body()

    if GOWA_WEBHOOK_SECRET and signature:
        expected = "sha256=" + _hmac.new(
            GOWA_WEBHOOK_SECRET.encode(), body, _hashlib.sha256
        ).hexdigest()
        if not _hmac.compare_digest(signature, expected):
            return {"status": "unauthorized"}

    try:
        data = json.loads(body)
    except Exception:
        return {"status": "ignored", "reason": "invalid json"}

    event = data.get("event", "")
    payload = data.get("payload", {})
    device_id = data.get("device_id", "")

    # --- Log to Fonnte-era siji.db (existing pipeline, old schema) ---
    SIJI_DB_PATH = "/root/sijibintaro.id/api/siji.db"
    wa_conn = _sqlite3.connect(SIJI_DB_PATH)
    wa_conn.row_factory = _sqlite3.Row
    init_wa_tables(wa_conn)

    # --- Also log to siji_database.db (new GOWA pipeline, new schema) ---
    tx_conn = _sqlite3.connect(TX_DB_PATH)

    try:
        if event == "message":
            from_jid = payload.get("from", "")
            chat_jid = payload.get("chat_id", from_jid)
            sender = from_jid.replace("@s.whatsapp.net", "").replace("@c.us", "")
            is_from_me = payload.get("is_from_me", False)
            from_name = payload.get("from_name", "")
            timestamp = payload.get("timestamp", "")
            msg_id_wa = payload.get("id", "")
            body_text = payload.get("body", "")
            is_forwarded = payload.get("forwarded", False)
            replied_to = payload.get("replied_to_id", "")

            msg_type, media_path = _detect_message_type(payload)
            if msg_type != "text" and not body_text:
                body_text = f"[{msg_type}]"

            direction = "outbound" if is_from_me else "inbound"
            wa_number = device_id.replace("@s.whatsapp.net", "")
            is_group = "@g.us" in chat_jid

            # 1) Existing siji.db pipeline (Fonnte-compatible)
            if not is_from_me and sender:
                upsert_customer(wa_conn, sender, name=from_name)

            fonnte_msg_id = log_message(
                conn=wa_conn,
                wa_number=wa_number,
                sender=sender if not is_from_me else wa_number,
                recipient=wa_number if not is_from_me else sender,
                direction=direction,
                message=body_text,
                media_url=media_path or None,
                category="gowa",
                replied_by="gowa",
                wa_timestamp=timestamp,
                inbox_id=msg_id_wa,
            )

            # 2) New siji_database.db pipeline (wa_conversations + wa_messages)
            phone = chat_jid.split("@")[0] if "@" in chat_jid else chat_jid
            now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            tx_conn.execute("""
                INSERT INTO wa_conversations (jid, phone, contact_name, is_group, last_message, last_message_time, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET
                    contact_name = COALESCE(NULLIF(excluded.contact_name, ''), wa_conversations.contact_name),
                    last_message = excluded.last_message,
                    last_message_time = excluded.last_message_time,
                    total_messages = wa_conversations.total_messages + 1,
                    synced_at = excluded.synced_at
            """, (chat_jid, phone, from_name if not is_from_me else None,
                  is_group, body_text, timestamp, now))

            tx_conn.execute("""
                INSERT OR IGNORE INTO wa_messages
                (conversation_jid, message_id, sender_jid, sender_name, message_text, message_type,
                 media_url, is_from_me, is_forwarded, quoted_message_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (chat_jid, msg_id_wa, from_jid, from_name, body_text, msg_type,
                  media_path or None, is_from_me, is_forwarded, replied_to or None, timestamp))

            tx_conn.commit()
            wa_conn.commit()

            print(f"[GOWA] {direction} {sender} → {body_text[:60]}")

            # Tandai: karyawan sedang handle conversation ini
            if is_from_me and body_text.strip():
                _mark_staff_replied(chat_jid)

            # === GOWA AUTOREPLY PIPELINE ===
            # Stale check: skip pesan lebih dari 3 menit lalu (mencegah replay saat restart)
            _msg_stale = False
            if timestamp:
                try:
                    import dateutil.parser as _dp
                    _msg_time = _dp.parse(timestamp).timestamp()
                    _age_sec = time.time() - _msg_time
                    if _age_sec > 180:  # 3 menit
                        _msg_stale = True
                        print(f"[AUTOREPLY] STALE skip ({_age_sec:.0f}s old): {msg_id_wa[:12]} from {sender}")
                except Exception:
                    pass  # kalau parse gagal, lanjut normal

            if (not is_from_me
                    and not is_group
                    and GOWA_AUTOREPLY_ENABLED
                    and body_text.strip()
                    and msg_type in ("text", "image", "video", "document", "sticker")
                    and not _msg_stale
                    and not _is_duplicate(msg_id_wa)
                    and not _staff_is_handling(chat_jid)):

                # Test mode: hanya proses nomor test, skip semua lainnya
                if GOWA_TEST_MODE and sender not in GOWA_TEST_NUMBERS:
                    print(f"[AUTOREPLY] TEST MODE — skip: {sender}")
                # Skip admin & staff numbers
                elif sender in SKIP_AUTOREPLY_NUMBERS:
                    print(f"[AUTOREPLY] Skip staff/admin: {sender}")
                else:
                    reply_text = None
                    reply_layer = None

                    # Customer context dari DB transaksi
                    cust_ctx = get_customer_context(sender)
                    # Nama: prioritas DB transaksi > from_name WA > "Kak"
                    cust_name = format_customer_greeting(cust_ctx, from_name or "")
                    if cust_ctx["found"]:
                        print(f"[CustomerCtx] {sender} → {cust_ctx['nama']} | {cust_ctx['segment']} | {cust_ctx['total_transaksi']} tx")
                    else:
                        cust_name = from_name or ""

                    # Layer 1: Job application keywords
                    if is_job_application(body_text):
                        reply_text = AUTO_REPLY_JOB
                        reply_layer = "job"

                    # Layer 0: Image/media — customer kirim foto + tanya bisa cuci?
                    if not reply_text and msg_type in ("image", "video", "sticker"):
                        reply_text = ASK_ITEM_REPLY
                        reply_layer = "ask_item:media"

                    # Layer 0.5: Pesan teks tapi item tidak disebutkan ("bisa cuci ini?")
                    if not reply_text and is_vague_item_query(body_text):
                        reply_text = ASK_ITEM_REPLY
                        reply_layer = "ask_item:vague"

                    # Layer 2: Complaint check DULU (sebelum catalog/keyword)
                    # "sepatu rusak setelah dicuci" → escalate, bukan catalog sepatu
                    if not reply_text and is_complaint(body_text):
                        _notif_name = from_name or sender
                        _notif_body = body_text[:300]
                        notif_msg = (
                            "\u26a0\ufe0f *KOMPLAIN MASUK*\n\n"
                            "Dari: *" + _notif_name + "* (wa.me/" + sender + ")\n\n"
                            "Pesan: _" + _notif_body + "_\n\n"
                            "\u2139\ufe0f Mohon ditangani: cek order & follow up ke customer."
                        )
                        for _esc_num in ESCALATION_NUMBERS:
                            await send_gowa_message(_esc_num, notif_msg)
                        # Reply ke customer: acknowledgment — jangan jatuh ke catalog
                        reply_text = (
                            "Halo Kak! \U0001f64f Pesan kamu sudah kami terima dan segera ditangani tim kami.\n\n"
                            "Mohon tunggu sebentar ya, kami akan segera menghubungi kamu kembali."
                        )
                        reply_layer = "escalated:complaint"
                        print(f"[AUTOREPLY] COMPLAINT escalated to {ESCALATION_NUMBERS}: {sender}")

                    # Layer 2.6: Order status — cek SEBELUM catalog
                    # "cuci setrika kemarin sudah selesai?" → status reply, bukan catalog
                    # LLM tidak bisa akses DB order → akan halusinasi "sudah selesai" dll
                    if not reply_text and not reply_layer and is_order_status_query(body_text):
                        reply_text = ORDER_STATUS_REPLY
                        reply_layer = "order_status"

                    # Layer 2.5: Service catalog — bisa cuci X? harga X?
                    if not reply_text:
                        svc_reply = check_service_catalog(body_text)
                        if svc_reply:
                            reply_text = svc_reply
                            reply_layer = "catalog"

                    # Layer 3: Keyword match (harga, jam, lokasi, promo)
                    if not reply_text:
                        cat = match_keyword(body_text)
                        if cat and cat in KEYWORD_REPLIES:
                            reply_text = KEYWORD_REPLIES[cat]
                            reply_layer = f"keyword:{cat}"

                    # Layer 4: RAG + LLM (qwen2.5:1.5b + karyawan Q&A history)
                    _rag_score = 0.0
                    if not reply_text and not reply_layer and RAG_ENABLED:
                        try:
                            loop = asyncio.get_event_loop()
                            context = await loop.run_in_executor(None, find_context, body_text)
                            context["customer_name"] = cust_name
                            context["customer_segment"] = cust_ctx.get("segment", "Baru")
                            context["customer_tx_count"] = cust_ctx.get("total_transaksi", 0)
                            _rag_score = context["best_score"]
                            if _rag_score >= 0.75:
                                llm_reply = await generate_reply_async(body_text, context)
                                if llm_reply:
                                    reply_text = llm_reply
                                    reply_layer = f"rag_llm:{_rag_score:.2f}"
                                    print(f"[AUTOREPLY] RAG+LLM score={_rag_score:.2f} → {sender}")
                        except Exception as _rag_err:
                            print(f"[AUTOREPLY] RAG error: {_rag_err}")

                    # Layer 5: Default fallback
                    # - Kalau RAG score tinggi tapi LLM gagal: kirim default TANPA cooldown (retry ok)
                    # - Kalau RAG score rendah (pertanyaan tidak relevan): cooldown 10 menit
                    if not reply_text and not reply_layer:
                        if _rag_score >= 0.75:
                            # LLM timeout/gagal — kirim default, tidak set cooldown supaya bisa retry
                            reply_text = AUTO_REPLY_DEFAULT
                            reply_layer = "default:llm_fail"
                            print(f"[AUTOREPLY] LLM fail fallback (score={_rag_score:.2f}), no cooldown: {sender}")
                        elif _can_send_default(sender):
                            # Pertanyaan tidak relevan — kirim default dengan cooldown
                            reply_text = AUTO_REPLY_DEFAULT
                            reply_layer = "default:low_score"
                        else:
                            print(f"[AUTOREPLY] Default cooldown active (low score): {sender}")

                    # Inject greeting personal untuk customer dikenal
                    # Layer LLM (rag_llm) sudah handle greeting sendiri via system prompt
                    # Layer lain (keyword, catalog, default) → prepend greeting
                    if reply_text and reply_layer and not reply_layer.startswith("rag_llm"):
                        greeting = build_greeting(cust_name, cust_ctx.get("segment", "Baru"))
                        if greeting and cust_ctx.get("found"):
                            reply_text = f"{greeting}\n{reply_text}"

                    # SEND — kirim reply kalau ada (layer 1/2/4/5)
                    if reply_text:
                        await send_gowa_message(sender, reply_text)
                        print(f"[AUTOREPLY] {reply_layer} → {sender}: {reply_text[:60]}")

            return {"status": "ok", "message_id": fonnte_msg_id, "direction": direction}

        elif event == "message.ack":
            # Read/delivery receipt
            receipt_type = payload.get("receipt_type", "")
            msg_ids = payload.get("ids", [])
            if receipt_type == "read" and msg_ids:
                placeholders = ",".join("?" for _ in msg_ids)
                tx_conn.execute(
                    f"UPDATE wa_messages SET status = 'read' WHERE message_id IN ({placeholders})",
                    msg_ids)
                tx_conn.commit()
            elif receipt_type == "delivered" and msg_ids:
                placeholders = ",".join("?" for _ in msg_ids)
                tx_conn.execute(
                    f"UPDATE wa_messages SET status = 'delivered' WHERE message_id IN ({placeholders}) AND status IS NULL",
                    msg_ids)
                tx_conn.commit()
            return {"status": "ok", "event": "ack", "receipt_type": receipt_type}

        elif event == "message.revoked":
            revoked_id = payload.get("revoked_message_id", "")
            if revoked_id:
                tx_conn.execute(
                    "UPDATE wa_messages SET message_text = '[message deleted]', message_type = 'revoked' WHERE message_id = ?",
                    (revoked_id,))
                tx_conn.commit()
            return {"status": "ok", "event": "revoked"}

        elif event == "message.edited":
            orig_id = payload.get("original_message_id", "")
            new_body = payload.get("body", "")
            if orig_id and new_body:
                tx_conn.execute(
                    "UPDATE wa_messages SET message_text = ? WHERE message_id = ?",
                    (new_body, orig_id))
                tx_conn.commit()
            return {"status": "ok", "event": "edited"}

        else:
            return {"status": "ignored", "reason": f"event {event} not handled"}

    except Exception as e:
        print(f"[GOWA Webhook Error] {e}")
        try:
            wa_conn.rollback()
            tx_conn.rollback()
        except Exception:
            pass
        return {"status": "error", "detail": str(e)}
    finally:
        wa_conn.close()
        tx_conn.close()
