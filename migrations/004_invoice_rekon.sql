-- ============================================================
-- SIJI BINTARO: Invoice Reconciliation Pipeline
-- Parses FAKTUR ELEKTRONIK from WA + links payment proofs
-- Migration: 004_invoice_rekon
-- ============================================================

SET search_path TO siji_bintaro, public;

-- Payment signals: customer-sent payment proof images/captions
CREATE TABLE IF NOT EXISTS payment_signal (
    id                   SERIAL PRIMARY KEY,
    conversation_jid     TEXT NOT NULL,
    wa_message_id        TEXT UNIQUE,
    wa_timestamp         TIMESTAMPTZ NOT NULL,
    signal_type          TEXT NOT NULL CHECK (signal_type IN ('text_caption', 'vision_ocr', 'text_only')),
    is_payment           BOOLEAN NOT NULL DEFAULT false,
    bank_name            TEXT,
    amount               BIGINT,
    payment_datetime     TEXT,
    reference            TEXT,
    recipient            TEXT,
    raw_caption          TEXT,
    local_file_path      TEXT,
    ocr_raw              JSONB,
    confidence           NUMERIC(4,3) DEFAULT 0,
    -- matching
    invoice_rekon_id     INT,
    rekon_status         TEXT NOT NULL DEFAULT 'unmatched'
                         CHECK (rekon_status IN ('unmatched', 'auto_matched', 'manual_matched', 'confirmed', 'rejected')),
    rekon_at             TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Parsed invoices from FAKTUR ELEKTRONIK WA messages
CREATE TABLE IF NOT EXISTS invoice_rekon (
    id                   SERIAL PRIMARY KEY,
    nota_number          TEXT UNIQUE NOT NULL,
    conversation_jid     TEXT NOT NULL,
    phone                TEXT,
    customer_name        TEXT,
    total_tagihan        BIGINT NOT NULL,
    grand_total          BIGINT,
    status_invoice       TEXT NOT NULL DEFAULT 'belum_lunas'
                         CHECK (status_invoice IN ('belum_lunas', 'lunas', 'unknown')),
    layanan              TEXT,
    terima_at            TIMESTAMPTZ,
    selesai_at           TIMESTAMPTZ,
    wa_timestamp         TIMESTAMPTZ NOT NULL,
    raw_message_text     TEXT,
    -- rekon
    payment_signal_id    INT REFERENCES payment_signal(id) ON DELETE SET NULL,
    pemasukan_manual_id  INT REFERENCES pemasukan_manual(id) ON DELETE SET NULL,
    rekon_status         TEXT NOT NULL DEFAULT 'unmatched'
                         CHECK (rekon_status IN ('unmatched', 'auto_matched', 'manual_matched', 'confirmed')),
    rekon_at             TIMESTAMPTZ,
    rekon_notes          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add FK from payment_signal to invoice_rekon
ALTER TABLE payment_signal
    ADD CONSTRAINT fk_ps_invoice
    FOREIGN KEY (invoice_rekon_id) REFERENCES invoice_rekon(id) ON DELETE SET NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_invoice_rekon_jid      ON invoice_rekon (conversation_jid);
CREATE INDEX IF NOT EXISTS idx_invoice_rekon_status   ON invoice_rekon (rekon_status);
CREATE INDEX IF NOT EXISTS idx_invoice_rekon_ts       ON invoice_rekon (wa_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_invoice_rekon_nota     ON invoice_rekon (nota_number);
CREATE INDEX IF NOT EXISTS idx_payment_signal_jid     ON payment_signal (conversation_jid);
CREATE INDEX IF NOT EXISTS idx_payment_signal_status  ON payment_signal (rekon_status);
CREATE INDEX IF NOT EXISTS idx_payment_signal_ts      ON payment_signal (wa_timestamp DESC);
