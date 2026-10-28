"""
OpenVelo Studio: web application backend (FastAPI).

Analysis runs as a background job so the UI can show real progress. Every
finished analysis is saved as a *session* under output/sessions/<id>/ so riders
can reopen past fits and compare them.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from src import coach, engine, fit_profiles as fp, pose_backends as pb, report  # noqa: E402

VERSION = "3.0.0"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
SAMPLES_DIR = os.path.join(ROOT, 'assets', 'examples')
DEMO_DIR = os.path.join(ROOT, 'assets', 'demo')
SESSIONS_DIR = os.path.join(ROOT, 'output', 'sessions')
DEMO_ID = 'demo'

VIDEO_EXT = ('.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv')
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp')
MEDIA_TYPES = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
               '.avi': 'video/x-msvideo', '.mkv': 'video/x-matroska', '.jpg': 'image/jpeg',
               '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}

app = FastAPI(title="OpenVelo Studio", version=VERSION)
os.makedirs(SESSIONS_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _session_dir(sid):
    if sid == DEMO_ID:
        return DEMO_DIR
    if not sid or not all(c.isalnum() or c in '-_' for c in sid):
        raise HTTPException(404, "Fit not found")
    return os.path.join(SESSIONS_DIR, sid)


def _load(sid):
    path = os.path.join(_session_dir(sid), 'result.json')
    if not os.path.exists(path):
        raise HTTPException(404, "Fit not found")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save(sid, data):
    if sid == DEMO_ID:
        return
    path = os.path.join(_session_dir(sid), 'result.json')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    os.replace(tmp, path)


def _media_path(sid, data):
    src = data.get('source') or {}
    if src.get('sample'):
        return os.path.join(SAMPLES_DIR, src['sample'])
    if src.get('file'):
        return os.path.join(_session_dir(sid), src['file'])
    return None


def _summary(data):
    return {
        'id': data['id'], 'created': data.get('created'), 'title': data.get('title'),
        'mode': data.get('mode'), 'profile': data.get('profile'), 'profile_name': data.get('profile_name'),
        'score': data.get('score'), 'rider_name': (data.get('rider') or {}).get('name'),
        'quality': (data.get('quality') or {}).get('grade'),
        'thumb': f"/api/sessions/{data['id']}/snapshots/{next(iter(data.get('snapshots') or {}), 'none')}.jpg",
    }


def _finalize(sid, res, source, title, rider):
    """Write snapshots to disk and turn the engine result into the stored session JSON."""
    sdir = _session_dir(sid)
    os.makedirs(os.path.join(sdir, 'snapshots'), exist_ok=True)
    snaps = {}
    for k, s in (res.get('snapshots') or {}).items():
        if s.get('jpg'):
            with open(os.path.join(sdir, 'snapshots', f'{k}.jpg'), 'wb') as f:
                f.write(s['jpg'])
            snaps[k] = {'title': s['title'], 'url': f'/api/sessions/{sid}/snapshots/{k}.jpg'}
    res['snapshots'] = snaps
    res['rider'] = dict(res.get('rider') or {}, **rider)
    res.update({'id': sid, 'created': _now(), 'title': title, 'source': source, 'version': VERSION,
                'media_url': f'/api/sessions/{sid}/media'})
    res['coach'] = {'text': coach.coach_notes(res['recommendations'], res['profile_name'])}
    return res


# --------------------------------------------------------------------------- #
# Background jobs
# --------------------------------------------------------------------------- #
JOBS = {}
JOBS_LOCK = threading.Lock()
WORKER = threading.Semaphore(1)  # one analysis at a time keeps the machine responsive


def _set_job(jid, **kw):
    with JOBS_LOCK:
        JOBS[jid].update(kw, updated=time.time())


def _run_job(jid, sid, media_path, media_type, params, source, title, rider):
    with WORKER:
        _set_job(jid, status='running', stage='detect', progress=0.02, message='Starting')
        try:
            def cb(frac, msg, stage='track'):
                _set_job(jid, progress=round(float(frac), 3), message=msg, stage=stage)

            if media_type == 'video':
                res = engine.analyze_video(media_path, progress_cb=cb, **params)
            else:
                params = {k: v for k, v in params.items()
                          if k in ('profile_key', 'backend', 'flexibility', 'pains')}
                res = engine.analyze_image(media_path, **params)
            res = _finalize(sid, res, source, title, rider)
            _save(sid, res)
            _set_job(jid, status='done', progress=1.0, stage='done', message='Done', session_id=sid)
        except engine.AnalysisError as e:
            _set_job(jid, status='error', error=str(e))
            shutil.rmtree(_session_dir(sid), ignore_errors=True)
        except Exception as e:  # pragma: no cover - surfaced to the UI
            import traceback
            traceback.print_exc()
            _set_job(jid, status='error', error=f"Analysis stopped unexpectedly ({e}). Try another clip, "
                                                 "or export it as MP4 (H.264).")
            shutil.rmtree(_session_dir(sid), ignore_errors=True)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={'Cache-Control': 'no-cache'})


@app.get("/api/config")
def get_config():
    samples = []
    for folder, kind, exts in (('videos', 'video', VIDEO_EXT), ('images', 'image', IMAGE_EXT)):
        d = os.path.join(SAMPLES_DIR, folder)
        if os.path.isdir(d):
            for fname in sorted(os.listdir(d)):
                if fname.lower().endswith(exts):
                    samples.append({'id': f'{folder}/{fname}', 'type': kind, 'name': fname,
                                    'url': f'/api/samples/{folder}/{fname}'})
    return {
        'version': VERSION,
        'profiles': fp.PROFILES,
        'default_profile': fp.DEFAULT_PROFILE,
        'metrics': fp.METRICS,
        'flexibility': fp.FLEXIBILITY,
        'pains': fp.PAINS,
        'backends': pb.BACKENDS,
        'available_backends': pb.available(),
        'samples': samples,
        'demo_available': os.path.exists(os.path.join(DEMO_DIR, 'result.json')),
    }


@app.get("/api/samples/{folder}/{filename}")
def get_sample_media(folder: str, filename: str):
    path = os.path.abspath(os.path.join(SAMPLES_DIR, folder, filename))
    if not path.startswith(SAMPLES_DIR + os.sep) or not os.path.isfile(path):
        raise HTTPException(404, "Sample not found")
    ext = os.path.splitext(filename)[1].lower()
    return FileResponse(path, media_type=MEDIA_TYPES.get(ext, 'application/octet-stream'))


@app.get("/models/pose_landmarker.task")
def get_pose_model():
    """Bundled MediaPipe model, reused by the in-browser live camera check."""
    if not os.path.exists(pb.MP_MODEL):
        raise HTTPException(404, "Model not bundled")
    return FileResponse(pb.MP_MODEL, media_type='application/octet-stream',
                        headers={'Cache-Control': 'public, max-age=604800'})


@app.post("/api/jobs")
async def create_job(
    file: Optional[UploadFile] = File(None),
    sample_id: Optional[str] = Form(None),
    profile: str = Form(fp.DEFAULT_PROFILE),
    backend: str = Form("auto"),
    crank_mm: float = Form(172.5),
    rider_height: Optional[float] = Form(None),
    rider_name: Optional[str] = Form(""),
    flexibility: str = Form("average"),
    pains: Optional[str] = Form(""),
):
    if profile not in fp.PROFILES:
        raise HTTPException(400, f"Unknown riding style '{profile}'")
    if flexibility not in fp.FLEXIBILITY:
        flexibility = 'average'
    pain_list = [p for p in (pains or '').split(',') if p in fp.PAINS]
    if rider_height is not None and not (100 <= rider_height <= 230):
        raise HTTPException(400, "Enter a height between 100 and 230 cm.")
    if not (150 <= crank_mm <= 185):
        raise HTTPException(400, "Enter a crank length between 150 and 185 mm.")

    sid = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    sdir = _session_dir(sid)
    os.makedirs(sdir, exist_ok=True)

    if file and file.filename:
        ext = os.path.splitext(file.filename)[1].lower() or '.mp4'
        if ext not in VIDEO_EXT + IMAGE_EXT:
            shutil.rmtree(sdir, ignore_errors=True)
            raise HTTPException(400, "Upload a video (MP4, MOV, WebM) or a photo (JPG, PNG, WebP).")
        media_type = 'image' if ext in IMAGE_EXT else 'video'
        fname = f'source{ext}'
        with open(os.path.join(sdir, fname), 'wb') as out:
            shutil.copyfileobj(file.file, out)
        source = {'file': fname, 'name': file.filename, 'type': media_type}
        media_path = os.path.join(sdir, fname)
        title = os.path.splitext(file.filename)[0][:60]
    elif sample_id:
        path = os.path.abspath(os.path.join(SAMPLES_DIR, sample_id))
        if not path.startswith(SAMPLES_DIR + os.sep) or not os.path.isfile(path):
            shutil.rmtree(sdir, ignore_errors=True)
            raise HTTPException(404, "Sample not found")
        media_type = 'image' if path.lower().endswith(IMAGE_EXT) else 'video'
        source = {'sample': sample_id, 'name': os.path.basename(path), 'type': media_type}
        media_path = path
        title = f"Sample · {os.path.basename(path)}"
    else:
        shutil.rmtree(sdir, ignore_errors=True)
        raise HTTPException(400, "Choose a video or photo to analyse.")

    params = dict(profile_key=profile, backend=backend, crank_mm=crank_mm, rider_height_cm=rider_height,
                  flexibility=flexibility, pains=pain_list)
    rider = {'name': (rider_name or '').strip()[:60], 'height_cm': rider_height, 'crank_mm': crank_mm}
    jid = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[jid] = {'id': jid, 'status': 'queued', 'progress': 0.0, 'stage': 'upload',
                     'message': 'Queued', 'session_id': None, 'error': None, 'created': time.time()}
    threading.Thread(target=_run_job, args=(jid, sid, media_path, media_type, params, source, title, rider),
                     daemon=True).start()
    return {'job_id': jid}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if not job:
            raise HTTPException(404, "Job not found")
        return dict(job)


@app.get("/api/sessions")
def list_sessions():
    out = []
    for sid in sorted(os.listdir(SESSIONS_DIR), reverse=True):
        p = os.path.join(SESSIONS_DIR, sid, 'result.json')
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    out.append(_summary(json.load(f)))
            except (OSError, ValueError):
                continue
    return {'sessions': out}


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    return _load(sid)


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    if sid == DEMO_ID:
        raise HTTPException(400, "The sample fit can't be deleted.")
    sdir = _session_dir(sid)
    if not os.path.isdir(sdir):
        raise HTTPException(404, "Fit not found")
    shutil.rmtree(sdir)
    return {'deleted': sid}


@app.get("/api/sessions/{sid}/media")
def get_session_media(sid: str):
    data = _load(sid)
    path = _media_path(sid, data)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "The original video is no longer available.")
    ext = os.path.splitext(path)[1].lower()
    return FileResponse(path, media_type=MEDIA_TYPES.get(ext, 'application/octet-stream'))


@app.get("/api/sessions/{sid}/frame")
def get_session_frame(sid: str, t: float = 0.0, w: int = 960):
    """Decoded frame at time t, for browsers that can't play the uploaded codec."""
    data = _load(sid)
    path = _media_path(sid, data)
    cap = cv2.VideoCapture(path) if path else None
    if cap is None or not cap.isOpened():
        raise HTTPException(404, "Video unavailable")
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(404, "Frame unavailable")
    jpg = engine._to_jpeg(img, width=max(320, min(w, 1600)), quality=82)
    return Response(jpg, media_type='image/jpeg', headers={'Cache-Control': 'public, max-age=3600'})


