#!/usr/bin/env python3
"""
Kirim rekap semua pelamar existing ke Ocha + Erik via WhatsApp.
Dibagi per posisi, split per chunk agar tidak terlalu panjang.
"""

import sqlite3, subprocess
from collections import defaultdict

DB_PATH = "/root/sijibintaro.id/api/siji.db"

TARGETS = [
    "+628118606999",  # Ocha SIJI
    "+62811319003",   # Erik
]

def send_wa(target, msg):
    result = subprocess.run(
        ["openclaw", "message", "send",
         "--channel", "whatsapp",
         "-t", target,
         "-m", msg],
        capture_output=True, text=True, timeout=30
    )
    ok = result.returncode == 0
    print(f"{'✅' if ok else '❌'} → {target}: {result.stdout.strip()[:80] or result.stderr.strip()[:80]}")
    return ok

def clean_exp(exp):
    if not exp:
        return "-"
    # Ambil baris pertama yang bermakna
    lines = [l.strip() for l in exp.replace('|', '\n').split('\n') if l.strip()]
    # Skip "Pendidikan: xxx", "Gender: xxx", "Email: xxx"
    meaningful = [l for l in lines if not any(l.startswith(k) for k in ['Pendidikan:', 'Gender:', 'Email:', 'Pengalaman:'])]
    if not meaningful:
        meaningful = lines[:1]
    result = meaningful[0] if meaningful else "-"
    return result[:80]

def clean_domisili(dom):
    if not dom:
        return "-"
    # Ambil bagian pertama saja
    parts = dom.split(',')
    return parts[0].strip()[:40]

def build_messages():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT nama, whatsapp, posisi, domisili, pengalaman, status, created_at
        FROM lamaran
        WHERE nama != 'Test Applicant'
        ORDER BY posisi ASC, created_at DESC
    """)
    rows = cur.fetchall()

    # Group by posisi
    by_posisi = defaultdict(list)
    for r in rows:
        by_posisi[r['posisi']].append(r)

    total = len(rows)
    posisi_list = sorted(by_posisi.keys())

    # Pesan pertama: header + statistik
    header_lines = [
        "📋 *REKAP LAMARAN SIJI BINTARO*",
        f"Total: *{total} pelamar* dari {len(posisi_list)} posisi\n",
    ]
    for posisi in posisi_list:
        header_lines.append(f"• {posisi}: {len(by_posisi[posisi])} orang")
    header_lines.append("\n_(Detail per posisi menyusul)_")

    messages = ["\n".join(header_lines)]

    # Pesan per posisi (1 pesan per posisi atau gabung kalau sedikit)
    CHUNK_MAX = 3500  # karakter per pesan WA

    current_chunk = ""
    for posisi in posisi_list:
        pelamars = by_posisi[posisi]
        posisi_block = f"\n💼 *{posisi}* ({len(pelamars)} orang)\n"
        for p in pelamars:
            dom = clean_domisili(p['domisili'])
            exp = clean_exp(p['pengalaman'])
            wa_clean = p['whatsapp'].replace('+62', '0').replace('+', '0')[:13]
            tgl = p['created_at'][:10]
            line = (
                f"👤 *{p['nama']}*\n"
                f"   📱 {wa_clean} | 📍 {dom}\n"
                f"   📝 {exp}\n"
                f"   🗓 {tgl}\n"
            )
            posisi_block += line

        # Cek apakah harus split
        if len(current_chunk) + len(posisi_block) > CHUNK_MAX:
            if current_chunk:
                messages.append(current_chunk.strip())
            current_chunk = posisi_block
        else:
            current_chunk += posisi_block

    if current_chunk.strip():
        messages.append(current_chunk.strip())

    return messages

def main():
    messages = build_messages()
    print(f"Total {len(messages)} pesan akan dikirim ke {len(TARGETS)} target\n")
    for i, msg in enumerate(messages, 1):
        print(f"--- Pesan {i} ({len(msg)} chars) ---")
        print(msg[:300])
        print()

    confirm = input("Kirim sekarang? (y/N): ").strip().lower()
    if confirm != 'y':
        print("Dibatalkan.")
        return

    for target in TARGETS:
        print(f"\n📤 Kirim ke {target}...")
        for i, msg in enumerate(messages, 1):
            print(f"  Pesan {i}/{len(messages)}...")
            send_wa(target, msg)
            import time; time.sleep(1.5)

    print("\n✅ Selesai!")

if __name__ == "__main__":
    main()
