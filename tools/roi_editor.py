"""
ROI Editor — VisionAPISmart
Micro web-tool per disegnare poligoni ROI su snapshot live delle telecamere.

Avvio:  python tools/roi_editor.py
        python tools/roi_editor.py --port 8001

Apre automaticamente il browser su http://localhost:8001
Click sinistro sul canvas per aggiungere punti.
Tasti: Z = undo, C = clear, S = save, R = refresh snapshot
"""
from __future__ import annotations

import argparse
import base64
import logging
import threading
import webbrowser
from io import BytesIO
from pathlib import Path

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("roi_editor")

# ---------------------------------------------------------------------------
# YAML helpers (standalone – non importa da app/)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
CAMERAS_YAML = ROOT / "cameras.yaml"

# Legge AXIS_DEFAULT_USER / AXIS_DEFAULT_PASS da .env come fallback credentials,
# replicando il comportamento di app/axis.py per le telecamere senza username in cameras.yaml.
def _load_axis_defaults() -> tuple[str, str]:
    env_path = ROOT / ".env"
    _user, _pass = "root", ""
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("AXIS_DEFAULT_USER="):
                _user = line.split("=", 1)[1].strip()
            elif line.startswith("AXIS_DEFAULT_PASS="):
                _pass = line.split("=", 1)[1].strip()
    log.debug("axis default credentials: user=%s", _user)
    return _user, _pass

_AXIS_DEFAULT_USER, _AXIS_DEFAULT_PASS = _load_axis_defaults()


def _load_cameras() -> list[dict]:
    with open(CAMERAS_YAML) as f:
        return yaml.safe_load(f).get("cameras", [])


def _save_cameras(cameras: list[dict]) -> None:
    with open(CAMERAS_YAML, "w") as f:
        yaml.safe_dump(
            {"cameras": cameras},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def _find_camera(camera_id: str) -> dict:
    for c in _load_cameras():
        if c["id"] == camera_id:
            return c
    raise HTTPException(404, f"Camera '{camera_id}' not found")


def _fetch_snapshot_sync(camera: dict) -> tuple[bytes, int, int]:
    cam_id = camera.get("id", "?")
    ch     = camera.get("channel", 1)
    res    = camera.get("resolution", "1280x720")
    comp   = camera.get("compression", 30)
    rot    = camera.get("rotation", 0)
    user   = camera.get("username") or _AXIS_DEFAULT_USER
    passwd = camera.get("password") or _AXIS_DEFAULT_PASS
    base   = camera.get("base_url", "")

    url = (
        f"{base}/axis-cgi/jpg/image.cgi"
        f"?camera={ch}&resolution={res}&compression={comp}"
    )
    if rot:
        url += f"&rotation={rot}"

    log.debug("[%s] snapshot URL: %s", cam_id, url)
    log.debug("[%s] auth user: %s (da %s) | channel: %s | resolution: %s | compression: %s | rotation: %s",
              cam_id, user,
              "cameras.yaml" if camera.get("username") else ".env default",
              ch, res, comp, rot)

    try:
        resp = httpx.get(
            url,
            auth=httpx.DigestAuth(user, passwd),
            timeout=5.0,
        )
    except httpx.ConnectTimeout:
        log.error("[%s] CONNECT TIMEOUT dopo 5s — host raggiungibile? URL: %s", cam_id, url)
        raise
    except httpx.ReadTimeout:
        log.error("[%s] READ TIMEOUT — connessione aperta ma nessun dato entro 5s. URL: %s", cam_id, url)
        raise
    except httpx.ConnectError as e:
        log.error("[%s] CONNECT ERROR — %s. URL: %s", cam_id, e, url)
        raise
    except httpx.RequestError as e:
        log.error("[%s] REQUEST ERROR — %s (%s). URL: %s", cam_id, type(e).__name__, e, url)
        raise

    log.debug("[%s] HTTP %s — Content-Type: %s — Content-Length: %s bytes",
              cam_id, resp.status_code,
              resp.headers.get("content-type", "n/d"),
              len(resp.content))

    if resp.status_code == 401:
        log.error("[%s] HTTP 401 Unauthorized — credenziali errate o digest auth fallita. user=%s", cam_id, user)
    elif resp.status_code == 403:
        log.error("[%s] HTTP 403 Forbidden — utente autenticato ma senza permessi snapshot.", cam_id)
    elif resp.status_code >= 400:
        log.error("[%s] HTTP %s — risposta: %s", cam_id, resp.status_code, resp.text[:300])

    resp.raise_for_status()

    ct = resp.headers.get("content-type", "")
    if "image" not in ct:
        log.warning("[%s] Content-Type inatteso: %r (atteso image/jpeg). "
                    "Prime 200 char della risposta: %s", cam_id, ct, resp.text[:200])

    from PIL import Image  # lazy import
    try:
        img = Image.open(BytesIO(resp.content))
        w, h = img.size
    except Exception as e:
        log.error("[%s] Impossibile decodificare l'immagine ricevuta (%d bytes): %s",
                  cam_id, len(resp.content), e)
        raise

    log.info("[%s] snapshot OK — %dx%d px — %d bytes", cam_id, w, h, len(resp.content))
    return resp.content, w, h


# ---------------------------------------------------------------------------
# FastAPI micro-app
# ---------------------------------------------------------------------------
editor = FastAPI(docs_url=None, redoc_url=None)

_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>ROI Editor — VisionAPISmart</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#1a1a2e; color:#eee; height:100vh;
       display:flex; flex-direction:column; overflow:hidden; }
