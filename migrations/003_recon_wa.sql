-- ============================================================
-- SIJI BINTARO: WA Payment Proof Reconciliation
-- Add columns to mutasi_rekening for linking WA payment proofs
-- ============================================================

SET search_path TO siji_bintaro, public;

ALTER TABLE mutasi_rekening
ADD COLUMN IF NOT EXISTS wa_media_path TEXT,
ADD COLUMN IF NOT EXISTS recon_notes TEXT,
ADD COLUMN IF NOT EXISTS recon_at TIMESTAMPTZ;

-- Index for quick lookup of unlinked incoming (kredit = customer payment in)
CREATE INDEX IF NOT EXISTS idx_mutasi_rekening_incoming_unlinked
    ON mutasi_rekening (tanggal, status_link)
    WHERE tipe = 'kredit' AND status_link = 'unlinked';
