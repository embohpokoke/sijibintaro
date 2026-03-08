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
- Ada layanan jemput-antar ke area Bintaro

DAFTAR LAYANAN & HARGA:
Kiloan:
- Cuci kering setrika: Rp16.000/kg (min 3 kg)
- Cuci kering lipat: Rp12.000/kg (min 3 kg)
- Setrika kiloan: Rp12.000/kg (min 3 kg)

Satuan:
- Bedcover: Rp70.000/lembar
- Sprei 1 set (2 sarung bantal + 2 sarung guling): Rp35.000
- Sepatu reguler: Rp90.000/pasang
- Sepatu kulit: Rp150.000/pasang
- Tas regular: Rp140.000/unit
- Tas brand (Fossil/Kate Spade/Coach/Tory Burch dll): Rp250.000/pcs
- Blazer/jaket biasa: Rp65.000/pcs
- Dry clean blazer/jas: Rp80.000/pcs
- Pakaian/jaket kulit: Rp150.000/pcs
- Karpet: Rp75.000/m²
- Baby stroller: Rp250.000/unit
- Laundry satuan reguler: Rp40.000/pcs
- Laundry satuan express 24 jam: Rp50.000/pcs
- Laundry satuan express 10 jam: Rp60.000/pcs

ATURAN BALAS:
- Bahasa Indonesia, singkat, ramah seperti chat WA
- Sapa pakai nama pelanggan kalau ada, atau "Kak"
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
    user_msg = f"Pelanggan ({cust_name or 'Kak'}): {customer_message}" if cust_name else customer_message

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