#topbar { display:flex; align-items:center; gap:10px; padding:8px 14px;
          background:#16213e; border-bottom:1px solid #0f3460; flex-shrink:0; flex-wrap:wrap; }
#topbar h1 { font-size:15px; font-weight:700; color:#e94560; white-space:nowrap; }
select { background:#0f3460; color:#eee; border:1px solid #3a5f9e;
         padding:5px 9px; border-radius:4px; font-size:13px; }
button { padding:5px 13px; border:none; border-radius:4px;
         font-size:12px; cursor:pointer; white-space:nowrap; }
.btn-secondary { background:#0f3460; color:#ccc; }
.btn-success   { background:#27ae60; color:#fff; font-weight:600; }
.btn-danger    { background:#c0392b; color:#fff; }
button:hover   { filter:brightness(1.15); }
button:disabled { opacity:.45; cursor:default; }
#canvas-wrap { flex:1; overflow:auto; display:flex;
               align-items:flex-start; justify-content:center; padding:10px; }
canvas { cursor:crosshair; display:block; }
#bottombar { display:flex; align-items:center; gap:8px; padding:6px 14px;
             background:#16213e; border-top:1px solid #0f3460;
             flex-shrink:0; font-size:12px; }
#status { flex:1; color:#aaa; }
#coords { color:#4fc3f7; min-width:160px; font-family:monospace; }
#toast { position:fixed; top:58px; left:50%; transform:translateX(-50%);
         padding:9px 20px; border-radius:6px; font-size:13px;
         z-index:999; pointer-events:none; opacity:0; transition:opacity .3s; }
#toast.show { opacity:1; }
#toast.ok  { background:#27ae60; color:#fff; }
#toast.err { background:#c0392b; color:#fff; }
#overlay { position:fixed; inset:0; background:rgba(0,0,0,.6);
           display:flex; align-items:center; justify-content:center;
           font-size:17px; z-index:500; }
#overlay.hidden { display:none; }
</style>
</head>
<body>
<div id="topbar">
  <h1>ROI Editor</h1>
  <select id="cam-sel" title="Seleziona telecamera">
    <option value="">— seleziona telecamera —</option>
  </select>
  <button class="btn-secondary" id="btn-refresh" onclick="refreshSnap()" title="R">↻ Refresh</button>
  <span style="color:#333">|</span>
  <button class="btn-secondary" onclick="undo()" title="Z">↩ Undo</button>
  <button class="btn-secondary" onclick="clearPts()" title="C">✕ Clear</button>
  <span style="color:#333">|</span>
  <button class="btn-success"  onclick="saveROI()" title="S">💾 Save ROI</button>
  <button class="btn-danger"   onclick="removeROI()">🗑 Remove ROI</button>
  <span style="color:#666;font-size:11px">Scorciatoie: Z undo · C clear · S save · R refresh</span>
</div>

<div id="canvas-wrap">
  <canvas id="canvas"></canvas>
</div>

<div id="bottombar">
  <div id="status">Seleziona una telecamera per iniziare.</div>
  <div id="coords">x: — &nbsp; y: —</div>
</div>

<div id="overlay">⏳ Caricamento...</div>
<div id="toast"></div>

<script>
const canvas  = document.getElementById('canvas');
const ctx     = canvas.getContext('2d');
const overlay = document.getElementById('overlay');

let pts = [];        // [[origX, origY], ...]
let imgEl = null;
let scaleX = 1, scaleY = 1;
let origW = 0, origH = 0;
let camId = null;

// ── load camera list ──────────────────────────────────────────────────────
async function loadCameraList() {
  const res = await fetch('/api/cameras');
  const cams = await res.json();
  const sel = document.getElementById('cam-sel');
  cams.forEach(c => {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = `${c.id} — ${c.label}${c.has_roi ? '  ✓ ROI' : ''}`;
    sel.appendChild(o);
  });
  const qCam = new URLSearchParams(location.search).get('camera');
  if (qCam) { sel.value = qCam; sel.dispatchEvent(new Event('change')); }
  overlay.classList.add('hidden');
}

