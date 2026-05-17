"""
media_upload_tool_api.py — Upload tool API untuk SIJI media management
Auto-optimize: Image → WebP (max 1200px, quality 85) | Video → WebP thumbnail + compressed MP4
Token: siji-media-2026
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse
import os, shutil, subprocess, io, tempfile
from pathlib import Path
from PIL import Image, ImageOps

router = APIRouter()

UPLOAD_TOKEN = "siji-media-2026"
IMAGES_DIR = Path("/opt/sijibintaro/images")
ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/quicktime", "video/webm"
}
CATEGORY_MAP = {
    "service-1": "service-1",
    "service-2": "service-2",
    "service-3": "service-3",
    "service-4": "service-4",
    "service-5": "service-5",
    "google-place-photo": "google-place-photo",
}
MAX_SIZE = 100 * 1024 * 1024  # 100MB
IMAGE_MAX_W = 1200
IMAGE_MAX_H = 1200
IMAGE_QUALITY = 85


def check_token(token: str):
    if token != UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token tidak valid")


def optimize_image(content: bytes, original_mime: str) -> tuple[bytes, str]:
    """
    Optimize image: resize (max 1200px), convert to WebP, quality 85.
    Returns (optimized_bytes, '.webp')
    """
    img = Image.open(io.BytesIO(content))

    # Fix EXIF orientation
    img = ImageOps.exif_transpose(img)

    # Convert RGBA/P to RGB for WebP compat (preserve transparency in RGBA)
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    # Resize if larger than max
    w, h = img.size
    if w > IMAGE_MAX_W or h > IMAGE_MAX_H:
        img.thumbnail((IMAGE_MAX_W, IMAGE_MAX_H), Image.LANCZOS)

    # Save as WebP
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=IMAGE_QUALITY, method=6)
    return out.getvalue(), ".webp"


def optimize_video(content: bytes, original_name: str) -> tuple[bytes, str]:
    """
    Optimize video with ffmpeg:
    - Compress to H.264/AAC (web-safe)
    - Scale down to max 1280px width
    - CRF 28 (good quality/size balance)
    Returns (optimized_bytes, '.mp4')
    """
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        tmp_in.write(content)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path.replace(".mp4", "_opt.mp4")

    try:
        result = subprocess.run([
            "ffmpeg", "-y", "-i", tmp_in_path,
            "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease",
            "-c:v", "libx264", "-crf", "28", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",  # Streaming-optimized
            tmp_out_path
        ], capture_output=True, timeout=120)

        if result.returncode == 0 and os.path.exists(tmp_out_path):
            with open(tmp_out_path, "rb") as f:
                return f.read(), ".mp4"
        else:
            # Fallback: return original
            return content, ".mp4"
    except Exception:
        return content, ".mp4"
    finally:
        for p in [tmp_in_path, tmp_out_path]:
            try: os.unlink(p)
            except: pass


def extract_video_thumbnail(video_path: Path) -> Path | None:
    """Extract first good frame from video as WebP thumbnail."""
    thumb_path = video_path.with_suffix(".thumb.webp")
    try:
        result = subprocess.run([
            "ffmpeg", "-y", "-ss", "00:00:01", "-i", str(video_path),
            "-frames:v", "1", "-vf",
            f"scale='min(800,iw)':-2",
            "-c:v", "libwebp", "-quality", "80",
            str(thumb_path)
        ], capture_output=True, timeout=30)
        if result.returncode == 0 and thumb_path.exists():
            return thumb_path
    except Exception:
        pass
    return None


def git_push(filename: str):
    """Git add + commit + push."""
    try:
        subprocess.run(["git", "add", f"images/{filename}"], cwd="/opt/sijibintaro", capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", f"media: upload {filename} via web tool (auto-optimized)"],
                       cwd="/opt/sijibintaro", capture_output=True, timeout=15)
        subprocess.run(["git", "push"], cwd="/opt/sijibintaro", capture_output=True, timeout=30)
        return True
    except Exception:
        return False


@router.post("/api/media-upload")
async def upload_media(
    file: UploadFile = File(...),
    token: str = Form(...),
    category: str = Form(default=""),
    custom_name: str = Form(default=""),
):
    check_token(token)

    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"Tipe file tidak didukung: {content_type}")

    content = await file.read()
    original_size = len(content)
    if original_size > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File terlalu besar (max 100MB)")

    is_video = content_type.startswith("video/")
    is_image = content_type.startswith("image/")

    # ── Optimize ────────────────────────────────────────────────
    optimized_content = content
    final_ext = ".webp" if is_image else ".mp4"

    if is_image:
        try:
            optimized_content, final_ext = optimize_image(content, content_type)
        except Exception as e:
            # Fallback: keep original
            final_ext = {"image/jpeg": ".jpg", "image/png": ".png",
                         "image/webp": ".webp", "image/gif": ".gif"}.get(content_type, ".jpg")

    elif is_video:
        try:
            optimized_content, final_ext = optimize_video(content, file.filename or "video.mp4")
        except Exception:
            final_ext = ".mp4"

    # ── Determine filename ───────────────────────────────────────
    if category and category in CATEGORY_MAP:
        base = CATEGORY_MAP[category]
        filename = base + final_ext
    elif custom_name:
        safe = "".join(c for c in custom_name if c.isalnum() or c in "-_")
        filename = safe + final_ext
    else:
        original = file.filename or "upload"
        base = Path(original).stem
        safe_base = "".join(c for c in base if c.isalnum() or c in "-_")
        filename = safe_base + final_ext

    save_path = IMAGES_DIR / filename

    # Backup if exists
    if save_path.exists():
        shutil.copy2(save_path, save_path.with_suffix(save_path.suffix + ".bak"))

    # Save
    with open(save_path, "wb") as f:
        f.write(optimized_content)

    # Extract thumbnail for videos
    thumb_name = None
    if is_video:
        thumb_path = extract_video_thumbnail(save_path)
        if thumb_path:
            thumb_name = thumb_path.name

    # Git push
    git_pushed = git_push(filename)
    if thumb_name:
        git_push(thumb_name)

    # Stats
    orig_kb = original_size // 1024
    opt_kb = len(optimized_content) // 1024
    savings_pct = round((1 - len(optimized_content) / original_size) * 100, 1) if original_size > 0 else 0

    return {
        "ok": True,
        "saved_as": filename,
        "thumbnail": thumb_name,
        "original_kb": orig_kb,
        "optimized_kb": opt_kb,
        "savings_pct": savings_pct,
        "git_pushed": git_pushed,
        "url": f"https://sijibintaro.id/images/{filename}"
    }


@router.get("/api/media-list")
async def list_media(token: str = Query(...)):
    check_token(token)

    files = []
    for f in sorted(IMAGES_DIR.iterdir()):
        if f.is_file() and not f.name.startswith(".") and f.suffix not in [".bak", ""]:
            size = f.stat().st_size
            size_str = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size // 1024} KB"
            files.append({
                "name": f.name,
                "size": size_str,
                "url": f"https://sijibintaro.id/images/{f.name}"
            })
    return {"files": files, "count": len(files)}
