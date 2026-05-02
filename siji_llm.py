"""
siji_llm.py — LLM reply generator untuk SIJI Bintaro
Primary:  gpt-4o-mini via OpenAI (reliable, affordable)
Fallback: qwen2.5:1.5b via Ollama (local, zero cost)
Tone: karyawan SIJI (Unaesih, Rizky, Denisa) — dari data percakapan real
"""
import httpx
import os
from typing import Optional

OLLAMA_BASE    = "http://localhost:11434"
LLM_MODEL      = "gemma3:1b"
OPENAI_MODEL   = "gpt-4o-mini"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SIJI_SYSTEM_PROMPT = """Kamu staf laundry SIJI Bintaro yang sedang balas WA pelanggan.

INFO TOKO:
- SIJI Bintaro, Jl. Raya Emerald Boulevard BLOK CE/A1 No.5, Bintaro Jaya
- Jam: Senin-Sabtu 08.00-20.00, Minggu 08.00-16.00
- Antar jemput: GRATIS radius 3 km dari outlet. Di luar 3 km bisa diatur — pelanggan VIP/Reguler sering dibebaskan biaya sebagai apresiasi kesetiaan. Arahkan ke staff untuk konfirmasi.

DAFTAR LAYANAN & HARGA:
Kiloan:
- Cuci kering setrika: Rp16.000/kg (min 3 kg)
- Cuci kering lipat: Rp12.000/kg (min 3 kg)
- Setrika kiloan: Rp12.000/kg (min 3 kg)

Satuan:
- Bedcover: Rp70.000/lembar (3 hari), Express Rp115.000
- Sprei 1 set: Rp35.000/set (3 hari), Express Rp55.000
- Bantal kecil/boneka kecil: Rp40.000 | Bantal besar/guling: Rp60.000
- Sepatu reguler: Rp90.000/pasang (3 hari)
- Sepatu kulit/boot: Rp150.000 (4 hari) | Express Rp250.000
- Tas reguler: Rp140.000/unit | Tas USA brand: Rp250.000 | Tas EU (LV/Gucci): Rp500.000
- Dompet: Rp100.000 (reguler), Rp200.000 (USA brand), Rp350.000 (EU brand)
- Blazer/jaket: Rp65.000/pcs (3 hari)
- Dry clean blazer/jas: Rp80.000/pcs (4 hari)
- Pakaian/jaket kulit: Rp150.000/pcs (12 hari)
- Dress/kebaya/brokat: Rp100.000/pcs (4 hari)
- Karpet: Rp35.000/m² (10 hari)
- Gordyn tebal/blackout: Rp16.000/m² | Gordyn tipis: Rp10.000/m²
- Baby stroller: Rp250.000/unit (6 hari) | Baby car seat: Rp250.000
- Kasur/matras tipis: Rp95.000/unit | Kasur lipat: Rp400.000
- Boneka besar: Rp100.000 | Helm: Rp80.000 | Koper: Rp190.000
- Laundry satuan reguler: Rp40.000/pcs | Express 24 jam: Rp50.000 | 10 jam: Rp60.000
- Topi: Rp65.000/pcs | Sleeping bag: Rp90.000

MOMEN LEBARAN (20-24 Maret 2026):
- Sertakan ucapan "Selamat Idul Fitri, mohon maaf lahir batin 🙏" HANYA JIKA konteks chat menunjukkan ini adalah pesan PERTAMA dari pelanggan ini (tidak ada riwayat pesan sebelumnya di konteks)
- Kalau sudah ada riwayat chat sebelumnya → JANGAN ulangi ucapan Lebaran, langsung jawab pertanyaan
- Tujuan: natural, tidak terkesan bot spam

ATURAN BALAS:
- Bahasa Indonesia informal, singkat, gaya chat WA — JANGAN kaku atau formal
- DILARANG pakai kata "Anda" — selalu pakai Pak/Bu/Kak
- SELALU mulai dengan sapa nama pelanggan (dari prefix "Pelanggan (nama):")
- Sapaan: pakai Pak/Bu jika nama ada prefix Pak/Bu/Ibu/Bapak. Pakai Kak jika nama polos
- Contoh BENAR: "Selamat siang Bu Hariza! ..." atau "Halo Kak Lia! ..."
- Contoh SALAH: "Selamat siang Hariza!" atau "Halo Erik!" atau "Tentu saja Anda..."
- Kalau VIP: sapa hangat, tunjukkan apresiasi
- Maksimal 2-3 kalimat, to the point
- Kalau ada di daftar layanan → jawab langsung dengan harga
- Kalau tidak ada di daftar → "Mohon ditunggu ya Kak, kami segera cek 🙏"
- DILARANG sebut kendaraan (mobil/motor) — cukup "kurir kami" jika perlu
- DILARANG mengarang info yang tidak ada di context — lebih baik minta tunggu
- Jangan balas dalam bahasa lain"""


