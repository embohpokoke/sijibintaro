from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
import os
import shutil
import sqlite3
import csv
import io
from datetime import datetime
from typing import Optional, List
import json

from database import init_db, get_db, get_db_dict
from models import *
import httpx
from wa_webhook import router as wa_router
from dashboard_api import router as dashboard_router
from wa_crm_api import router as wa_crm_router
from order_tracking_api import router as order_tracking_router

app = FastAPI(title="SIJI Bintaro API", version="1.0.0")
app.include_router(wa_router)
app.include_router(dashboard_router)
app.include_router(wa_crm_router)
app.include_router(order_tracking_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sijibintaro.id",
        "https://www.sijibintaro.id",
        "https://dashboard.sijibintaro.id",
        "https://gowa.sijibintaro.id",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer(auto_error=False)
ADMIN_TOKEN = os.getenv("SIJI_ADMIN_TOKEN", "sijiadmin2026")

# Upload directories
CV_UPLOAD_DIR = "/root/sijibintaro.id/api/uploads/cv"

# Initialize database on startup
@app.on_event("startup")

def startup():
    init_db()
    os.makedirs(CV_UPLOAD_DIR, exist_ok=True)

def verify_admin_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    # Token check disabled — nginx basic auth is sufficient
    return "ok"

# Helper functions
def save_cv_file(file: UploadFile) -> str:
    """Save uploaded CV file and return the path"""
    # Validate file type
    allowed_types = ['.pdf', '.jpg', '.jpeg', '.png']
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_types:
        raise HTTPException(status_code=400, detail="File type not allowed. Use PDF, JPG, JPEG, or PNG.")
    
    # Validate file size (5MB max)
    file.file.seek(0, 2)  # Go to end of file
    file_size = file.file.tell()
    file.file.seek(0)  # Go back to start
    if file_size > 5 * 1024 * 1024:  # 5MB
        raise HTTPException(status_code=400, detail="File size too large. Maximum 5MB allowed.")
    
    # Generate unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{file.filename}"
    file_path = os.path.join(CV_UPLOAD_DIR, filename)
    
    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return file_path

# Public endpoints
@app.post("/api/lamaran", response_model=Response)
async def submit_job_application(
    nama: str = Form(...),
    whatsapp: str = Form(...),
    domisili: str = Form(None),
    posisi: str = Form(...),
    pengalaman: str = Form(None),
    cv: UploadFile = File(None)
):
    try:
        # Validate required fields
        if not nama.strip():
            raise HTTPException(status_code=400, detail="Nama is required")
        if not whatsapp.strip():
            raise HTTPException(status_code=400, detail="WhatsApp number is required")
        if not posisi.strip():
            raise HTTPException(status_code=400, detail="Posisi is required")
        
        cv_path = None
        if cv and cv.filename:
            cv_path = save_cv_file(cv)
        
        # Insert to database
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO lamaran (nama, whatsapp, domisili, posisi, pengalaman, cv_path)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (nama.strip(), whatsapp.strip(), domisili.strip() if domisili else None, 
                 posisi.strip(), pengalaman.strip() if pengalaman else None, cv_path))
            conn.commit()
            
        return Response(
            success=True,
            message="Lamaran berhasil dikirim! Tim kami akan menghubungi kamu via WhatsApp.",
            data={"id": cursor.lastrowid}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/feedback", response_model=Response)
async def submit_feedback(
    rating: int = Form(...),
    nama: Optional[str] = Form(None),
    whatsapp: Optional[str] = Form(None),
    layanan: Optional[str] = Form(None),
    komentar: Optional[str] = Form(None),
    nomor_nota: Optional[str] = Form(None),
    foto: Optional[UploadFile] = File(None)
):
    try:
        foto_path = None
        if foto and foto.filename:
            # Validate file
            allowed = ['.jpg', '.jpeg', '.png', '.pdf']
            ext = os.path.splitext(foto.filename)[1].lower()
            if ext not in allowed:
                return Response(success=False, message="File harus JPG, PNG, atau PDF")
            content = await foto.read()
            if len(content) > 5 * 1024 * 1024:
                return Response(success=False, message="Ukuran file maksimal 5MB")
            
            import uuid
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = f"uploads/feedback/{filename}"
            with open(filepath, "wb") as f:
                f.write(content)
            foto_path = filename
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO feedback (nama, whatsapp, rating, layanan, komentar, nomor_nota, foto_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (nama, whatsapp, rating, layanan, komentar, nomor_nota, foto_path))
            conn.commit()
            
        return Response(
            success=True,
            message="Terima kasih atas feedback-nya! Sangat berharga buat kami 🙏",
            data={"id": cursor.lastrowid}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/reviews", response_model=List[GoogleReview])
async def get_reviews():
    """Get cached Google reviews with rating >= 4"""
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM google_reviews 
                WHERE rating >= 4 
                ORDER BY fetched_at DESC 
                LIMIT 10
            """)
            reviews = cursor.fetchall()
            
        return reviews
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Admin endpoints
@app.get("/api/admin/lamaran", response_model=List[LamaranResponse])
async def get_all_applications(token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM lamaran 
                ORDER BY created_at DESC
            """)
            applications = cursor.fetchall()
            
        return applications
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/lamaran/stats")
async def get_lamaran_stats(token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) as cnt FROM lamaran GROUP BY status")
            rows = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) as total FROM lamaran")
            total = cursor.fetchone()["total"]
        stats = {s: 0 for s in VALID_PIPELINE_STATUSES}
        stats["imported"] = 0
        stats["review"] = 0
        stats["total"] = total
        for row in rows:
            stats[row["status"]] = row["cnt"]
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/lamaran/{application_id}", response_model=LamaranResponse)
async def get_application_by_id(application_id: int, token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM lamaran WHERE id = ?", (application_id,))
            application = cursor.fetchone()
            
        if not application:
            raise HTTPException(status_code=404, detail="Application not found")
            
        return application
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/admin/lamaran/{application_id}/status", response_model=Response)
async def update_application_status(
    application_id: int, 
    status_update: LamaranStatusUpdate,
    token: str = Depends(verify_admin_token)
):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE lamaran SET status = ? WHERE id = ?",
                (status_update.status, application_id)
            )
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Application not found")
            
            conn.commit()
            
        return Response(
            success=True,
            message=f"Status updated to {status_update.status}"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

VALID_PIPELINE_STATUSES = ["baru", "dihubungi", "interview", "diterima", "ditolak", "tidak_aktif"]


@app.patch("/api/admin/lamaran/{application_id}/pipeline")
async def update_pipeline(
    application_id: int,
    update: PipelineUpdate,
    token: str = Depends(verify_admin_token)
):
    try:
        if update.status and update.status not in VALID_PIPELINE_STATUSES + ["imported", "review"]:
            raise HTTPException(status_code=400, detail=f"Invalid status: {update.status}")
        
        fields = []
        values = []
        if update.status is not None:
            fields.append("status = ?")
            values.append(update.status)
            if update.status == "dihubungi" and not update.tgl_dihubungi:
                fields.append("tgl_dihubungi = ?")
                values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if update.notes is not None:
            fields.append("notes = ?")
            values.append(update.notes)
        if update.tgl_dihubungi is not None:
            fields.append("tgl_dihubungi = ?")
            values.append(update.tgl_dihubungi)
        if update.tgl_interview is not None:
            fields.append("tgl_interview = ?")
            values.append(update.tgl_interview)
        
        fields.append("tgl_update = ?")
        values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        values.append(application_id)
        
        if not fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE lamaran SET {', '.join(fields)} WHERE id = ?",
                values
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Lamaran not found")
            conn.commit()
        
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM lamaran WHERE id = ?", (application_id,))
            updated = dict(cursor.fetchone())

        # Auto-create karyawan record when status becomes "diterima"
        if update.status == "diterima":
            with get_db_dict() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM karyawan WHERE lamaran_id = ?", (application_id,))
                existing_karyawan = cursor.fetchone()
            if not existing_karyawan:
                with get_db() as conn:
                    conn.execute(
                        """INSERT INTO karyawan (lamaran_id, nama, whatsapp, posisi, tgl_bergabung)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            application_id,
                            updated["nama"],
                            updated.get("whatsapp"),
                            updated.get("posisi", ""),
                            datetime.now().strftime("%Y-%m-%d"),
                        )
                    )
                    conn.commit()

        return {"success": True, "data": updated}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/lamaran/{application_id}/send-wa")
async def send_wa_to_applicant(
    application_id: int,
    req: SendWARequest,
    token: str = Depends(verify_admin_token)
):
    try:
        import httpx
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM lamaran WHERE id = ?", (application_id,))
            lam = cursor.fetchone()
        if not lam:
            raise HTTPException(status_code=404, detail="Lamaran not found")
        
        wa_number = lam["whatsapp"].replace("+", "").replace("-", "").replace(" ", "")
        if wa_number.startswith("0"):
            wa_number = "62" + wa_number[1:]
        
        fonnte_token = os.getenv("FONNTE_TOKEN", "")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.fonnte.com/send",
                headers={"Authorization": fonnte_token},
                data={"target": wa_number, "message": req.message, "countryCode": "62"},
                timeout=15
            )
        result = resp.json() if resp.status_code == 200 else {"status": False, "detail": resp.text}
        
        # Auto-update tgl_dihubungi if status is still baru
        if lam["status"] == "baru":
            with get_db() as conn:
                conn.execute(
                    "UPDATE lamaran SET status='dihubungi', tgl_dihubungi=?, tgl_update=? WHERE id=?",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), application_id)
                )
                conn.commit()
        
        return {"success": result.get("status", False), "detail": result, "wa_number": wa_number}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── End HR Pipeline ───────────────────────────────────────────────────────────

# ─── Karyawan (Employee) Endpoints ────────────────────────────────────────────

from models import KaryawanCreate, KaryawanUpdate, KaryawanResponse, VALID_TIPE_KONTRAK, VALID_STATUS_KERJA

@app.get("/api/admin/karyawan")
async def get_karyawan_list(
    status_kerja: Optional[str] = None,
    token: str = Depends(verify_admin_token)
):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            if status_kerja:
                cursor.execute("SELECT * FROM karyawan WHERE status_kerja = ? ORDER BY created_at DESC", (status_kerja,))
            else:
                cursor.execute("SELECT * FROM karyawan ORDER BY created_at DESC")
            rows = cursor.fetchall()
        return {"success": True, "data": rows, "total": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/karyawan/stats")
async def get_karyawan_stats(token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status_kerja, COUNT(*) as cnt FROM karyawan GROUP BY status_kerja")
            status_counts = {row["status_kerja"]: row["cnt"] for row in cursor.fetchall()}
            cursor.execute("SELECT COUNT(*) as total FROM karyawan")
            total = cursor.fetchone()["total"]
            cursor.execute("SELECT posisi, COUNT(*) as cnt FROM karyawan WHERE status_kerja='aktif' GROUP BY posisi")
            posisi_counts = {row["posisi"]: row["cnt"] for row in cursor.fetchall()}
        return {"success": True, "total": total, "by_status": status_counts, "by_posisi": posisi_counts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/karyawan/{karyawan_id}")
async def get_karyawan_detail(
    karyawan_id: int,
    token: str = Depends(verify_admin_token)
):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM karyawan WHERE id = ?", (karyawan_id,))
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Karyawan not found")
        return {"success": True, "data": row}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/karyawan")
async def create_karyawan(
    data: KaryawanCreate,
    token: str = Depends(verify_admin_token)
):
    try:
        if data.tipe_kontrak and data.tipe_kontrak not in VALID_TIPE_KONTRAK:
            raise HTTPException(status_code=400, detail=f"Invalid tipe_kontrak: {data.tipe_kontrak}")
        if data.status_kerja and data.status_kerja not in VALID_STATUS_KERJA:
            raise HTTPException(status_code=400, detail=f"Invalid status_kerja: {data.status_kerja}")
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO karyawan (lamaran_id, nama, whatsapp, posisi, tipe_kontrak, status_kerja,
                    tgl_bergabung, tgl_akhir_kontrak, no_ktp, alamat, gaji_pokok, catatan)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.lamaran_id, data.nama, data.whatsapp, data.posisi,
                data.tipe_kontrak or "probation", data.status_kerja or "aktif",
                data.tgl_bergabung, data.tgl_akhir_kontrak,
                data.no_ktp, data.alamat, data.gaji_pokok, data.catatan
            ))
            conn.commit()
            new_id = cursor.lastrowid
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM karyawan WHERE id = ?", (new_id,))
            row = dict(cursor.fetchone())
        return {"success": True, "message": "Karyawan berhasil ditambah", "data": row}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/admin/karyawan/{karyawan_id}")
async def update_karyawan(
    karyawan_id: int,
    data: KaryawanUpdate,
    token: str = Depends(verify_admin_token)
):
    try:
        if data.tipe_kontrak and data.tipe_kontrak not in VALID_TIPE_KONTRAK:
            raise HTTPException(status_code=400, detail=f"Invalid tipe_kontrak: {data.tipe_kontrak}")
        if data.status_kerja and data.status_kerja not in VALID_STATUS_KERJA:
            raise HTTPException(status_code=400, detail=f"Invalid status_kerja: {data.status_kerja}")

        fields = []
        values = []
        for field, value in data.dict(exclude_none=True).items():
            fields.append(f"{field} = ?")
            values.append(value)

        if not fields:
            raise HTTPException(status_code=400, detail="No fields to update")

        fields.append("updated_at = ?")
        values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        values.append(karyawan_id)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE karyawan SET {', '.join(fields)} WHERE id = ?", values)
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Karyawan not found")
            conn.commit()

        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM karyawan WHERE id = ?", (karyawan_id,))
            row = dict(cursor.fetchone())
        return {"success": True, "data": row}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── End Karyawan ──────────────────────────────────────────────────────────────

# ─── Presensi (Attendance) Endpoints ──────────────────────────────────────────

@app.get("/api/admin/presensi")
async def get_presensi(
    tanggal: Optional[str] = None,
    karyawan_id: Optional[int] = None,
    token: str = Depends(verify_admin_token)
):
    """Get attendance records. Default: today."""
    try:
        from datetime import date
        target_date = tanggal or date.today().isoformat()
        with get_db_dict() as conn:
            cursor = conn.cursor()
            if karyawan_id:
                cursor.execute("""
                    SELECT p.*, k.nama, k.posisi FROM presensi p
                    JOIN karyawan k ON k.id = p.karyawan_id
                    WHERE p.karyawan_id = ? ORDER BY p.tanggal DESC LIMIT 30
                """, (karyawan_id,))
            else:
                cursor.execute("""
                    SELECT p.*, k.nama, k.posisi FROM presensi p
                    JOIN karyawan k ON k.id = p.karyawan_id
                    WHERE p.tanggal = ? ORDER BY k.nama
                """, (target_date,))
            rows = cursor.fetchall()
        return {"success": True, "tanggal": target_date, "data": rows, "total": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/presensi/rekap")
async def get_presensi_rekap(
    bulan: Optional[str] = None,
    token: str = Depends(verify_admin_token)
):
    """Monthly attendance summary. bulan format: YYYY-MM. Default: current month."""
    try:
        from datetime import date
        bulan_target = bulan or date.today().strftime("%Y-%m")
        with get_db_dict() as conn:
            cursor = conn.cursor()
            # Per-karyawan summary
            cursor.execute("""
                SELECT k.id, k.nama, k.posisi,
                    SUM(CASE WHEN p.tipe='hadir' THEN 1 ELSE 0 END) as hadir,
                    SUM(CASE WHEN p.tipe='izin' THEN 1 ELSE 0 END) as izin,
                    SUM(CASE WHEN p.tipe='sakit' THEN 1 ELSE 0 END) as sakit,
                    SUM(CASE WHEN p.tipe='alpha' THEN 1 ELSE 0 END) as alpha,
                    COUNT(p.id) as total_hari
                FROM karyawan k
                LEFT JOIN presensi p ON p.karyawan_id = k.id
                    AND strftime('%Y-%m', p.tanggal) = ?
                WHERE k.status_kerja = 'aktif'
                GROUP BY k.id ORDER BY k.nama
            """, (bulan_target,))
            rekap = cursor.fetchall()
        return {"success": True, "bulan": bulan_target, "data": rekap}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/presensi")
async def create_presensi_manual(
    karyawan_id: int,
    tanggal: str,
    tipe: str,
    jam_masuk: Optional[str] = None,
    jam_keluar: Optional[str] = None,
    catatan: Optional[str] = None,
    token: str = Depends(verify_admin_token)
):
    """Manually add attendance record."""
    try:
        valid_types = ["hadir", "izin", "sakit", "alpha"]
        if tipe not in valid_types:
            raise HTTPException(status_code=400, detail=f"Invalid tipe: {tipe}")
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, nama FROM karyawan WHERE id=?", (karyawan_id,))
            k = cursor.fetchone()
            if not k:
                raise HTTPException(status_code=404, detail="Karyawan not found")
            conn.execute("""
                INSERT INTO presensi (karyawan_id, tanggal, jam_masuk, jam_keluar, tipe, catatan, sumber)
                VALUES (?,?,?,?,?,?,?)
            """, (karyawan_id, tanggal, jam_masuk, jam_keluar, tipe, catatan, "manual"))
            conn.commit()
        return {"success": True, "message": f"Presensi {k['nama']} tanggal {tanggal} tercatat"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── End Presensi ──────────────────────────────────────────────────────────────

@app.get("/api/admin/feedback", response_model=List[FeedbackResponse])
async def get_all_feedback(token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM feedback 
                ORDER BY created_at DESC
            """)
            feedback_list = cursor.fetchall()
            
        return feedback_list
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/feedback/stats", response_model=FeedbackStats)
async def get_feedback_statistics(token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            
            # Total feedback
            cursor.execute("SELECT COUNT(*) as total FROM feedback")
            total_feedback = cursor.fetchone()['total']
            
            # Average rating
            cursor.execute("SELECT AVG(rating) as avg_rating FROM feedback")
            avg_rating = cursor.fetchone()['avg_rating'] or 0
            
            # Rating distribution
            cursor.execute("""
                SELECT rating, COUNT(*) as count 
                FROM feedback 
                GROUP BY rating 
                ORDER BY rating
            """)
            rating_dist = {str(row['rating']): row['count'] for row in cursor.fetchall()}
            
            # Feedback per layanan
            cursor.execute("""
                SELECT layanan, COUNT(*) as count 
                FROM feedback 
                WHERE layanan IS NOT NULL 
                GROUP BY layanan
            """)
            layanan_stats = {row['layanan']: row['count'] for row in cursor.fetchall()}
            
        return FeedbackStats(
            total_feedback=total_feedback,
            average_rating=round(avg_rating, 1),
            rating_distribution=rating_dist,
            feedback_per_layanan=layanan_stats
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/export/lamaran")
async def export_applications_csv(token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, nama, whatsapp, domisili, posisi, pengalaman, status, created_at
                FROM lamaran 
                ORDER BY created_at DESC
            """)
            applications = cursor.fetchall()
        
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['id', 'nama', 'whatsapp', 'domisili', 'posisi', 'pengalaman', 'status', 'created_at'])
        writer.writeheader()
        writer.writerows(applications)
        
        output.seek(0)
        filename = f"lamaran_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8')),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/export/feedback")
async def export_feedback_csv(token: str = Depends(verify_admin_token)):
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, nama, whatsapp, rating, layanan, komentar, created_at
                FROM feedback 
                ORDER BY created_at DESC
            """)
            feedback_list = cursor.fetchall()
        
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['id', 'nama', 'whatsapp', 'rating', 'layanan', 'komentar', 'created_at'])
        writer.writeheader()
        writer.writerows(feedback_list)
        
        output.seek(0)
        filename = f"feedback_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8')),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/cv/{filename}")
async def download_cv(filename: str, token: str = Depends(verify_admin_token)):
    file_path = os.path.join(CV_UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="CV file not found")
    
    return FileResponse(file_path, filename=filename)


@app.get("/api/admin/feedback-foto/{filename}")
async def get_feedback_foto(filename: str, token: str = Depends(verify_admin_token)):
    filepath = f"uploads/feedback/{filename}"
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath)


