from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from app.api.frame_store import FrameStore

_HTML = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>GasVision — Camera Viewer</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #111; color: #e0e0e0; font-family: 'Courier New', monospace; height: 100vh; display: flex; flex-direction: column; }
  header { background: #1c1c1c; border-bottom: 1px solid #333; padding: 12px 20px; display: flex; align-items: center; gap: 16px; flex-shrink: 0; }
  header h1 { font-size: 16px; letter-spacing: 2px; color: #4af; text-transform: uppercase; }
  #cameraSelect { background: #252525; color: #e0e0e0; border: 1px solid #444; padding: 6px 10px; font-size: 13px; font-family: inherit; cursor: pointer; border-radius: 3px; min-width: 160px; }
  .btn { background: #252525; color: #aaa; border: 1px solid #444; padding: 6px 12px; font-size: 12px; font-family: inherit; cursor: pointer; border-radius: 3px; }
  .btn:hover { background: #333; color: #fff; }
  .btn.active { background: #1a3a5c; border-color: #4af; color: #4af; }
  #status { margin-left: auto; font-size: 11px; color: #666; }
  main { flex: 1; display: flex; overflow: hidden; }
  #streamWrap { flex: 1; display: flex; align-items: center; justify-content: center; background: #0a0a0a; overflow: hidden; }
  #streamImg { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
  #noFrame { color: #444; font-size: 14px; }
  #camBar { width: 180px; background: #161616; border-left: 1px solid #2a2a2a; overflow-y: auto; padding: 10px 8px; flex-shrink: 0; }
  #camBar h2 { font-size: 10px; letter-spacing: 1px; color: #555; text-transform: uppercase; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid #2a2a2a; }
  .cam-btn { display: block; width: 100%; text-align: left; background: transparent; color: #888; border: 1px solid transparent; padding: 7px 10px; font-size: 12px; font-family: inherit; cursor: pointer; border-radius: 3px; margin-bottom: 4px; }
  .cam-btn:hover { background: #222; color: #ccc; }
  .cam-btn.active { background: #1a3a5c; border-color: #4af; color: #4af; }
</style>
</head>
<body>
<header>
  <h1>GasVision</h1>
  <select id="cameraSelect" onchange="switchCamera(this.value)"></select>
  <button class="btn" onclick="loadCameras()">&#8635; Refresh</button>
  <span id="status">Loading&hellip;</span>
</header>
<main>
  <div id="streamWrap">
    <span id="noFrame">Select a camera</span>
    <img id="streamImg" src="" alt="" style="display:none" onerror="onImgError()" onload="onImgLoad()">
  </div>
  <nav id="camBar">
    <h2>Cameras</h2>
    <div id="camList"></div>
  </nav>
</main>
<script>
let cameras = [];
let current = null;

async function loadCameras() {
  try {
    const r = await fetch('/cameras');
    const d = await r.json();
    cameras = d.cameras;
    rebuildUI();
    document.getElementById('status').textContent = cameras.length + ' camera(s)';
    if (cameras.length && !current) switchCamera(cameras[0]);
  } catch(e) {
    document.getElementById('status').textContent = 'Error loading cameras';
  }
}

function rebuildUI() {
  const sel = document.getElementById('cameraSelect');
  const list = document.getElementById('camList');
  sel.innerHTML = '';
  list.innerHTML = '';
  cameras.forEach((cam, i) => {
    const opt = document.createElement('option');
    opt.value = cam; opt.textContent = cam;
    if (cam === current) opt.selected = true;
    sel.appendChild(opt);

    const btn = document.createElement('button');
    btn.className = 'cam-btn' + (cam === current ? ' active' : '');
    btn.textContent = (i + 1 <= 9 ? (i + 1) + '. ' : '   ') + cam;
    btn.dataset.cam = cam;
    btn.onclick = () => switchCamera(cam);
    list.appendChild(btn);
  });
}

function switchCamera(code) {
  if (code === current) return;
  current = code;

  const img = document.getElementById('streamImg');
  const none = document.getElementById('noFrame');
  img.style.display = 'none';
  none.textContent = 'Connecting…';
  none.style.display = '';

  img.src = '/stream/' + encodeURIComponent(code) + '?_=' + Date.now();

  document.querySelectorAll('.cam-btn').forEach(b => b.classList.toggle('active', b.dataset.cam === code));
  const sel = document.getElementById('cameraSelect');
  sel.value = code;
  document.getElementById('status').textContent = 'Viewing: ' + code;
}

function onImgLoad() {
  document.getElementById('streamImg').style.display = '';
  document.getElementById('noFrame').style.display = 'none';
}
function onImgError() {
  document.getElementById('noFrame').textContent = 'Stream unavailable';
}

document.addEventListener('keydown', e => {
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= cameras.length) switchCamera(cameras[n - 1]);
  if ((e.key === 'ArrowRight' || e.key === 'ArrowDown') && current) {
    const i = cameras.indexOf(current);
    if (i < cameras.length - 1) switchCamera(cameras[i + 1]);
  }
  if ((e.key === 'ArrowLeft' || e.key === 'ArrowUp') && current) {
    const i = cameras.indexOf(current);
    if (i > 0) switchCamera(cameras[i - 1]);
  }
});

loadCameras();
setInterval(loadCameras, 10000);
</script>
</body>
</html>
"""


def create_app(store: FrameStore) -> FastAPI:
    app = FastAPI(title="GasVision Camera Viewer", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return _HTML

    @app.get("/cameras")
    async def list_cameras():
        return {"cameras": store.cameras()}

    @app.get("/snapshot/{camera_code}")
    async def snapshot(camera_code: str):
        frame = store.get(camera_code)
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame available")
        return Response(content=frame, media_type="image/jpeg")

    @app.get("/stream/{camera_code}")
    async def stream(camera_code: str):
        async def generate():
            while True:
                frame = store.get(camera_code)
                if frame is not None:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(
            generate(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    return app
