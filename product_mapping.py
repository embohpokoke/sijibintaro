"""
SIJI Product Categorization Mapping
Maps all ~160 unique Smartlink product names to standardized categories.
"""

import re

# Category definitions
CATEGORIES = {
    "LAUNDRY_KILOAN": {
        "Cuci Kering Setrika": "CKS",
        "Cuci Kering Lipat": "CKL", 
        "Setrika Saja": "Setrika",
    },
    "LAUNDRY_SATUAN": {
        "Reguler": "Satuan",
        "Express": "Satuan Express",
        "Setrika": "Setrika Satuan",
    },
    "SEPATU": {
        "Reguler": "Sepatu Reguler",
        "Kulit": "Sepatu Kulit",
        "Boot": "Sepatu Boot",
        "Express": "Sepatu Express",
        "Repair": "Sepatu Repair",
        "Recolor": "Sepatu Recolor",
        "Retouch": "Sepatu Retouch",
        "Treatment": "Sepatu Treatment",
        "Unyellowing": "Sepatu Unyellowing",
    },
    "TAS": {
        "USA Brand": "Tas USA Brand",
        "EU Brand": "Tas EU Brand",
        "Reguler": "Tas Reguler",
        "Repair": "Tas Repair",
        "Dompet": "Dompet",
        "Recolor": "Tas Recolor",
        "Ransel": "Tas Ransel",
    },
    "BEDDING": {
        "Bedcover": "Bedcover",
        "Sprei Set": "Sprei Set",
        "Bantal/Guling": "Bantal Guling",
        "Kasur/Matras": "Kasur Matras",
        "Sleeping Bag": "Sleeping Bag",
    },
    "DRY_CLEAN": {
        "Blazer/Jas": "Dry Clean Blazer",
        "Dress/Kebaya": "Dress Kebaya",
        "Jaket": "Jaket",
        "Kulit": "Pakaian Kulit",
    },
    "HOUSEHOLD": {
        "Gordyn": "Gordyn",
        "Karpet": "Karpet",
        "Sofa": "Sofa",
    },
    "LAINNYA": {
        "Helm": "Helm",
        "Koper": "Koper",
        "Boneka": "Boneka",
        "Baby Items": "Baby Items",
        "Topi": "Topi",
        "Accessories": "Aksesoris",
        "Hanger": "Hanger",
        "Internal": "Internal Karyawan",
        "Loyalty": "Loyalty Program",
        "Paket": "Paket Bundling",
    },
}

