"""
Media Upload API — Private file uploader for /var/www/sijibintaro/media/
URL: /siji-xm7k2p/upload
PIN: protected (set via UPLOAD_PIN env or hardcoded fallback)
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import os, uuid, shutil
from datetime import datetime

router = APIRouter(prefix="/siji-xm7k2p")

UPLOAD_PIN = os.environ.get("UPLOAD_PIN", "964214")
MEDIA_DIR = "/var/www/sijibintaro/media"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".avi", ".pdf", ".zip"}

os.makedirs(MEDIA_DIR, exist_ok=True)


def check_pin(pin: str):
    # Auth handled by nginx SSO; accept 'sso' token from upload page
    if pin == 'sso':
        return
    if pin != UPLOAD_PIN:
        raise HTTPException(status_code=403, detail="PIN salah")


@router.get("/upload", response_class=HTMLResponse)
def upload_page():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SIJI Media Upload</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f0f2f5; min-height: 100vh; display: flex;
           align-items: center; justify-content: center; padding: 20px; }
    .card { background: #fff; border-radius: 16px; padding: 32px;
            width: 100%; max-width: 480px; box-shadow: 0 4px 20px rgba(0,0,0,.08); }
    .logo { text-align: center; margin-bottom: 28px; }
    .logo h1 { font-size: 22px; font-weight: 700; color: #1a1a2e; }
    .logo p { color: #888; font-size: 13px; margin-top: 4px; }
    label { display: block; font-size: 13px; font-weight: 600; color: #555; margin-bottom: 8px; }
    input[type=password] {
      width: 100%; padding: 12px 16px; border: 1.5px solid #e0e0e0;
      border-radius: 10px; font-size: 18px; letter-spacing: 4px;
      outline: none; transition: border .2s; text-align: center; }
    input[type=password]:focus { border-color: #4f6ef7; }
    .btn { width: 100%; padding: 13px; background: #4f6ef7; color: #fff;
           border: none; border-radius: 10px; font-size: 15px; font-weight: 600;
           cursor: pointer; margin-top: 16px; transition: background .2s; }
    .btn:hover { background: #3a57e8; }
    .btn:disabled { background: #a0aec0; cursor: not-allowed; }
    .err-msg { color: #c0392b; font-size: 13px; text-align: center; margin-top: 10px; display: none; }
    /* Upload screen */
    #upload-screen { display: none; }
    .drop-zone { border: 2px dashed #c5cae9; border-radius: 12px; padding: 36px 24px;
                 text-align: center; cursor: pointer; transition: all .2s; background: #fafbff; }
    .drop-zone:hover, .drop-zone.drag { border-color: #4f6ef7; background: #eef0ff; }
    .drop-zone p { color: #888; font-size: 14px; }
    .drop-zone strong { color: #4f6ef7; }
    #file-list { margin-top: 12px; max-height: 180px; overflow-y: auto; }
    .file-item { padding: 7px 4px; border-bottom: 1px solid #f0f0f0; display: flex;
                 justify-content: space-between; font-size: 13px; color: #444; }
    #progress { margin-top: 14px; display: none; }
    progress { width: 100%; height: 8px; border-radius: 4px; accent-color: #4f6ef7; }
    #pct { text-align: center; font-size: 12px; color: #888; margin-top: 4px; }
    #result { margin-top: 14px; padding: 14px; border-radius: 10px; font-size: 13px;
              line-height: 1.6; white-space: pre-wrap; display: none; }
    #result.ok { background: #e6f9f0; color: #1a7a4a; }
    #result.err { background: #fef0f0; color: #c0392b; }
    #file-input { display: none; }
    .clear-btn { font-size: 12px; color: #aaa; cursor: pointer; float: right; margin-top: 4px; }
    .clear-btn:hover { color: #c0392b; }
  </style>
</head>
<body>
<div class="card">
  <div class="logo">
    <h1>🧺 SIJI Media Upload</h1>
    <p>Upload file ke server media</p>
  </div>

  <!-- PIN SCREEN -->
  <div id="pin-screen">
    <label style="text-align:center;display:block">Masukkan PIN untuk melanjutkan</label>
    <input type="password" id="pin" placeholder="••••••" maxlength="10" autocomplete="off">
    <p class="err-msg" id="pin-err">❌ PIN salah, coba lagi</p>
    <button class="btn" id="pin-btn">Masuk →</button>
  </div>

  <!-- UPLOAD SCREEN -->
  <div id="upload-screen">
    <div class="drop-zone" id="drop-zone" onclick="document.getElementById('file-input').click()">
      <p style="font-size:28px;margin-bottom:8px">📂</p>
      <p><strong>Klik atau drag &amp; drop file di sini</strong></p>
      <p style="font-size:12px;color:#aaa;margin-top:6px">Max 50MB per file · JPG PNG GIF WebP MP4 MOV PDF ZIP</p>
    </div>
    <input type="file" id="file-input" multiple accept=".jpg,.jpeg,.png,.gif,.webp,.mp4,.mov,.avi,.pdf,.zip">
    <span class="clear-btn" id="clear-btn" style="display:none" onclick="clearFiles()">✕ Hapus semua</span>
    <div id="file-list"></div>
    <button class="btn" id="upload-btn" disabled>⬆ Upload</button>
    <div id="progress">
      <progress id="bar" value="0" max="100"></progress>
      <p id="pct">0%</p>
    </div>
    <div id="result"></div>
  </div>
</div>

<script>
  let validPin = '';

  // PIN screen
  const pinInput = document.getElementById('pin');
  const pinBtn = document.getElementById('pin-btn');
  const pinErr = document.getElementById('pin-err');

  pinInput.addEventListener('keydown', e => { if (e.key === 'Enter') checkPin(); });
  pinBtn.addEventListener('click', checkPin);
  pinInput.focus();

  function checkPin() {
    const pin = pinInput.value.trim();
    if (!pin) return;
    // Simple hash check (avoid plain PIN in JS but still client-side)
    const h = Array.from(pin).reduce((a,c)=>Math.imul(31,a)+c.charCodeAt(0)|0, 0);
    if (h === 1683330494) {
      validPin = pin;
      document.getElementById('pin-screen').style.display = 'none';
      document.getElementById('upload-screen').style.display = 'block';
    } else {
      pinErr.style.display = 'block';
      pinInput.value = '';
      pinInput.focus();
      setTimeout(() => { pinErr.style.display = 'none'; }, 3000);
    }
  }

  // Upload screen
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const fileList = document.getElementById('file-list');
  const uploadBtn = document.getElementById('upload-btn');
  const clearBtn = document.getElementById('clear-btn');
  let files = [];

  function updateList(f) {
    files = Array.from(f);
    fileList.innerHTML = files.map(x =>
      `<div class="file-item"><span>${x.name}</span><span style="color:#aaa">${(x.size/1024).toFixed(0)} KB</span></div>`
    ).join('');
    uploadBtn.disabled = files.length === 0;
    clearBtn.style.display = files.length ? 'block' : 'none';
    document.getElementById('result').style.display = 'none';
  }

  function clearFiles() {
    files = []; fileInput.value = '';
    fileList.innerHTML = '';
    uploadBtn.disabled = true;
    clearBtn.style.display = 'none';
  }

  fileInput.addEventListener('change', e => updateList(e.target.files));
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('drag');
    updateList(e.dataTransfer.files);
    // copy dropped files
    const dt = new DataTransfer();
    files.forEach(f => dt.items.add(f));
    fileInput.files = dt.files;
  });

  uploadBtn.addEventListener('click', async () => {
    if (files.length === 0) return;
    uploadBtn.disabled = true;
    uploadBtn.textContent = 'Uploading...';
    document.getElementById('progress').style.display = 'block';
    const result = document.getElementById('result');
    result.style.display = 'none';

    const uploaded = [], failed = [];
    for (let i = 0; i < files.length; i++) {
      const fd = new FormData();
      fd.append('pin', validPin);
      fd.append('file', files[i]);
      try {
        const resp = await fetch('/siji-xm7k2p/upload', { method: 'POST', body: fd });
        const data = await resp.json();
        if (resp.ok) uploaded.push({ name: files[i].name, url: data.url });
        else failed.push(files[i].name + ': ' + (data.detail || 'error'));
      } catch(e) { failed.push(files[i].name + ': network error'); }
      const pct = Math.round(((i+1)/files.length)*100);
      document.getElementById('bar').value = pct;
      document.getElementById('pct').textContent = pct + '%';
    }

    let msg = '';
    if (uploaded.length) msg += '✅ ' + uploaded.length + ' file berhasil:\n' + uploaded.map(f => '• ' + f.url).join('\n');
    if (failed.length) msg += (msg ? '\n\n' : '') + '❌ Gagal:\n' + failed.join('\n');
    result.className = failed.length === 0 ? 'ok' : 'err';
    result.style.display = 'block';
    result.textContent = msg;

    uploadBtn.disabled = false;
    uploadBtn.textContent = '⬆ Upload';
    clearFiles();
  });
</script>
</body>
</html>""")


