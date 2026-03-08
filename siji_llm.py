"""
siji_llm.py — LLM reply generator untuk SIJI Bintaro
Model: qwen2.5:1.5b via Ollama (local, zero cost)
Tone: Ocha/Filean karyawan kasir style
"""
import httpx
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"
LLM_MODEL   = "qwen2.5:1.5b"

SIJI_SYSTEM_PROMPT = """Kamu adalah kasir SIJI Bintaro, laundry premium di Bintaro Jaya Sektor 9.

IDENTITAS:
- Nama toko: SIJI Bintaro (dulu Soki Laundry)
- Lokasi: Jl. Raya Emerald Boulevard, BLOK CE/A1 No.5 (Ruko PHD, Sebelah Marchand), Bintaro Jaya
- Jam: Senin-Sabtu 08.00-20.00, Minggu 08.00-16.00
- Layanan: cuci kiloan, bedcover, sepatu, tas, dry clean, setrika, jemput-antar
- Harga kiloan: Rp 16.000/kg (cuci kering setrika), Rp 12.000/kg (cuci kering lipat/setrika saja), min 3kg
- Bedcover: Rp 70.000/lembar | Sepatu: Rp 90.000/pasang | Sprei 1 set: Rp 35.000

GAYA BICARA (tiru gaya kasir Ocha/Filean):
- Ramah, ringkas, pakai "Kak" atau nama kalau tahu (contoh: "Baik Bu Ratih")
- Semi-formal, sesekali pakai emoji tapi tidak berlebihan
- Maksimal 3-4 kalimat per reply
- Kalau tidak tahu → "Tim kami segera membalas ya Kak 🙏"
- Jangan buat info yang tidak kamu ketahui pasti

JANGAN lakukan:
- Jangan balas dalam bahasa Inggris atau Mandarin
- Jangan tulis lebih dari 4 kalimat
- Jangan hardcode harga yang tidak ada di atas"""


def build_prompt_messages(customer_message: str, context: dict) -> list:
    """Build chat messages with RAG context injected"""
    system = SIJI_SYSTEM_PROMPT

    # Inject QA example jika ada (contoh balasan karyawan sebelumnya)
    if context.get("qa_context") and context.get("qa_answer"):
        system += f"\n\nCONTOH BALASAN KARYAWAN:\nPelanggan: {context['qa_context']}\nKasir: {context['qa_answer']}"

    # Inject SOP knowledge jika ada
    if context.get("sop_context"):
        system += f"\n\nKNOWLEDGE BASE:\n{context['sop_context']}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": customer_message}
    ]


def generate_reply(customer_message: str, context: dict) -> Optional[str]:
    """
    Generate natural reply using qwen2.5:1.5b with RAG context.
    Returns reply string or None if failed.
    """
    try:
        messages = build_prompt_messages(customer_message, context)
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 100,
                    "stop": ["\n\n\n"]
                }
            },
            timeout=60
        )
        result = resp.json()
        reply = result.get("message", {}).get("content", "").strip()
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
        resp = httpx.post(f"{OLLAMA_BASE}/api/generate",
                          json={"model": LLM_MODEL, "prompt": "halo", "stream": False,
                                "options": {"num_predict": 1}},
                          timeout=60)
        print(f"[LLM] Warmup done: {resp.status_code}")
    except Exception as e:
        print(f"[LLM] Warmup failed (ok): {e}")


async def generate_reply_async(customer_message: str, context: dict) -> Optional[str]:
    """Async wrapper for generate_reply (runs in thread executor)"""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, generate_reply, customer_message, context)