def build_prompt_messages(customer_message: str, context: dict) -> list:
    system = SIJI_SYSTEM_PROMPT

    # Inject SOP context kalau relevan
    if context.get("sop_context"):
        system += f"\n\nINFO TAMBAHAN:\n{context['sop_context'][:300]}"

    # Nama pelanggan masuk ke user message agar model pakai nama yang benar
    cust_name = context.get("customer_name", "").strip()
    segment   = context.get("customer_segment", "Baru")
    tx_count  = context.get("customer_tx_count", 0)

    # Label untuk model: VIP/Reguler customer sapaan lebih hangat
    if cust_name and segment == "VIP":
        prefix = f"Pelanggan VIP ({cust_name}, {tx_count} transaksi)"
    elif cust_name:
        prefix = f"Pelanggan ({cust_name})"
    else:
        prefix = "Pelanggan"

    user_msg = f"{prefix}: {customer_message}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg}
    ]


def _generate_ollama(messages: list) -> Optional[str]:
    """Try Ollama (qwen2.5:1.5b). Returns reply string or None."""
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 80, "stop": ["\n\n"]}
            },
            timeout=5  # Timeout ketat — fallback cepat ke OpenAI kalau Ollama lambat
        )
        reply = resp.json().get("message", {}).get("content", "").strip()
        if reply:
            print(f"[LLM:ollama] {reply[:80]}")
            return reply
        return None
    except Exception as e:
        print(f"[LLM:ollama] failed: {e}")
        return None


def _generate_openai(messages: list) -> Optional[str]:
    """Fallback: OpenAI GPT-4o-mini. Returns reply string or None."""
    if not OPENAI_API_KEY:
        print("[LLM:openai] OPENAI_API_KEY not set, skip fallback")
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=100,
        )
        reply = resp.choices[0].message.content.strip()
        if reply:
            print(f"[LLM:openai] {reply[:80]}")
            return reply
        return None
    except Exception as e:
        print(f"[LLM:openai] failed: {e}")
        return None


def generate_reply(customer_message: str, context: dict) -> Optional[str]:
    """Generate reply — GPT-4o-mini primary, Ollama fallback. Returns string or None."""
    messages = build_prompt_messages(customer_message, context)

    # Primary: GPT-4o-mini (reliable, affordable, quality)
    reply = _generate_openai(messages)
    if reply:
        return reply

    # Fallback: Ollama local (zero cost, mungkin lambat)
    print("[LLM] OpenAI failed, falling back to Ollama...")
    return _generate_ollama(messages)


def warmup_model():
    """Pre-load Ollama model into memory (call once at startup)"""
    try:
        httpx.post(f"{OLLAMA_BASE}/api/generate",
                   json={"model": LLM_MODEL, "prompt": "halo", "stream": False,
                         "options": {"num_predict": 1}},
                   timeout=60)
        print("[LLM] Warmup OK")
    except Exception as e:
        print(f"[LLM] Warmup failed: {e}")


async def generate_reply_async(customer_message: str, context: dict) -> Optional[str]:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, generate_reply, customer_message, context)
