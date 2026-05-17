"""
video_render_api.py — Video render pipeline untuk SIJI
Trim → Merge → Polish (ffmpeg) → Crop 9:16 atau 1:1 → Bumper → Output
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os, subprocess, tempfile, shutil, json, time
from pathlib import Path

router = APIRouter()

UPLOAD_TOKEN = "siji-media-2026"
IMAGES_DIR   = Path("/opt/sijibintaro/images")
RENDERED_DIR = Path("/opt/sijibintaro/images/rendered")
RENDERED_DIR.mkdir(exist_ok=True)

# ── Models ────────────────────────────────────────────────────────────
class ClipSpec(BaseModel):
    name: str
    trim_start: float = 0
    trim_end: Optional[float] = None

class PolishSpec(BaseModel):
    brightness: float = 0.05
    contrast: float   = 1.05
    saturation: float = 1.10
    warmth: int       = 8
    sharpen: float    = 1.0

class BumperSpec(BaseModel):
    intro_mode: str    = "none"   # none | text | video
    intro_text: str    = "SIJI Bintaro"
    intro_sub: str     = "Premium Laundry & Care"
    closing_video: str = ""
    watermark: str     = "none"   # none | text | logo

class RenderRequest(BaseModel):
    token: str
    clips: list[ClipSpec]
    format: str       = "9:16"   # 9:16 | 1:1
    resolution: int   = 1080
    polish: PolishSpec = PolishSpec()
    bumper: BumperSpec = BumperSpec()
    output_name: str  = ""
    caption: str      = ""

# ── Helpers ────────────────────────────────────────────────────────────
def build_scale_crop(fmt: str, res: int) -> str:
    """Build ffmpeg vf filter for crop + scale."""
    if fmt == "9:16":
        w, h = res * 9 // 16, res
        # Scale to cover, then crop center
        return (
            f"scale='if(gt(iw/ih,{w}/{h}),{h}*iw/ih,-2)':'if(gt(iw/ih,{w}/{h}),-2,{h})',"
            f"crop={w}:{h}"
        )
    else:  # 1:1
        side = res
        return (
            f"scale='if(gt(iw,ih),{side}*iw/ih,-2)':'if(gt(iw,ih),-2,{side}*iw/ih)',"
            f"crop={side}:{side}"
        )

def build_polish_filter(p: PolishSpec, scale_crop: str) -> str:
    """Build eq + unsharp filter chain."""
    warmth_r = 1 + p.warmth * 0.003
    warmth_b = 1 - p.warmth * 0.002
    filters = [
        scale_crop,
        f"eq=brightness={p.brightness}:contrast={p.contrast}:saturation={p.saturation}",
        f"colorchannelmixer=rr={warmth_r:.3f}:bb={warmth_b:.3f}",
    ]
    if p.sharpen > 0:
        lx = min(5, max(3, int(p.sharpen * 1.5) * 2 + 1))
        filters.append(f"unsharp={lx}:{lx}:{p.sharpen * 0.3:.2f}:5:5:0.0")
    return ",".join(filters)

def run_ffmpeg(cmd: list, timeout: int = 180) -> tuple[bool, str]:
    """Run ffmpeg command, return (success, stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode == 0, result.stderr[-2000:] if result.stderr else ""

def make_text_bumper(text: str, sub: str, fmt: str, res: int, output: Path) -> bool:
    """Generate 2-second text bumper card via ffmpeg."""
    w = res * 9 // 16 if fmt == "9:16" else res
    h = res
    font_size = res // 15
    sub_size  = res // 25
    # Dark navy background with gold text
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x1a1a2e:size={w}x{h}:rate=30",
        "-vf", (
            f"drawtext=text='{text}':fontcolor=0xc8a96e:fontsize={font_size}:"
            f"x=(w-text_w)/2:y=(h/2-text_h),"
            f"drawtext=text='{sub}':fontcolor=0xffffff@0.6:fontsize={sub_size}:"
            f"x=(w-text_w)/2:y=(h/2+text_h/2+20)"
        ),
        "-t", "2", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        str(output)
    ]
    ok, _ = run_ffmpeg(cmd, 30)
    return ok

