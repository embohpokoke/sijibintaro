"""
SIJI Address Normalizer v2
Robust fuzzy matching for SIJI Bintaro laundry addresses.
Uses: exact alias match → fuzzy alias match → pattern-based detection → fallback normalization.
"""

import json
import re
import os
from difflib import SequenceMatcher

REFERENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'location_references.json')

class LocationNormalizer:
    def __init__(self, reference_path=None, pg_conn=None):
        self.pg = pg_conn
        self.references = []
        
        # Try loading from file
        path = reference_path or REFERENCE_PATH
        if os.path.exists(path):
            with open(path) as f:
                self.references = json.load(f)
        elif pg_conn:
            self.references = self._load_from_db()
        
        # Build alias → ref index (longest aliases first for greedy matching)
        self._alias_pairs = []  # [(alias_lower, ref), ...] sorted by alias length DESC
        for ref in self.references:
            for alias in ref.get('aliases', []):
                self._alias_pairs.append((alias.lower().strip(), ref))
        self._alias_pairs.sort(key=lambda x: len(x[0]), reverse=True)
    
    def _load_from_db(self):
        cur = self.pg.cursor()
        cur.execute("SELECT canonical_name, category, kelurahan, kecamatan, aliases FROM location_references")
        refs = []
        for row in cur.fetchall():
            refs.append({
                'canonical_name': row[0], 'category': row[1],
                'kelurahan': row[2], 'kecamatan': row[3], 'aliases': row[4] or []
            })
        return refs
    
    def normalize(self, raw_address):
        if not raw_address or raw_address.strip() in ('-', '', 'L3', 'lantai 3', 'Lantai 3'):
            return self._result(raw_address, raw_address, None, None, None, None, 0, True)
        
        addr = raw_address.strip()
        addr_lower = addr.lower()
        
        # === Step 1: Exact substring alias match (most reliable) ===
        for alias, ref in self._alias_pairs:
            if alias in addr_lower:
                normalized = self._replace_and_format(addr, alias, ref)
                return self._result(raw_address, normalized, ref['canonical_name'],
                                   ref['category'], ref['kelurahan'], ref['kecamatan'], 95, False)
        
        # === Step 2: Fuzzy match - compare address prefix against aliases ===
        # Extract potential complex name: first 1-4 words
        best_match = None
        best_score = 0
        best_alias = None
        
        # Try matching whole address and progressively smaller prefixes
        words = addr_lower.replace(',', ' ').replace('.', ' ').split()
        candidates = []
        for n in range(min(5, len(words)), 0, -1):
            candidates.append(' '.join(words[:n]))
        # Also try without first word (in case of prefix like "Jl", "Cluster", etc)
        if len(words) > 2:
            for n in range(min(4, len(words)-1), 0, -1):
                candidates.append(' '.join(words[1:1+n]))
        
        for candidate in candidates:
            for alias, ref in self._alias_pairs:
                score = SequenceMatcher(None, candidate, alias).ratio()
                if score > best_score and score > 0.65:
                    best_score = score
                    best_match = ref
                    best_alias = alias
        
        if best_match and best_score > 0.65:
            confidence = min(best_score * 100, 94)  # Cap below exact match
            normalized = self._replace_fuzzy_and_format(addr, best_match)
            needs_review = confidence < 80
            return self._result(raw_address, normalized, best_match['canonical_name'],
                               best_match['category'], best_match['kelurahan'], best_match['kecamatan'],
                               confidence, needs_review)
        
        # === Step 3: Fallback - just format what we have ===
        normalized = self._general_normalize(addr)
        return self._result(raw_address, normalized, None, None, None, None, 30, True)
    
    def _replace_and_format(self, addr, matched_alias, ref):
        """Replace matched alias with canonical name, apply formatting"""
        canonical = ref['canonical_name']
        # Case-insensitive replace
        pattern = re.compile(re.escape(matched_alias), re.IGNORECASE)
        result = pattern.sub(canonical, addr, count=1)
        return self._format_address(result)
    
    def _replace_fuzzy_and_format(self, addr, ref):
        """For fuzzy matches, replace the beginning of address with canonical name"""
        canonical = ref['canonical_name']
        addr_lower = addr.lower()
        
        # Try to find best matching portion to replace
        for alias in ref.get('aliases', []):
            alias_l = alias.lower()
            if alias_l in addr_lower:
                pattern = re.compile(re.escape(alias_l), re.IGNORECASE)
                result = pattern.sub(canonical, addr, count=1)
                return self._format_address(result)
        
        # If no exact substring found, replace first N words that look like complex name
        words = addr.split()
        # Find where the address detail starts (Blok, No, numbers)
        split_idx = 0
        for i, w in enumerate(words):
            wl = w.lower().rstrip('.,')
            if wl in ('blok', 'no', 'no.') or (w[0].isdigit() and i > 0):
                split_idx = i
                break
            split_idx = i + 1
        
        detail = ' '.join(words[split_idx:]) if split_idx < len(words) else ''
        result = f"{canonical} {detail}".strip()
        return self._format_address(result)
    
    def _format_address(self, addr):
        """Standardize Blok/No/spacing"""
        # Blok capitalization
        addr = re.sub(r'\bblok\b', 'Blok', addr, flags=re.IGNORECASE)
        # "no" → "No." when followed by number or space+number
        addr = re.sub(r'\bno\.?\s*(\d)', r'No.\1', addr, flags=re.IGNORECASE)
        # Fix common patterns
        addr = re.sub(r'\s+', ' ', addr).strip()  # double spaces
        addr = re.sub(r',\s*', ', ', addr)  # comma spacing
        addr = re.sub(r'\bblok\s+', 'Blok ', addr, flags=re.IGNORECASE)
        return addr
    
    def _general_normalize(self, addr):
        """Fallback: basic capitalization + formatting"""
        result = self._format_address(addr)
        # Title case for words, preserve block/unit codes
        parts = result.split()
        normalized = []
        for p in parts:
            pl = p.lower().rstrip('.,')
            if pl in ('jl', 'jl.', 'rt', 'rw'):
                normalized.append(p.upper() if len(p) <= 3 else p.capitalize())
            elif p.isalpha() and len(p) <= 3:
                normalized.append(p.upper())
            elif p[0].islower() and not any(c.isupper() for c in p[1:]):
                normalized.append(p.capitalize())
            else:
                normalized.append(p)
        return ' '.join(normalized)
    
    def _result(self, original, normalized, complex_name, category, kelurahan, kecamatan, confidence, needs_review):
        return {
            'original': original, 'normalized': normalized,
            'complex_name': complex_name, 'category': category,
            'kelurahan': kelurahan, 'kecamatan': kecamatan,
            'confidence': round(confidence, 1), 'needs_review': needs_review
        }
    
    def batch_normalize_db(self, limit=None):
        """Normalize all addresses in transactions table"""
        if not self.pg:
            print("No DB connection")
            return
        
        cur = self.pg.cursor()
        
        # Get unique addresses
        q = "SELECT DISTINCT customer_address FROM transactions WHERE customer_address IS NOT NULL AND customer_address != '-'"
        if limit:
            q += f" LIMIT {limit}"
        cur.execute(q)
        addresses = [row[0] for row in cur.fetchall()]
        
        print(f"Normalizing {len(addresses)} unique addresses...")
        
        results = {'matched': 0, 'fuzzy': 0, 'unmatched': 0, 'empty': 0}
        updates = []  # (normalized, complex, confidence, original)
        
        for addr in addresses:
            r = self.normalize(addr)
            if r['confidence'] >= 95:
                results['matched'] += 1
            elif r['confidence'] >= 65:
                results['fuzzy'] += 1
            elif r['confidence'] > 0:
                results['unmatched'] += 1
            else:
                results['empty'] += 1
            
            updates.append((r['normalized'], r['complex_name'], r['confidence'], addr))
        
        # Batch update
        print("Applying updates to DB...")
        updated = 0
        for norm, complex_name, conf, orig in updates:
            cur.execute("""
                UPDATE transactions 
                SET customer_address_normalized = %s, address_confidence = %s
                WHERE customer_address = %s
            """, (norm, conf, orig))
            updated += cur.rowcount
        
        self.pg.commit()
        
        print(f"\nResults:")
        print(f"  Exact match (95%+): {results['matched']}")
        print(f"  Fuzzy match (65-94%): {results['fuzzy']}")
        print(f"  Unmatched (30%): {results['unmatched']}")
        print(f"  Empty/invalid: {results['empty']}")
        print(f"  Total transactions updated: {updated}")
        
        # Show top complexes by transaction count
        cur.execute("""
            SELECT customer_address_normalized, COUNT(*) as cnt 
            FROM transactions 
            WHERE address_confidence >= 65 
            GROUP BY customer_address_normalized 
            ORDER BY cnt DESC LIMIT 20
        """)
        print(f"\nTop normalized addresses (confidence >= 65%):")
        for row in cur.fetchall():
            print(f"  {row[1]:>5} tx: {row[0]}")
        
        return results


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'batch':
        import psycopg2
        pg = psycopg2.connect(host='localhost', port=5432, dbname='siji_db', user='siji', password=os.environ.get('SIJI_DB_PASSWORD', 'siji2026db'))
        ref_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'location_references.json')
        if not os.path.exists(ref_path):
            ref_path = None
        normalizer = LocationNormalizer(reference_path=ref_path, pg_conn=pg)
        normalizer.batch_normalize_db()
        pg.close()
    elif len(sys.argv) > 1 and sys.argv[1] == 'test':
        # Quick test
        normalizer = LocationNormalizer()
        tests = [
            "emerald residen ,blok G no 7",
            "emrald townhose blok ag no20",
            "Emerld Garden Blok H28",
            "em res e12",
            "disc fiore blok D.20",
            "dc terra blok e 1",
            "discofery altezza block dz no.18",
            "Emerlad Townhous Ad 28",
            "emrald towone hose AA30",
            "dis. aluvia R 12",
            "kebayoran village blok k no 6",
            "vasa loka,perigi",
            "habitat 11",
            "Apartement Embarcadero",
            "Casa Deparco Appart",
            "bumi bihtaro asri blok c 6",
            "neo vierra B. A10",
            "Adora permata b3 10",
            "grasia residen,blok D 05,graha raya",
            "Emerakd Townhouse Blok AD No.10",
        ]
        print(f"{'Original':<45} {'Normalized':<45} {'Complex':<25} {'Conf':>5}")
        print("=" * 125)
        for t in tests:
            r = normalizer.normalize(t)
            cname = r['complex_name'] or '-'
            print(f"{t:<45} {r['normalized']:<45} {cname:<25} {r['confidence']:>5.1f}")
    else:
        print("Usage: python address_normalizer.py [test|batch]")