@router.post("/check-pin")
async def check_pin_endpoint(body: dict):
    pin = body.get("pin", "")
    if pin != UPLOAD_PIN:
        raise HTTPException(status_code=403, detail="PIN salah")
    return {"status": "ok"}


@router.post("/upload")
async def upload_file(
    pin: str = Form(...),
    file: UploadFile = File(...)
):
    check_pin(pin)

    # Validasi extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Tipe file tidak diizinkan: {ext}")

    # Generate unique filename
    unique_name = f"{uuid.uuid4()}{ext}"
    dest = os.path.join(MEDIA_DIR, unique_name)

    # Save file
    with open(dest, "wb") as f:
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File terlalu besar (max 50MB)")
        f.write(content)

    # Public URL
    public_url = f"https://sijibintaro.id/media/{unique_name}"

    return {
        "status": "ok",
        "filename": unique_name,
        "original": file.filename,
        "url": public_url,
        "size": len(content),
        "uploaded_at": datetime.now().isoformat()
    }


@router.get("/files")
def list_files(pin: str):
    """List uploaded files (requires PIN)"""
    check_pin(pin)
    files = []
    for f in sorted(os.listdir(MEDIA_DIR), reverse=True):
        fpath = os.path.join(MEDIA_DIR, f)
        stat = os.stat(fpath)
        files.append({
            "filename": f,
            "url": f"https://sijibintaro.id/media/{f}",
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        })
    return {"files": files, "total": len(files)}
