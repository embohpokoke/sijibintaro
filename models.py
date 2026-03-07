from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class LamaranCreate(BaseModel):
    nama: str = Field(..., min_length=1, max_length=100)
    whatsapp: str = Field(..., min_length=8, max_length=20)
    domisili: Optional[str] = Field(None, max_length=200)
    posisi: str = Field(..., min_length=1, max_length=100)
    pengalaman: Optional[str] = Field(None, max_length=1000)

class LamaranResponse(BaseModel):
    id: int
    nama: str
    whatsapp: str
    domisili: Optional[str]
    posisi: str
    pengalaman: Optional[str]
    cv_path: Optional[str]
    status: str
    created_at: str
    notes: Optional[str] = None
    tgl_dihubungi: Optional[str] = None
    tgl_interview: Optional[str] = None

class LamaranStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(baru|review|interview|diterima|ditolak)$")

class LamaranPipelineUpdate(BaseModel):
    status: str = Field(..., pattern="^(baru|dihubungi|interview|diterima|ditolak|tidak_aktif)$")
    notes: Optional[str] = None
    tgl_dihubungi: Optional[str] = None
    tgl_interview: Optional[str] = None

class PipelineUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    tgl_dihubungi: Optional[str] = None
    tgl_interview: Optional[str] = None

class SendWARequest(BaseModel):
    message: str

class FeedbackCreate(BaseModel):
    nama: Optional[str] = Field(None, max_length=100)
    whatsapp: Optional[str] = Field(None, max_length=20)
    rating: int = Field(..., ge=1, le=5)
    layanan: Optional[str] = Field(None, max_length=100)
    komentar: Optional[str] = Field(None, max_length=1000)

class FeedbackResponse(BaseModel):
    id: int
    nama: Optional[str]
    whatsapp: Optional[str]
    rating: int
    layanan: Optional[str]
    komentar: Optional[str]
    nomor_nota: Optional[str]
    foto_path: Optional[str]
    created_at: str

class FeedbackStats(BaseModel):
    total_feedback: int
    average_rating: float
    rating_distribution: dict
    feedback_per_layanan: dict

class GoogleReview(BaseModel):
    id: int
    author_name: Optional[str]
    rating: int
    text: Optional[str]
    time: Optional[str]
    profile_photo_url: Optional[str]
    fetched_at: str

class Response(BaseModel):
    success: bool
    message: str
    data: Optional[dict] = None

# ─── Karyawan (Employee) Models ─────────────────────────────────────────────

VALID_TIPE_KONTRAK = ["probation", "kontrak", "tetap"]
VALID_STATUS_KERJA = ["aktif", "resign", "terminated", "cuti"]

class KaryawanCreate(BaseModel):
    lamaran_id: Optional[int] = None
    nama: str
    whatsapp: Optional[str] = None
    posisi: str
    tipe_kontrak: Optional[str] = "probation"
    status_kerja: Optional[str] = "aktif"
    tgl_bergabung: Optional[str] = None
    tgl_akhir_kontrak: Optional[str] = None
    no_ktp: Optional[str] = None
    alamat: Optional[str] = None
    gaji_pokok: Optional[int] = None
    catatan: Optional[str] = None

class KaryawanUpdate(BaseModel):
    nama: Optional[str] = None
    whatsapp: Optional[str] = None
    posisi: Optional[str] = None
    tipe_kontrak: Optional[str] = None
    status_kerja: Optional[str] = None
    tgl_bergabung: Optional[str] = None
    tgl_akhir_kontrak: Optional[str] = None
    no_ktp: Optional[str] = None
    alamat: Optional[str] = None
    gaji_pokok: Optional[int] = None
    catatan: Optional[str] = None

class KaryawanResponse(BaseModel):
    id: int
    lamaran_id: Optional[int]
    nama: str
    whatsapp: Optional[str]
    posisi: str
    tipe_kontrak: str
    status_kerja: str
    tgl_bergabung: Optional[str]
    tgl_akhir_kontrak: Optional[str]
    no_ktp: Optional[str]
    alamat: Optional[str]
    gaji_pokok: Optional[int]
    catatan: Optional[str]
    created_at: str
    updated_at: str