def classify_product(nama_layanan: str) -> dict:
    """
    Classify a Smartlink product name into category, sub-category, speed tier, and promo info.
    Returns dict with: kategori, sub_kategori, speed_tier, is_promo, promo_name, base_product
    """
    if not nama_layanan:
        return {"kategori": "LAINNYA", "sub_kategori": "Unknown", "speed_tier": "REGULER", 
                "is_promo": False, "promo_name": None, "base_product": nama_layanan}
    
    n = nama_layanan.strip()
    nl = n.lower()
    
    # Detect promo
    is_promo = False
    promo_name = None
    promo_patterns = [
        (r'promo?\s+ramadhan\s*(sale)?\s*(disc?\s*\d+%?)?', 'Ramadhan'),
        (r'promo?\s+merdeka\s*(sale)?\s*(disc?\s*\d+%?)?', 'Merdeka'),
        (r'november\s+promo\s*(\d+%?)?', 'November'),
        (r'valentine\s+promo\s*(disc?\s*\d+%?)?', 'Valentine'),
        (r'promo?\s+may-?day\s*(disc?\s*\d+%?)?', 'May Day'),
        (r'promo?\s+oktober\s*(disc?\s*\d+%?)?', 'Oktober'),
        (r'promo?\s+september\s*(disc?\s*\d+%?)?', 'September'),
        (r'promo?\s+end\s+year\s*(disc?\s*\d+%?)?', 'End Year'),
        (r'promo?\s+cuci\s+', 'Promo Bundling'),
        (r'promo?\s+satuan\s+', 'Promo Bundling'),
        (r'promo?\s+repair\s+', 'Promo'),
        (r'promo?\s+cks\s+', 'Promo CKS'),
        (r'disc(?:ount)?\s+(?:khusus\s+)?\d+%?', 'Disc Khusus'),
        (r'diskon\s+\d+%?', 'Disc Khusus'),
        (r'free\s+', 'Free/Loyalty'),
        (r'^z\.\s+loyalty', 'Loyalty'),
    ]
    for pat, pname in promo_patterns:
        if re.search(pat, nl):
            is_promo = True
            promo_name = pname
            break
    
    # Also detect customer-specific discounts
    if re.search(r'(bu |pak |utk |labin|bash studio)', nl) and not is_promo:
        is_promo = True
        promo_name = "Disc Khusus"

    # Speed tier detection
    speed_tier = "REGULER"
    if re.search(r'express|ekspress|ekspres|24\s*jam', nl):
        speed_tier = "EXPRESS"
    elif re.search(r'same\s*day|8\s*jam|10\s*jam', nl):
        speed_tier = "SAME_DAY"

    # === CLASSIFICATION RULES (order matters) ===
    
    # Internal / Loyalty
    if nl.startswith('x') and 'karyawan' in nl:
        if 'setrika' in nl:
            return _r("LAUNDRY_KILOAN", "Setrika Saja", speed_tier, True, "Internal Karyawan", n)
        elif 'bedcover' in nl:
            return _r("BEDDING", "Bedcover", speed_tier, True, "Internal Karyawan", n)
        elif 'sprei' in nl:
            return _r("BEDDING", "Sprei Set", speed_tier, True, "Internal Karyawan", n)
        return _r("LAINNYA", "Internal", speed_tier, True, "Internal Karyawan", n)
    if 'loyalty' in nl:
        return _r("LAINNYA", "Loyalty", speed_tier, True, "Loyalty", n)
    if 'paket' in nl and ('diskon' in nl or 'gratis' in nl or 'kriks' in nl):
        if 'sepatu' in nl:
            return _r("SEPATU", "Reguler", speed_tier, True, "Paket Bundling", n)
        return _r("LAINNYA", "Paket", speed_tier, True, "Paket Bundling", n)
    
    # SEPATU
    if any(x in nl for x in ['sepatu', 'shoes', 'shoe']):
        if any(x in nl for x in ['kulit', 'leather']):
            return _r("SEPATU", "Kulit", speed_tier, is_promo, promo_name, n)
        if 'boot' in nl:
            return _r("SEPATU", "Boot", speed_tier, is_promo, promo_name, n)
        if 'recolor' in nl or 'repaint' in nl:
            return _r("SEPATU", "Recolor", speed_tier, is_promo, promo_name, n)
        if 'retouch' in nl:
            return _r("SEPATU", "Retouch", speed_tier, is_promo, promo_name, n)
        if 'repair' in nl or 'ganti sol' in nl or 'lem' in nl:
            return _r("SEPATU", "Repair", speed_tier, is_promo, promo_name, n)
        if 'treatment' in nl or 'jamur' in nl or 'noda' in nl:
            return _r("SEPATU", "Treatment", speed_tier, is_promo, promo_name, n)
        if 'unyellowing' in nl:
            return _r("SEPATU", "Unyellowing", speed_tier, is_promo, promo_name, n)
        if 'trail' in nl:
            return _r("SEPATU", "Reguler", speed_tier, is_promo, promo_name, n)
        if speed_tier == "EXPRESS":
            return _r("SEPATU", "Express", speed_tier, is_promo, promo_name, n)
        return _r("SEPATU", "Reguler", speed_tier, is_promo, promo_name, n)
    
    # LEM SEPATU (no "sepatu" keyword in some entries)
    if nl == 'lem sepatu':
        return _r("SEPATU", "Repair", "REGULER", is_promo, promo_name, n)
    
    # TAS / BAG
    if any(x in nl for x in ['tas ', 'bag ', 'bag_', 'dompet']):
        if 'dompet' in nl:
            return _r("TAS", "Dompet", speed_tier, is_promo, promo_name, n)
        if any(x in nl for x in ['usa', 'us brand', 'fossil', 'coach', 'kate spade', 'tory']):
            return _r("TAS", "USA Brand", speed_tier, is_promo, promo_name, n)
        if any(x in nl for x in ['eropa', 'eu brand', 'eu ', 'lv', 'gucci', 'prada', 'fendi', 'dior', 'givenchy', 'givency']):
            return _r("TAS", "EU Brand", speed_tier, is_promo, promo_name, n)
        if any(x in nl for x in ['repair', 'retouch']):
            return _r("TAS", "Repair", speed_tier, is_promo, promo_name, n)
        if any(x in nl for x in ['recolor', 'repaint']):
            return _r("TAS", "Recolor", speed_tier, is_promo, promo_name, n)
        if any(x in nl for x in ['gunung', 'ransel']):
            return _r("TAS", "Ransel", speed_tier, is_promo, promo_name, n)
        return _r("TAS", "Reguler", speed_tier, is_promo, promo_name, n)
    
    # BEDDING
    if 'bedcover' in nl or 'bed cover' in nl:
        return _r("BEDDING", "Bedcover", speed_tier, is_promo, promo_name, n)
    if 'sprei' in nl or 'selimut' in nl:
        return _r("BEDDING", "Sprei Set", speed_tier, is_promo, promo_name, n)
    if any(x in nl for x in ['bantal', 'guling', 'boneka kecil']):
        return _r("BEDDING", "Bantal/Guling", speed_tier, is_promo, promo_name, n)
    if any(x in nl for x in ['kasur', 'matras', 'baby bed']):
        return _r("BEDDING", "Kasur/Matras", speed_tier, is_promo, promo_name, n)
    if 'sleeping bag' in nl:
        return _r("BEDDING", "Sleeping Bag", speed_tier, is_promo, promo_name, n)
    
    # DRY CLEAN
    if any(x in nl for x in ['dry clean', 'dry_clean']):
        if any(x in nl for x in ['blazer', 'jas']):
            return _r("DRY_CLEAN", "Blazer/Jas", speed_tier, is_promo, promo_name, n)
        return _r("DRY_CLEAN", "Blazer/Jas", speed_tier, is_promo, promo_name, n)
    if any(x in nl for x in ['dress', 'kebaya', 'brokat', 'tile']):
        return _r("DRY_CLEAN", "Dress/Kebaya", speed_tier, is_promo, promo_name, n)
    if nl in ['blazer /jaket', 'blazer/jaket'] or (('blazer' in nl or 'jaket' in nl) and 'kulit' not in nl):
        if is_promo:
            return _r("DRY_CLEAN", "Jaket", speed_tier, is_promo, promo_name, n)
        return _r("DRY_CLEAN", "Jaket", speed_tier, is_promo, promo_name, n)
    if 'pakaian' in nl and 'kulit' in nl:
        return _r("DRY_CLEAN", "Kulit", speed_tier, is_promo, promo_name, n)
    
    # LAUNDRY KILOAN (CKS, CKL, Setrika)
    if any(x in nl for x in ['cuci kering setrika', 'cks', 'kerlng setrika', 'cuci kilo setrika']):
        return _r("LAUNDRY_KILOAN", "Cuci Kering Setrika", speed_tier, is_promo, promo_name, n)
    if any(x in nl for x in ['cuci kering lipat', 'ckl']):
        return _r("LAUNDRY_KILOAN", "Cuci Kering Lipat", speed_tier, is_promo, promo_name, n)
    if 'setrika kiloan' in nl or ('setrika' in nl and 'kiloan' in nl):
        return _r("LAUNDRY_KILOAN", "Setrika Saja", speed_tier, is_promo, promo_name, n)
    if 'setrika' in nl and ('reguler' in nl or 'regular' in nl):
        return _r("LAUNDRY_KILOAN", "Setrika Saja", speed_tier, is_promo, promo_name, n)
    if 'setrika satuan' in nl or nl.startswith('10. setrika'):
        return _r("LAUNDRY_SATUAN", "Setrika", speed_tier, is_promo, promo_name, n)
    if 'setrika sprei' in nl:
        return _r("BEDDING", "Sprei Set", speed_tier, is_promo, promo_name, n)
    
    # LAUNDRY SATUAN
    if any(x in nl for x in ['laundry satuan', 'satuan']):
        if speed_tier in ("EXPRESS", "SAME_DAY"):
            return _r("LAUNDRY_SATUAN", "Express", speed_tier, is_promo, promo_name, n)
        return _r("LAUNDRY_SATUAN", "Reguler", speed_tier, is_promo, promo_name, n)
    
    # HOUSEHOLD
    if 'gordyn' in nl or 'gorden' in nl:
        return _r("HOUSEHOLD", "Gordyn", speed_tier, is_promo, promo_name, n)
    if 'karpet' in nl:
        return _r("HOUSEHOLD", "Karpet", speed_tier, is_promo, promo_name, n)
    if 'sofa' in nl or 'stool' in nl:
        return _r("HOUSEHOLD", "Sofa", speed_tier, is_promo, promo_name, n)
    
    # LAINNYA
    if 'helm' in nl:
        return _r("LAINNYA", "Helm", speed_tier, is_promo, promo_name, n)
    if 'koper' in nl:
        return _r("LAINNYA", "Koper", speed_tier, is_promo, promo_name, n)
    if 'boneka' in nl and 'besar' in nl:
        return _r("LAINNYA", "Boneka", speed_tier, is_promo, promo_name, n)
    if any(x in nl for x in ['baby car', 'baby stroller', 'stroller']):
        return _r("LAINNYA", "Baby Items", speed_tier, is_promo, promo_name, n)
    if 'topi' in nl:
        return _r("LAINNYA", "Topi", speed_tier, is_promo, promo_name, n)
    if 'hanger' in nl:
        return _r("LAINNYA", "Hanger", speed_tier, is_promo, promo_name, n)
    if 'sarung tangan' in nl or 'pelindung' in nl:
        return _r("LAINNYA", "Accessories", speed_tier, is_promo, promo_name, n)
    
    # Catch promos that mention base products
    if is_promo:
        if 'recolor' in nl:
            if 'bag' in nl or 'tas' in nl:
                return _r("TAS", "Recolor", speed_tier, True, promo_name, n)
            return _r("SEPATU", "Recolor", speed_tier, True, promo_name, n)
    
    # Fallback
    return _r("LAINNYA", "Accessories", speed_tier, is_promo, promo_name, n)


