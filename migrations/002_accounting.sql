-- ============================================================
-- SIJI BINTARO ACCOUNTING SYSTEM
-- Schema: siji_bintaro
-- Migration: 002_accounting
-- ============================================================

SET search_path TO siji_bintaro, public;

CREATE TABLE IF NOT EXISTS pengeluaran_kategori (
    id          SERIAL PRIMARY KEY,
    nama        VARCHAR(100) NOT NULL,
    parent_id   INT REFERENCES pengeluaran_kategori(id) ON DELETE SET NULL,
    icon        VARCHAR(10) DEFAULT '📦',
    aktif       BOOLEAN NOT NULL DEFAULT true,
    urutan      INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS supplier (
    id          SERIAL PRIMARY KEY,
    nama        VARCHAR(200) NOT NULL,
    tipe        VARCHAR(50) DEFAULT 'lainnya'
                CHECK (tipe IN ('online', 'offline', 'vendor', 'individu', 'lainnya')),
    kontak      VARCHAR(100),
    catatan     TEXT,
    aktif       BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pengeluaran (
    id              SERIAL PRIMARY KEY,
    tanggal         DATE NOT NULL DEFAULT CURRENT_DATE,
    nominal         BIGINT NOT NULL CHECK (nominal > 0),
    kategori_id     INT REFERENCES pengeluaran_kategori(id) ON DELETE SET NULL,
    supplier_id     INT REFERENCES supplier(id) ON DELETE SET NULL,
    deskripsi       TEXT NOT NULL,
    metode_bayar    VARCHAR(20) NOT NULL DEFAULT 'transfer_bca'
                    CHECK (metode_bayar IN ('transfer_bca', 'cash', 'qris', 'edc', 'lainnya')),
    no_referensi    VARCHAR(100),
    dicatat_oleh    VARCHAR(50),
    sumber_input    VARCHAR(50) NOT NULL DEFAULT 'wa_chat'
                    CHECK (sumber_input IN ('wa_chat', 'dashboard', 'import')),
    foto_struk      TEXT,
    catatan         TEXT,
    is_deleted      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pemasukan_manual (
    id              SERIAL PRIMARY KEY,
    tanggal         DATE NOT NULL DEFAULT CURRENT_DATE,
    nama_customer   VARCHAR(100),
    layanan         VARCHAR(200),
    kategori        VARCHAR(50),
    nominal         BIGINT NOT NULL CHECK (nominal > 0),
    metode_bayar    VARCHAR(20) NOT NULL DEFAULT 'cash'
                    CHECK (metode_bayar IN ('cash', 'transfer', 'qris', 'edc')),
    dicatat_oleh    VARCHAR(50),
    transaction_id  INT,
    foto_bukti      TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'unverified'
                    CHECK (status IN ('unverified', 'verified', 'duplicate')),
    catatan         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mutasi_rekening (
    id              SERIAL PRIMARY KEY,
    tanggal         DATE NOT NULL,
    no_urut         VARCHAR(20),
    nominal         BIGINT NOT NULL CHECK (nominal > 0),
    tipe            VARCHAR(10) NOT NULL
                    CHECK (tipe IN ('debit', 'kredit')),
    keterangan      TEXT,
    penerima        VARCHAR(200),
    no_rek_penerima VARCHAR(50),
    pengeluaran_id  INT REFERENCES pengeluaran(id) ON DELETE SET NULL,
    status_link     VARCHAR(20) NOT NULL DEFAULT 'unlinked'
                    CHECK (status_link IN ('unlinked', 'linked', 'ignored')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pengeluaran_tanggal ON pengeluaran(tanggal);
CREATE INDEX IF NOT EXISTS idx_pengeluaran_kategori ON pengeluaran(kategori_id);
CREATE INDEX IF NOT EXISTS idx_pengeluaran_supplier ON pengeluaran(supplier_id);
CREATE INDEX IF NOT EXISTS idx_pengeluaran_not_deleted ON pengeluaran(is_deleted);
CREATE INDEX IF NOT EXISTS idx_pemasukan_manual_tanggal ON pemasukan_manual(tanggal);
CREATE INDEX IF NOT EXISTS idx_pemasukan_manual_status ON pemasukan_manual(status);
CREATE INDEX IF NOT EXISTS idx_mutasi_rekening_tanggal ON mutasi_rekening(tanggal);
CREATE INDEX IF NOT EXISTS idx_mutasi_rekening_status ON mutasi_rekening(status_link);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pengeluaran_kategori_root
    ON pengeluaran_kategori (LOWER(nama))
    WHERE parent_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pengeluaran_kategori_child
    ON pengeluaran_kategori (parent_id, LOWER(nama))
    WHERE parent_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_nama
    ON supplier (LOWER(nama));

INSERT INTO pengeluaran_kategori (nama, parent_id, icon, urutan)
VALUES
    ('Gaji & Honor', NULL, '👤', 1),
    ('Bahan & Supplies', NULL, '🧴', 2),
    ('Utilitas', NULL, '🏠', 3),
    ('Perawatan & Perbaikan', NULL, '🔧', 4),
    ('Belanja Online', NULL, '📦', 5),
    ('Operasional', NULL, '🚗', 6),
    ('Lain-lain', NULL, '💼', 7)
ON CONFLICT DO NOTHING;

DO $$
DECLARE
    id_gaji INT;
    id_bahan INT;
    id_utilitas INT;
    id_perbaikan INT;
    id_online INT;
    id_operasional INT;
BEGIN
    SELECT id INTO id_gaji FROM pengeluaran_kategori WHERE nama = 'Gaji & Honor' AND parent_id IS NULL;
    SELECT id INTO id_bahan FROM pengeluaran_kategori WHERE nama = 'Bahan & Supplies' AND parent_id IS NULL;
    SELECT id INTO id_utilitas FROM pengeluaran_kategori WHERE nama = 'Utilitas' AND parent_id IS NULL;
    SELECT id INTO id_perbaikan FROM pengeluaran_kategori WHERE nama = 'Perawatan & Perbaikan' AND parent_id IS NULL;
    SELECT id INTO id_online FROM pengeluaran_kategori WHERE nama = 'Belanja Online' AND parent_id IS NULL;
    SELECT id INTO id_operasional FROM pengeluaran_kategori WHERE nama = 'Operasional' AND parent_id IS NULL;

    INSERT INTO pengeluaran_kategori (nama, parent_id, icon, urutan)
    VALUES
        ('Gaji Karyawan', id_gaji, '👤', 1),
        ('Honor Kasir', id_gaji, '👤', 2),
        ('Bonus', id_gaji, '🎁', 3),
        ('Deterjen', id_bahan, '🧴', 1),
        ('Plastik', id_bahan, '🛍', 2),
        ('Tas Spunbond', id_bahan, '👜', 3),
        ('Gas', id_bahan, '🔥', 4),
        ('Air Galon', id_bahan, '💧', 5),
        ('Supplies Lainnya', id_bahan, '📦', 6),
        ('Listrik', id_utilitas, '⚡', 1),
        ('Air PAM', id_utilitas, '💧', 2),
        ('Internet', id_utilitas, '📡', 3),
        ('Service Mesin', id_perbaikan, '⚙️', 1),
        ('Vendor Eksternal', id_perbaikan, '🔧', 2),
        ('Renovasi', id_perbaikan, '🏗', 3),
        ('Tokopedia', id_online, '🟢', 1),
        ('Shopee', id_online, '🟠', 2),
        ('Lazada', id_online, '🔵', 3),
        ('E-commerce Lainnya', id_online, '📦', 4),
        ('Kurir / Ongkir', id_operasional, '🚚', 1),
        ('Transportasi', id_operasional, '🚗', 2),
        ('ATK', id_operasional, '📋', 3)
    ON CONFLICT DO NOTHING;
END $$;