@app.get("/api/sessions/{sid}/snapshots/{name}.jpg")
def get_snapshot(sid: str, name: str):
    if not name.isalnum():
        raise HTTPException(404, "Snapshot not found")
    p = os.path.join(_session_dir(sid), 'snapshots', f'{name}.jpg')
    if not os.path.isfile(p):
        raise HTTPException(404, "Snapshot not found")
    return FileResponse(p, media_type='image/jpeg')


class RescoreRequest(BaseModel):
    profile: str
    flexibility: str = 'average'
    pains: List[str] = []


@app.post("/api/sessions/{sid}/rescore")
def rescore(sid: str, req: RescoreRequest):
    if req.profile not in fp.PROFILES:
        raise HTTPException(400, f"Unknown riding style '{req.profile}'")
    data = _load(sid)
    engine.score_result(data, req.profile, req.flexibility if req.flexibility in fp.FLEXIBILITY else 'average',
                        [p for p in req.pains if p in fp.PAINS])
    data['coach'] = {'text': coach.coach_notes(data['recommendations'], data['profile_name'])}
    _save(sid, data)
    return data


@app.get("/api/sessions/{sid}/report.pdf")
def session_pdf(sid: str):
    data = _load(sid)
    sdir = _session_dir(sid)
    snaps = {k: os.path.join(sdir, 'snapshots', f'{k}.jpg') for k in (data.get('snapshots') or {})}
    snaps = {k: p for k, p in snaps.items() if os.path.isfile(p)}
    pdf = report.generate_fit_report(data, snaps, coach_text=(data.get('coach') or {}).get('text'),
                                     rider_name=(data.get('rider') or {}).get('name') or '')
    name = f"openvelo-fit-{sid}.pdf"
    return Response(pdf, media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


# --------------------------------------------------------------------------- #
# Synchronous endpoint kept for scripts and older clients
# --------------------------------------------------------------------------- #
@app.post("/api/analyze")
async def analyze_media(
    file: Optional[UploadFile] = File(None),
    sample_id: Optional[str] = Form(None),
    profile: str = Form(fp.DEFAULT_PROFILE),
    backend: str = Form("auto"),
    crank_mm: float = Form(172.5),
    rider_height: Optional[float] = Form(None),
):
    tmp = None
    try:
        if file and file.filename:
            suffix = os.path.splitext(file.filename)[1].lower() or '.mp4'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as t:
                shutil.copyfileobj(file.file, t)
                tmp = path = t.name
        elif sample_id:
            path = os.path.abspath(os.path.join(SAMPLES_DIR, sample_id))
            if not path.startswith(SAMPLES_DIR + os.sep) or not os.path.isfile(path):
                raise HTTPException(404, "Sample not found")
        else:
            raise HTTPException(400, "Choose a video or photo to analyse.")
        if path.lower().endswith(IMAGE_EXT):
            res = engine.analyze_image(path, profile_key=profile, backend=backend)
        else:
            res = engine.analyze_video(path, profile_key=profile, backend=backend, crank_mm=crank_mm,
                                       rider_height_cm=rider_height)
        res['snapshots'] = {k: {'title': s['title']} for k, s in (res.get('snapshots') or {}).items()}
        return res
    except engine.AnalysisError as e:
        raise HTTPException(422, str(e))
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
