"""
siji_llm.py — LLM reply generator untuk SIJI Bintaro
Model: qwen2.5:1.5b via Ollama (local, zero cost)
Tone: karyawan SIJI (Unaesih, Rizky, Denisa) — dari data percakapan real
"""
import httpx
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"
LLM_MODEL   = "qwen2.5:1.5b"

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

ATURAN BALAS:
- Bahasa Indonesia, singkat, ramah seperti chat WA
- SELALU mulai dengan sapa nama pelanggan (dari prefix "Pelanggan (nama):")
- Sapaan wajib: pakai Pak/Bu jika nama ada prefix Pak/Bu/Ibu/Bapak. Pakai Kak jika nama polos. JANGAN sapa langsung nama tanpa Pak/Bu/Kak
- Contoh benar: "Selamat siang Bu Hariza!", "Halo Kak Lia!" — Contoh salah: "Selamat siang Hariza!" atau "Halo Erik!"
- Kalau VIP: sapa hangat, akui kesetiaan mereka
- Maksimal 2-3 kalimat
- Kalau ada di daftar layanan → jawab langsung dengan harga
- Kalau tidak ada di daftar → "Mohon ditunggu ya Kak, kami segera cek 🙏"
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


def generate_reply(customer_message: str, context: dict) -> Optional[str]:
    """Generate reply via qwen2.5:1.5b. Returns string or None."""
    try:
        messages = build_prompt_messages(customer_message, context)
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 80, "stop": ["\n\n"]}
            },
            timeout=90
        )
        reply = resp.json().get("message", {}).get("content", "").strip()
        if reply:
            print(f"[LLM] Generated: {reply[:80]}")
            return reply
        return None
    except Exception as e:
        print(f"[LLM] generate error: {e}")
        return None


def warmup_model():
    """Pre-load model into memory (call once at startup)"""
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