document.getElementById('cam-sel').addEventListener('change', e => {
  if (e.target.value) loadCamera(e.target.value);
});

// ── load camera snapshot + existing ROI ───────────────────────────────────
async function loadCamera(id) {
  camId = id;
  history.replaceState(null, '', `?camera=${id}`);
  overlay.classList.remove('hidden');
  try {
    const [sRes, rRes] = await Promise.all([
      fetch(`/api/snapshot/${id}`),
      fetch(`/api/roi/${id}`)
    ]);
    if (!sRes.ok) throw new Error(`Snapshot error ${sRes.status}`);
    const { b64, width, height } = await sRes.json();
    pts = rRes.ok ? ((await rRes.json()).points || []) : [];
    initCanvas(b64, width, height);
  } catch(e) {
    toast(e.message, 'err');
  } finally {
    overlay.classList.add('hidden');
  }
}

async function refreshSnap() {
  if (!camId) return;
  overlay.classList.remove('hidden');
  try {
    const r = await fetch(`/api/snapshot/${camId}`);
    if (!r.ok) throw new Error(`Snapshot error ${r.status}`);
    const { b64, width, height } = await r.json();
    initCanvas(b64, width, height);
  } catch(e) {
    toast(e.message, 'err');
  } finally {
    overlay.classList.add('hidden');
  }
}

// ── canvas setup ──────────────────────────────────────────────────────────
function initCanvas(b64, w, h) {
  origW = w; origH = h;
  const wrap = document.getElementById('canvas-wrap');
  const maxW = wrap.clientWidth  - 20;
  const maxH = wrap.clientHeight - 20;
  const scale = Math.min(maxW / w, maxH / h, 1.0);
  canvas.width  = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  scaleX = canvas.width  / w;
  scaleY = canvas.height / h;

  imgEl = new Image();
  imgEl.onload = draw;
  imgEl.src = `data:image/jpeg;base64,${b64}`;
}

// ── drawing ───────────────────────────────────────────────────────────────
function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (imgEl) ctx.drawImage(imgEl, 0, 0, canvas.width, canvas.height);

  if (pts.length >= 2) {
    ctx.beginPath();
    ctx.moveTo(pts[0][0] * scaleX, pts[0][1] * scaleY);
    for (let i = 1; i < pts.length; i++)
      ctx.lineTo(pts[i][0] * scaleX, pts[i][1] * scaleY);
    if (pts.length >= 3) ctx.closePath();
    ctx.fillStyle   = 'rgba(0,220,100,.13)';
    ctx.fill();
    ctx.strokeStyle = '#00DC64';
    ctx.lineWidth   = 2;
    ctx.stroke();
  }

  pts.forEach(([ox, oy], i) => {
    const px = ox * scaleX, py = oy * scaleY;
    ctx.beginPath(); ctx.arc(px, py, 6, 0, Math.PI * 2);
    ctx.fillStyle = '#FF4444'; ctx.fill();
    ctx.strokeStyle = 'white'; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = 'white';
    ctx.font = 'bold 11px -apple-system,sans-serif';
    ctx.fillText(i + 1, px + 8, py - 4);
  });

  updateStatus();
}

// ── mouse ─────────────────────────────────────────────────────────────────
canvas.addEventListener('click', e => {
  if (!camId) return;
  const r = canvas.getBoundingClientRect();
  const cx = (e.clientX - r.left) * (canvas.width  / r.width);
  const cy = (e.clientY - r.top)  * (canvas.height / r.height);
  pts.push([Math.round(cx / scaleX), Math.round(cy / scaleY)]);
  draw();
});

canvas.addEventListener('mousemove', e => {
  const r = canvas.getBoundingClientRect();
  const cx = (e.clientX - r.left) * (canvas.width  / r.width);
  const cy = (e.clientY - r.top)  * (canvas.height / r.height);
  document.getElementById('coords').textContent =
    `x: ${Math.round(cx / scaleX).toString().padStart(4)}   y: ${Math.round(cy / scaleY).toString().padStart(4)}`;
});

// ── actions ───────────────────────────────────────────────────────────────
function undo()    { if (pts.length) { pts.pop(); draw(); } }
function clearPts(){ pts = []; draw(); }