# --- Notification endpoints ---

@app.get("/api/admin/notifications/pending")
async def get_pending_notifications(token: str = Depends(verify_admin_token)):
    """Get all un-notified lamaran and feedback"""
    try:
        with get_db_dict() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, nama, whatsapp, posisi, domisili, created_at 
                FROM lamaran WHERE notified = 0 ORDER BY created_at DESC
            """)
            new_lamaran = cursor.fetchall()
            cursor.execute("""
                SELECT id, nama, rating, layanan, komentar, nomor_nota, created_at 
                FROM feedback WHERE notified = 0 ORDER BY created_at DESC
            """)
            new_feedback = cursor.fetchall()
        return {"lamaran": new_lamaran, "feedback": new_feedback, "total": len(new_lamaran) + len(new_feedback)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/notifications/mark-sent")
async def mark_notifications_sent(token: str = Depends(verify_admin_token)):
    """Mark all pending notifications as sent"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE lamaran SET notified = 1 WHERE notified = 0")
            lc = cursor.rowcount
            cursor.execute("UPDATE feedback SET notified = 1 WHERE notified = 0")
            fc = cursor.rowcount
            conn.commit()
        return {"success": True, "marked": {"lamaran": lc, "feedback": fc}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"message": "SIJI Bintaro API is running!", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)