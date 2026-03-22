"""
media_upload_tool_api.py — Upload tool API untuk SIJI media management
Token: siji-media-2026
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse
import os, shutil, subprocess
from pathlib import Path

router = APIRouter()

UPLOAD_TOKEN = "siji-media-2026"
IMAGES_DIR = Path("/var/www/sijibintaro/images")
ALLOWED_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "video/mp4": ".mp4", "video/quicktime": ".mov",
    "video/webm": ".webm"
}
CATEGORY_MAP = {
    "service-1": "service-1.png",
    "service-2": "service-2.png",
    "service-3": "service-3.png",
    "service-4": "service-4.png",
    "service-5": "service-5.png",
    "google-place-photo": "google-place-photo.jpg",
}
MAX_SIZE = 100 * 1024 * 1024  # 100MB


def check_token(token: str):
    if token != UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token tidak valid")


@router.post("/api/media-upload")
async def upload_media(
    file: UploadFile = File(...),
    token: str = Form(...),
    category: str = Form(default=""),
    custom_name: str = Form(default=""),
):
    check_token(token)

    # Validasi mime type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"Tipe file tidak didukung: {content_type}")

    # Baca file
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File terlalu besar (max 100MB)")

    ext = ALLOWED_MIME[content_type]

    # Tentukan nama file
    if category and category in CATEGORY_MAP:
        filename = CATEGORY_MAP[category]
        # Pastikan ekstensi sesuai tipe file
        base = filename.rsplit(".", 1)[0]
        if content_type.startswith("video/"):
            filename = base + ext
        elif content_type.startswith("image/"):
            # Keep original extension for service-x.png
            pass
    elif custom_name:
        # Sanitize nama
        safe_name = "".join(c for c in custom_name if c.isalnum() or c in "-_")
        filename = safe_name + ext
    else:
        # Pakai nama asli, sanitize
        original = file.filename or "upload"
        base = Path(original).stem
        safe_base = "".join(c for c in base if c.isalnum() or c in "-_")
        filename = safe_base + ext

    save_path = IMAGES_DIR / filename

    # Backup jika sudah ada
    if save_path.exists():
        bak = save_path.with_suffix(save_path.suffix + ".bak")
        shutil.copy2(save_path, bak)

    # Simpan file
    with open(save_path, "wb") as f:
        f.write(content)

    # Git commit otomatis
    try:
        subprocess.run(
            ["git", "add", f"images/{filename}"],
            cwd="/var/www/sijibintaro", capture_output=True, timeout=10
        )
        subprocess.run(
            ["git", "commit", "-m", f"media: upload {filename} via web tool"],
            cwd="/var/www/sijibintaro", capture_output=True, timeout=15
        )
        subprocess.run(
            ["git", "push"],
            cwd="/var/www/sijibintaro", capture_output=True, timeout=30
        )
        git_pushed = True
    except Exception:
        git_pushed = False

    size_kb = len(content) // 1024
    return {
        "ok": True,
        "saved_as": filename,
        "size_kb": size_kb,
        "git_pushed": git_pushed,
        "url": f"https://sijibintaro.id/images/{filename}"
    }


@router.get("/api/media-list")
async def list_media(token: str = Query(...)):
    check_token(token)

    files = []
    for f in sorted(IMAGES_DIR.iterdir()):
        if f.is_file() and not f.name.startswith(".") and not f.suffix in [".bak", ""]:
            size = f.stat().st_size
            if size > 1024 * 1024:
                size_str = f"{size / 1024 / 1024:.1f} MB"
            else:
                size_str = f"{size // 1024} KB"
            files.append({
                "name": f.name,
                "size": size_str,
                "url": f"https://sijibintaro.id/images/{f.name}"
            })
    return {"files": files, "count": len(files)}