def _r(kategori, sub_kategori, speed_tier, is_promo, promo_name, original):
    return {
        "kategori": kategori,
        "sub_kategori": sub_kategori,
        "speed_tier": speed_tier,
        "is_promo": is_promo,
        "promo_name": promo_name,
        "base_product": original,
    }


if __name__ == "__main__":
    import sqlite3
    import json
    
    # Load all unique product names from both sources
    products = set()
    
    db1 = "/Users/erikmahendra/Desktop/SIJI_Analytics/automation/siji_database.db"
    db2 = "/Users/erikmahendra/clawd/projects/siji/siji_customers.db"
    
    conn1 = sqlite3.connect(db1)
    for row in conn1.execute("SELECT DISTINCT nama_layanan FROM transactions WHERE nama_layanan IS NOT NULL"):
        products.add(row[0])
    conn1.close()
    
    conn2 = sqlite3.connect(db2)
    for row in conn2.execute("SELECT DISTINCT nama_layanan FROM transactions WHERE nama_layanan IS NOT NULL AND nama_layanan NOT IN ('Belum Diambil','Diambil Semua','Diambil')"):
        products.add(row[0])
    conn2.close()
    
    # Classify all
    results = {}
    for p in sorted(products):
        r = classify_product(p)
        results[p] = r
    
    # Summary
    cat_counts = {}
    unmapped = []
    for p, r in results.items():
        k = f"{r['kategori']} > {r['sub_kategori']}"
        cat_counts[k] = cat_counts.get(k, 0) + 1
    
    print(f"\nTotal unique products: {len(results)}")
    print(f"\nCategory distribution:")
    for k in sorted(cat_counts.keys()):
        print(f"  {k}: {cat_counts[k]}")
    
    print(f"\nPromo products: {sum(1 for r in results.values() if r['is_promo'])}")
    print(f"Non-promo: {sum(1 for r in results.values() if not r['is_promo'])}")
    
    # Show full mapping
    print(f"\n{'='*100}")
    print(f"{'Original Name':<70} {'Kategori':<18} {'Sub':<20} {'Promo'}")
    print(f"{'='*100}")
    for p in sorted(results.keys()):
        r = results[p]
        promo_str = r['promo_name'] if r['is_promo'] else ''
        print(f"{p[:69]:<70} {r['kategori']:<18} {r['sub_kategori']:<20} {promo_str}")