# ── Main Render ────────────────────────────────────────────────────────
@router.post("/api/video-render")
async def video_render(req: RenderRequest):
    if req.token != UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="Token tidak valid")
    if not req.clips:
        raise HTTPException(status_code=400, detail="Minimal 1 clip")

    fmt = req.format
    res = req.resolution
    scale_crop = build_scale_crop(fmt, res)
    polish_vf  = build_polish_filter(req.polish, scale_crop)

    output_name = (req.output_name or f"siji-reel-{int(time.time())}") + ".mp4"
    output_name = "".join(c for c in output_name if c.isalnum() or c in "-_.")
    if not output_name.endswith(".mp4"):
        output_name += ".mp4"

    tmpdir = Path(tempfile.mkdtemp(prefix="siji_render_"))

    try:
        # ── 1. Process each clip (trim + polish + scale/crop) ──────────────
        processed = []
        for i, clip in enumerate(req.clips):
            src = IMAGES_DIR / clip.name
            if not src.exists():
                raise HTTPException(status_code=404, detail=f"Clip not found: {clip.name}")

            out = tmpdir / f"clip_{i:02d}.mp4"
            cmd = ["ffmpeg", "-y"]
            if clip.trim_start > 0:
                cmd += ["-ss", str(clip.trim_start)]
            cmd += ["-i", str(src)]
            if clip.trim_end:
                dur = clip.trim_end - clip.trim_start
                cmd += ["-t", str(max(0.1, dur))]
            cmd += [
                "-vf", polish_vf,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(out)
            ]
            ok, err = run_ffmpeg(cmd, 120)
            if not ok:
                raise HTTPException(status_code=500, detail=f"Error processing {clip.name}: {err[-500:]}")
            processed.append(out)

        # ── 2. Add text bumper if requested ────────────────────────────────
        if req.bumper.intro_mode == "text":
            bumper_out = tmpdir / "bumper.mp4"
            make_text_bumper(req.bumper.intro_text, req.bumper.intro_sub, fmt, res, bumper_out)
            if bumper_out.exists():
                processed.insert(0, bumper_out)

        # ── 3. Add closing video if provided ───────────────────────────────
        if req.bumper.intro_mode == "video" and req.bumper.closing_video:
            closing_src = IMAGES_DIR / req.bumper.closing_video
            if closing_src.exists():
                closing_out = tmpdir / "closing.mp4"
                w = res * 9 // 16 if fmt == "9:16" else res
                h = res
                cmd = [
                    "ffmpeg", "-y", "-i", str(closing_src),
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    str(closing_out)
                ]
                ok, _ = run_ffmpeg(cmd, 60)
                if ok:
                    processed.append(closing_out)

        # ── 4. Merge all clips ────────────────────────────────────────────
        if len(processed) == 1:
            merged = processed[0]
        else:
            concat_list = tmpdir / "concat.txt"
            with open(concat_list, "w") as f:
                for p in processed:
                    f.write(f"file '{p}'\n")
            merged = tmpdir / "merged.mp4"
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(merged)
            ]
            ok, err = run_ffmpeg(cmd, 120)
            if not ok:
                raise HTTPException(status_code=500, detail=f"Merge error: {err[-500:]}")

        # ── 5. Add watermark if requested ────────────────────────────────
        if req.bumper.watermark in ("text", "logo"):
            wm_out = tmpdir / "watermarked.mp4"
            w = res * 9 // 16 if fmt == "9:16" else res
            wm_vf = (
                f"drawtext=text='@siji.bintaro':fontcolor=white@0.7:"
                f"fontsize={res//40}:x=w-text_w-20:y=h-text_h-20"
            )
            cmd = [
                "ffmpeg", "-y", "-i", str(merged),
                "-vf", wm_vf,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy", str(wm_out)
            ]
            ok, _ = run_ffmpeg(cmd, 120)
            if ok:
                merged = wm_out

        # ── 6. Save final output ──────────────────────────────────────────
        final_out = RENDERED_DIR / output_name
        shutil.copy2(merged, final_out)

        # Get stats
        size_mb = round(final_out.stat().st_size / 1024 / 1024, 1)
        dur_raw = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(final_out)],
            capture_output=True, text=True
        ).stdout.strip()
        duration_s = round(float(dur_raw or 0), 1)

        # Save caption alongside
        if req.caption:
            (RENDERED_DIR / output_name.replace(".mp4", ".txt")).write_text(req.caption, encoding="utf-8")

        return {
            "ok": True,
            "output_file": output_name,
            "size_mb": size_mb,
            "duration_s": duration_s,
            "format": fmt,
            "resolution": res,
            "url": f"https://sijibintaro.id/images/rendered/{output_name}"
        }

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@router.get("/api/rendered-list")
async def rendered_list(token: str):
    if token != UPLOAD_TOKEN:
        raise HTTPException(status_code=401)
    files = []
    for f in sorted(RENDERED_DIR.iterdir()):
        if f.suffix == ".mp4":
            size = f.stat().st_size
            files.append({
                "name": f.name,
                "size": f"{size/1024/1024:.1f} MB",
                "url": f"https://sijibintaro.id/images/rendered/{f.name}"
            })
    return {"files": files, "count": len(files)}


@router.post("/api/buffer-post")
async def buffer_post(data: dict):
    if data.get("token") != UPLOAD_TOKEN:
        raise HTTPException(status_code=401)

    # Placeholder — nanti connect ke Buffer API setelah token di-refresh
    return {
        "ok": False,
        "detail": "Buffer token perlu di-refresh. Buka buffer.com → Settings → API Key."
    }