async function saveROI() {
  if (!camId)           return toast('Nessuna telecamera selezionata.', 'err');
  if (pts.length < 3)   return toast('Servono almeno 3 punti per un ROI valido.', 'err');
  const r = await fetch(`/api/roi/${camId}`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ points: pts })
  });
  if (r.ok) { toast(`ROI salvato (${pts.length} punti)`, 'ok'); refreshCamList(); }
  else      { toast(`Errore salvataggio: ${r.status}`, 'err'); }
}

async function removeROI() {
  if (!camId) return;
  if (!confirm('Rimuovere il ROI da questa telecamera?')) return;
  const r = await fetch(`/api/roi/${camId}`, { method: 'DELETE' });
  if (r.ok) { pts = []; draw(); toast('ROI rimosso.', 'ok'); refreshCamList(); }
  else      { toast(`Errore: ${r.status}`, 'err'); }
}

async function refreshCamList() {
  const r    = await fetch('/api/cameras');
  const cams = await r.json();
  const sel  = document.getElementById('cam-sel');
  const cur  = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  cams.forEach(c => {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = `${c.id} — ${c.label}${c.has_roi ? '  ✓ ROI' : ''}`;
    sel.appendChild(o);
  });
  sel.value = cur;
}

function updateStatus() {
  document.getElementById('status').textContent = camId
    ? `${camId} — ${pts.length} punti${pts.length >= 3 ? ' ✓ ROI valido' : ' (min. 3 punti)'}  |  immagine originale: ${origW}×${origH} px`
    : 'Seleziona una telecamera.';
}

// ── keyboard shortcuts ────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'SELECT') return;
  const k = e.key.toLowerCase();
  if (k === 'z') undo();
  if (k === 'c') clearPts();
  if (k === 's') saveROI();
  if (k === 'r') refreshSnap();
});

// ── toast ─────────────────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg, type) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = ''; }, 3200);
}

// ── init ──────────────────────────────────────────────────────────────────
loadCameraList();
</script>
</body>
</html>
"""


# ── API endpoints ─────────────────────────────────────────────────────────

@editor.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@editor.get("/api/cameras")
async def api_cameras():
    cameras = _load_cameras()
    return JSONResponse([
        {
            "id": c["id"],
            "label": c["label"],
            "has_roi": bool(c.get("roi") and len(c["roi"]) >= 3),
        }
        for c in cameras
    ])


@editor.get("/api/snapshot/{camera_id}")
async def api_snapshot(camera_id: str):
    log.info("[%s] richiesta snapshot", camera_id)
    camera = _find_camera(camera_id)
    log.debug("[%s] config camera: base_url=%s label=%s",
              camera_id, camera.get("base_url"), camera.get("label"))
    try:
        raw, w, h = _fetch_snapshot_sync(camera)
    except httpx.TimeoutException as e:
        log.error("[%s] timeout: %s", camera_id, e)
        raise HTTPException(502, f"Timeout connecting to camera '{camera_id}'")
    except httpx.HTTPStatusError as e:
        log.error("[%s] HTTP error %s dalla camera", camera_id, e.response.status_code)
        raise HTTPException(502, f"Camera returned HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        log.error("[%s] network error: %s", camera_id, e)
        raise HTTPException(502, f"Camera unreachable: {e}")
    except Exception as e:
        log.exception("[%s] errore imprevisto nel fetch snapshot: %s", camera_id, e)
        raise HTTPException(500, f"Internal error: {e}")
    return JSONResponse({"b64": base64.b64encode(raw).decode(), "width": w, "height": h})


@editor.get("/api/roi/{camera_id}")
async def api_get_roi(camera_id: str):
    camera = _find_camera(camera_id)
    roi = camera.get("roi") or []
    return JSONResponse({"points": roi})


class RoiPayload(BaseModel):
    points: list[list[int]]


@editor.post("/api/roi/{camera_id}")
async def api_save_roi(camera_id: str, body: RoiPayload):
    if len(body.points) < 3:
        raise HTTPException(400, "At least 3 points required")
    cameras = _load_cameras()
    found = False
    for c in cameras:
        if c["id"] == camera_id:
            c["roi"] = body.points
            found = True
            break
    if not found:
        raise HTTPException(404, f"Camera '{camera_id}' not found")
    _save_cameras(cameras)
    return JSONResponse({"saved": True, "points": len(body.points)})


@editor.delete("/api/roi/{camera_id}")
async def api_remove_roi(camera_id: str):
    cameras = _load_cameras()
    found = False
    for c in cameras:
        if c["id"] == camera_id:
            c.pop("roi", None)
            found = True
            break
    if not found:
        raise HTTPException(404, f"Camera '{camera_id}' not found")
    _save_cameras(cameras)
    return JSONResponse({"removed": True})


# ── entrypoint ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ROI Editor — VisionAPISmart")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"ROI Editor → {url}")
    uvicorn.run(editor, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
