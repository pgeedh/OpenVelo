"""
OpenVelo analysis engine.

Pure functions that turn a video or photo into fit metrics, annotated frames and
recommendations. No GUI calls (cv2.imshow etc.) so it can power both the
FastAPI/Streamlit web apps and the command-line scripts.
"""
import os
import sys

import cv2
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src import core  # noqa: E402
from src import fit_profiles as fp  # noqa: E402
from src import pose_backends as pb  # noqa: E402
from src import kinematics as kin  # noqa: E402
from src import posture  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

SKELETON = [('shoulder', 'elbow'), ('elbow', 'wrist'), ('shoulder', 'hip'),
            ('hip', 'knee'), ('knee', 'ankle'), ('ear', 'shoulder')]

# Brand palette (BGR), matching the web studio tokens
C_BONE = (247, 245, 244)       # near-white bones
C_JOINT = (44, 227, 226)       # brand yellow joints
C_FOOT = (44, 227, 226)        # brand yellow foot triangle
C_OK = (167, 218, 81)          # in range
C_WATCH = (83, 185, 252)       # borderline
C_FIX = (139, 142, 249)        # adjust
STATUS_BGR = {'ok': C_OK, 'watch': C_WATCH, 'fix': C_FIX, 'na': (200, 200, 200)}


class AnalysisError(Exception):
    """Raised with a user-friendly message when analysis can't proceed."""


# --------------------------------------------------------------------------- #
# Drawing & Overlay
# --------------------------------------------------------------------------- #
def _pt(v):
    return int(round(v[0])), int(round(v[1]))


def _label(img, text, org, color, scale=0.6):
    """Text with a frosted dark rounded background pill for legibility."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    th = max(1, int(round(scale * 2)))
    (w, h), base = cv2.getTextSize(text, font, scale, th)
    x, y = org
    x = max(2, min(x, img.shape[1] - w - 8))
    y = max(h + 6, min(y, img.shape[0] - 6))
    cv2.rectangle(img, (x - 6, y - h - 6), (x + w + 6, y + base + 3), (16, 13, 12), -1)
    cv2.rectangle(img, (x - 6, y - h - 6), (x + w + 6, y + base + 3), (45, 50, 65), 1, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), font, scale, color, th, cv2.LINE_AA)


def posture_panel(res):
    """Back & posture chart panel (BGR image) for the key frames and PDF."""
    return posture.render_panel(res['posture'])


def draw_overlay(img, lm, angles=None, statuses=None, title=None, back=None):
    """Draw skeleton, foot polygon, back contour and joint angle labels onto img (in place)."""
    angles = angles or {}
    statuses = statuses or {}
    scale = max(0.5, min(1.4, img.shape[1] / 1100.0))
    thick = max(2, int(3 * scale))

    if back is not None:
        contour, index = back
        col = STATUS_BGR.get(posture.VERDICTS[posture.verdict_for(index) or 'neutral'][0], C_OK)
        pts = np.round(contour[np.isfinite(contour[:, 0])]).astype(np.int32)
        cv2.polylines(img, [pts], False, (16, 13, 12), thick + 3, cv2.LINE_AA)
        cv2.polylines(img, [pts], False, col, thick + 1, cv2.LINE_AA)

    if all(k in lm for k in ('ankle', 'heel', 'toe')):
        pts = np.array([_pt(lm['ankle']), _pt(lm['heel']), _pt(lm['toe'])], np.int32)
        overlay = img.copy()
        cv2.fillPoly(overlay, [pts], C_FOOT)
        cv2.addWeighted(overlay, 0.35, img, 0.65, 0, img)
        cv2.polylines(img, [pts], True, C_FOOT, thick, cv2.LINE_AA)

    for a, b in SKELETON:
        if a in lm and b in lm:
            cv2.line(img, _pt(lm[a]), _pt(lm[b]), C_BONE, thick, cv2.LINE_AA)

    for k in ('ear', 'shoulder', 'elbow', 'wrist', 'hip', 'knee', 'ankle'):
        if k in lm:
            cv2.circle(img, _pt(lm[k]), int(7 * scale), (16, 13, 12), -1, cv2.LINE_AA)
            cv2.circle(img, _pt(lm[k]), int(5 * scale), C_JOINT, -1, cv2.LINE_AA)

    # Angle labels next to joints
    joint_for = {'knee': ('knee', 'knee_ext'), 'hip': ('hip', 'hip_closed'),
                 'elbow': ('elbow', 'elbow'), 'arm_torso': ('shoulder', 'shoulder')}
    for akey, (joint, mkey) in joint_for.items():
        v = angles.get(akey, 0)
        if v and joint in lm:
            col = STATUS_BGR.get(statuses.get(mkey), (255, 255, 255))
            x, y = _pt(lm[joint])
            _label(img, f"{v:.0f}°", (x + int(14 * scale), y - int(10 * scale)), col, 0.55 * scale + 0.1)

    if title:
        _label(img, title, (int(18 * scale), int(40 * scale)), (255, 255, 255), 0.8 * scale)
    return img


def _resize(frame, max_width):
    h, w = frame.shape[:2]
    if max_width and w > max_width:
        s = max_width / float(w)
        frame = cv2.resize(frame, (max_width, int(round(h * s))), interpolation=cv2.INTER_AREA)
    return frame


def _to_jpeg(img, width=640, quality=80):
    small = _resize(img, width)
    ok, buf = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


# --------------------------------------------------------------------------- #
# Video Analysis
# --------------------------------------------------------------------------- #
OVERLAY_JOINTS = ['ear', 'shoulder', 'elbow', 'wrist', 'hip', 'knee', 'ankle', 'heel', 'toe', 'ball']


def resolve_backend(backend):
    avail = pb.available()
    if backend == 'auto':
        return 'mediapipe' if avail.get('mediapipe') else 'rtmpose'
    if not avail.get(backend):
        return 'mediapipe'
    return backend


def score_result(res, profile_key, flexibility='average', pains=()):
    """(Re)compute ranges, statuses, score and recommendations for a result dict in place."""
    ranges, notes = fp.personalize(profile_key, flexibility, pains)
    metrics = res['metrics']
    bio = res.get('biomech') or {}
    res['profile'] = profile_key
    res['profile_name'] = fp.PROFILES[profile_key]['name']
    res['rider'] = dict(res.get('rider') or {}, flexibility=flexibility, pains=list(pains or ()))
    res['ranges'] = {k: list(v) for k, v in ranges.items()}
    res['range_notes'] = notes
    res['statuses'] = {k: fp.status_for(metrics.get(k), ranges[k]) for k in fp.METRICS}
    res['score'] = fp.fit_score(metrics, profile_key, ranges)

    saddle = None
    if res.get('mode') == 'video' and metrics.get('knee_ext'):
        lo, hi = ranges['knee_ext']
        ke = metrics['knee_ext']
        if not lo <= ke <= hi:
            target = lo + 1.5 if ke < lo else hi - 1.5
            saddle = kin.saddle_change_mm(ke, target, bio.get('thigh_px'), bio.get('shank_px'),
                                          bio.get('mm_per_px'))
    bio['saddle_adj_mm'] = saddle
    res['biomech'] = bio
    recs = fp.build_recommendations(
        metrics, profile_key,
        {'torso_range': (res.get('extra') or {}).get('torso_range'), 'mode': res.get('mode'),
         'saddle_adj_mm': saddle, 'hip_rock_mm': bio.get('hip_rock_mm'), 'pains': pains},
        ranges=ranges)
    back = posture.back_recommendation(res.get('posture'), metrics, ranges)
    if back:
        recs.append(back)
        recs.sort(key=lambda r: r['priority'])
    res['recommendations'] = recs
    res['posture_check'] = posture.posture_checks(res)
    return res


def analyze_video(path, profile_key=fp.DEFAULT_PROFILE, progress_cb=None,
                  target_fps=30, max_seconds=45, max_width=1280, backend='auto', crank_mm=172.5, rider_height_cm=None,
                  flexibility='average', pains=()):
    """
    Analyse a side-on cycling video.

    progress_cb(fraction, message, stage) is called as work proceeds.
    Returns a JSON-friendly result dict (snapshots hold JPEG bytes).
    """
    def report(frac, msg, stage):
        if progress_cb:
            try:
                progress_cb(frac, msg, stage)
            except TypeError:
                progress_cb(frac, msg)

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise AnalysisError("Unable to open this video. Export it as MP4 (H.264) and try again.")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src_fps <= 1 or src_fps > 240:
        src_fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    stride = max(1, int(round(src_fps / float(target_fps))))
    max_src_frames = int(max_seconds * src_fps) if max_seconds else None
    if max_src_frames and total:
        total = min(total, max_src_frames)

    backend = resolve_backend(backend)
    report(0.02, f"Loading {pb.BACKENDS[backend]['name']}", 'detect')
    try:
        detector = pb.make_backend(backend, mode='video')
    except Exception:
        if backend == 'mediapipe':
            raise
        backend = 'mediapipe'
        detector = pb.make_backend(backend, mode='video')

    raw_frames, times, src_indices, back_profiles = [], [], [], []
    side_votes = {'left': 0, 'right': 0}
    processed = detected = 0
    frame_size = None

    src_idx = -1
    last_t = -1.0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        src_idx += 1
        if max_src_frames and src_idx >= max_src_frames:
            break
        if src_idx % stride:
            continue
        pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
        t = pos_ms / 1000.0 if pos_ms and pos_ms > 0 else src_idx / src_fps
        if t <= last_t:  # variable-frame-rate files can report stale timestamps
            t = last_t + stride / src_fps
        last_t = t

        frame = _resize(frame, max_width)
        frame_size = frame.shape[1], frame.shape[0]
        processed += 1
        raw = detector.process(frame, ts_ms=int(t * 1000))
        prof = None
        if raw:
            detected += 1
            f_side = core.detect_side({k: v[:2] for k, v in raw.items()})
            side_votes[f_side] += 1
            prof = _back_profile(getattr(detector, 'last_mask', None), raw, f_side)
        back_profiles.append(prof)
        raw_frames.append(raw or {})
        times.append(t)
        src_indices.append(src_idx)

        if processed % 6 == 0:
            frac = (src_idx / total) if total else 0.5
            report(0.05 + 0.75 * min(frac, 1.0), f"Tracking rider · {t:.1f} s", 'track')

    cap.release()
    detector.close()

    duration = times[-1] - times[0] if len(times) > 1 else 0.0
    if processed == 0:
        raise AnalysisError("The video is empty or unreadable. Export it as MP4 (H.264) and try again.")
    if detected == 0:
        raise AnalysisError(
            "No rider found. Film from the side with the whole bike and rider in frame, "
            "in good light, then upload again.")

    facing = 'left' if side_votes['left'] >= side_votes['right'] else 'right'
    report(0.82, "Fixing left/right leg swaps", 'model')
    kin_res = kin.analyze(raw_frames, times, facing, crank_mm=crank_mm, rider_height_cm=rider_height_cm)
    report(0.9, "Modelling pedal strokes", 'model')

    ang = kin_res['angles']
    tracks = kin_res['tracks']
    knee = ang['knee']
    if np.isfinite(knee).sum() < 10:
        raise AnalysisError("The leg wasn't visible clearly enough to measure. Film from the side "
                            "with hip, knee and ankle in view, then upload again.")

    metrics = {k: kin_res['metrics'].get(k) for k in fp.METRICS}
    if kin_res['method'] != 'harmonic':
        # Robust percentile fallback when no stable crank phase was found
        def pct(a, q):
            a = a[np.isfinite(a)]
            return float(np.percentile(a, q)) if len(a) else None
        metrics = {'knee_ext': pct(knee, 97), 'knee_flex': pct(knee, 3), 'hip_closed': pct(ang['hip'], 3),
                   'torso': pct(ang['torso'], 50), 'shoulder': pct(ang['shoulder'], 50),
                   'elbow': pct(ang['elbow'], 50)}
    metrics = {k: (float(v) if v is not None and np.isfinite(v) else 0.0) for k, v in metrics.items()}

    torso = ang['torso'][np.isfinite(ang['torso'])]
    torso_range = float(np.percentile(torso, 95) - np.percentile(torso, 5)) if len(torso) > 5 else None
    knee_range = (metrics['knee_ext'] - metrics['knee_flex']) if metrics['knee_flex'] else None

    der = kin_res['derived']
    det_rate = detected / float(processed)
    warnings = []
    if det_rate < 0.6:
        warnings.append(f"The rider was found in only {det_rate:.0%} of frames. Brighter, even light "
                        "and a plain background will make results steadier.")
    if duration < 8:
        warnings.append("The clip is shorter than 8 s. Record 20–30 s of steady pedalling for tighter results.")
    if knee_range is not None and knee_range < 35:
        warnings.append("The knee barely changed angle. Pedal continuously while recording.")
    side_ratio = _shoulder_overlap(raw_frames)
    knee_sd = (kin_res.get('fit_quality') or {}).get('knee', {}).get('resid_sd')
    if knee_sd is not None and knee_sd > 5:
        warnings.append(f"Tracking is noisy (knee scatter ±{knee_sd:.0f}° around the model). Results are "
                        "approximate: film side-on, steady, in good light, with the rider seated and pedalling.")
    if side_ratio is not None and side_ratio > 0.35:
        warnings.append("The camera doesn't look fully side-on (both shoulders are visible). "
                        "Place it square to the bike, at 90°.")
    if kin_res['method'] != 'harmonic':
        warnings.append("The pedal stroke couldn't be modelled, so angles use whole-clip percentiles. "
                        "Make sure the near foot and pedal are visible through the full stroke.")
    if not der.get('mm_per_px'):
        warnings.append("No millimetre scale: the crank circle wasn't clear and no rider height was given, "
                        "so adjustments are shown in degrees only.")

    # Key frames (from the phase model when available)
    theta = kin_res.get('theta')
    ev = kin_res.get('events') or {}
    key_targets = []
    if theta is not None and ev:
        key_targets = [('bdc', ev['knee_ext_at'], "Leg extension (bottom of stroke)"),
                       ('tdc', ev['knee_flex_at'], "Knee bend (top of stroke)"),
                       ('front', 90.0, "3 o'clock (power phase)")]
    snap_frames = {}
    for name, target, title in key_targets:
        diff = np.abs(((theta - target + 180) % 360) - 180)
        diff[~np.isfinite(knee)] = np.inf
        if np.isfinite(diff).any():
            snap_frames[name] = (int(np.nanargmin(diff)), title)
    if not snap_frames:
        snap_frames = {'bdc': (int(np.nanargmax(knee)), "Leg extension (bottom of stroke)"),
                       'tdc': (int(np.nanargmin(knee)), "Knee bend (top of stroke)")}

    report(0.94, "Rendering key frames", 'report')
    snapshots = _grab_snapshots(path, src_indices, tracks, ang, snap_frames, max_width, back_profiles)

    # Compact per-frame track for the client-side overlay (normalised 0-1)
    fw, fh = frame_size
    joints_out = {}
    for j in OVERLAY_JOINTS:
        arr = tracks[j]
        joints_out[j] = [None if not np.all(np.isfinite(p)) else [round(p[0] / fw, 4), round(p[1] / fh, 4)]
                         for p in arr]
    angle_out = {k: [None if not np.isfinite(v) else round(float(v), 1) for v in ang[k]]
                 for k in ('knee', 'hip', 'elbow', 'shoulder', 'torso', 'ankle')}
    crank = der.get('crank_fit') if (der.get('crank_fit') or {}).get('ok') else None
    track = {
        'size': [fw, fh],
        't': [round(float(x), 3) for x in times],
        'theta': ([None if not np.isfinite(v) else round(float(v), 1) for v in theta]
                  if theta is not None else None),
        'joints': joints_out,
        'angles': angle_out,
        'crank': ({'cx': crank['cx'] / fw, 'cy': crank['cy'] / fh, 'r': crank['r'] / fw} if crank else None),
        'facing': der.get('facing', facing),
    }

    # Back roundness from the silhouette (MediaPipe only)
    post = posture.summarize(back_profiles, times, kin_res.get('stroke_id'), tracks,
                             der.get('mm_per_px'), kin_res['fps'])
    if post:
        idx = post.pop('frame_index')
        keep = np.arange(0, len(posture.STATIONS), 2)
        track['back'] = [None if p is None else [[round(q[0] / fw, 4), round(q[1] / fh, 4)] for q in p[0][keep]]
                         for p in back_profiles]
        track['back_index'] = idx
        track['back_thresholds'] = post['thresholds']
    elif backend != 'mediapipe':
        warnings.append("Back roundness needs the MediaPipe engine (it reads the rider's silhouette). "
                        "Re-run with MediaPipe to see the back and posture analysis.")

    # Stroke samples for the chart (decimated scatter + fitted curves)
    stroke = None
    if kin_res.get('curves'):
        idx = np.where(np.isfinite(theta) & np.isfinite(knee))[0]
        idx = idx[::max(1, len(idx) // 500)]
        stroke = {'curves': kin_res['curves'],
                  'samples': {'theta': [round(float(theta[i]), 1) for i in idx],
                              **{k: [None if not np.isfinite(ang[k][i]) else round(float(ang[k][i]), 1)
                                     for i in idx] for k in ('knee', 'hip', 'ankle', 'torso', 'elbow')}},
                  'events': ev}

    detail = kin_res.get('detail', {})
    res = {
        'mode': 'video',
        'backend': backend,
        'backend_name': pb.BACKENDS[backend]['name'],
        'method': kin_res['method'],
        'metrics': metrics,
        'metric_detail': {k: {kk: vv for kk, vv in v.items()} for k, v in detail.items()},
        'warnings': warnings,
        'snapshots': snapshots,
        'track': track,
        'stroke': stroke,
        'biomech': {
            'cadence_rpm': der.get('cadence_rpm'),
            'kops_mm': der.get('kops_mm'),
            'hip_rock_mm': der.get('hip_rock_mm'),
            'hip_sway_mm': der.get('hip_sway_mm'),
            'ankle_range': kin_res['metrics'].get('ankle_range'),
            'hip_open': kin_res['metrics'].get('hip_open'),
            'knee_at_bdc': ev.get('knee_at_bdc'),
            'scale_source': der.get('scale_source'),
            'mm_per_px': der.get('mm_per_px'),
            'thigh_px': der.get('thigh_px'),
            'shank_px': der.get('shank_px'),
            'thigh_mm': der.get('thigh_mm'),
            'shank_mm': der.get('shank_mm'),
            'cycles_detected': der.get('strokes', 0),
            'per_stroke': kin_res.get('per_stroke', {}),
            'phase_method': kin_res.get('phase_method'),
        },
        'quality': {
            'grade': _quality_grade(det_rate, duration, side_ratio, knee_sd, kin_res['method']),
            'detection_rate': det_rate,
            'frames_analysed': processed,
            'fps': kin_res['fps'],
            'duration_s': duration,
            'swaps_fixed': kin_res['quality'].get('swaps_fixed', 0),
            'side_overlap': side_ratio,
            'crank_fit_rms_pct': (100 * crank['rms'] / crank['r']) if crank else None,
            'knee_model_r2': (kin_res.get('fit_quality') or {}).get('knee', {}).get('r2'),
            'knee_model_sd': (kin_res.get('fit_quality') or {}).get('knee', {}).get('resid_sd'),
        },
        'extra': {
            'duration_s': duration, 'frames_analysed': processed, 'detection_rate': det_rate,
            'side': kin_res.get('near_side', facing), 'torso_range': torso_range, 'knee_range': knee_range,
        },
        'rider': {'crank_mm': crank_mm, 'height_cm': rider_height_cm},
        'posture': post,
    }
    report(0.97, "Reading back shape", 'report')
    if post:
        res['snapshots']['posture'] = {'title': 'Back & posture analysis', 'angles': {},
                                       'jpg': _to_jpeg(posture_panel(res), width=1400, quality=88)}
    score_result(res, profile_key, flexibility, pains)
    report(1.0, "Done", 'report')
    return res


def _back_profile(mask, raw, facing):
    """Back contour for one frame from the mid-line of both shoulders and hips."""
    if mask is None:
        return None

    def mid(name):
        pts = [raw[f'{s}_{name}'][:2] for s in ('left', 'right')
               if f'{s}_{name}' in raw and raw[f'{s}_{name}'][2] >= kin.CONF_MIN * 0.5]
        return np.mean(pts, axis=0) if pts else None
    return posture.back_profile(mask, mid('hip'), mid('shoulder'), facing)


def _quality_grade(det_rate, duration, side_ratio, knee_sd, method):
    """'good' | 'fair' | 'poor' capture grade shown next to the results."""
    pts = 0
    pts += det_rate < 0.8
    pts += duration < 8
    pts += 2 * (side_ratio is not None and side_ratio > 0.35)
    pts += 2 * (knee_sd is not None and knee_sd > 5) + (knee_sd is not None and 3.5 < knee_sd <= 5)
    pts += 2 * (method != 'harmonic')
    return 'good' if pts == 0 else 'fair' if pts <= 2 else 'poor'


def _shoulder_overlap(raw_frames):
    """Ratio of left/right shoulder separation to torso length (0 = perfect side view)."""
    vals = []
    for f in raw_frames[:: max(1, len(raw_frames) // 60)]:
        need = ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip')
        if all(k in f for k in need):
            sep = np.hypot(f['left_shoulder'][0] - f['right_shoulder'][0],
                           f['left_shoulder'][1] - f['right_shoulder'][1])
            mid_s = np.add(f['left_shoulder'][:2], f['right_shoulder'][:2]) / 2
            mid_h = np.add(f['left_hip'][:2], f['right_hip'][:2]) / 2
            torso = np.linalg.norm(mid_s - mid_h)
            if torso > 1:
                vals.append(sep / torso)
    return float(np.median(vals)) if vals else None


def _grab_snapshots(path, src_indices, tracks, ang, key, max_width, back_profiles=None):
    """Annotated JPEG key frames drawn from the smoothed, identity-corrected tracks."""
    cap = cv2.VideoCapture(path)
    out = {}
    for name, (n, title) in key.items():
        cap.set(cv2.CAP_PROP_POS_FRAMES, src_indices[n])
        ok, img = cap.read()
        if not ok:
            continue
        img = _resize(img, max_width)
        lm = {j: tracks[j][n] for j in kin.SIDE_JOINTS if np.all(np.isfinite(tracks[j][n]))}
        angles = {'knee': ang['knee'][n], 'hip': ang['hip'][n], 'elbow': ang['elbow'][n],
                  'arm_torso': ang['shoulder'][n]}
        angles = {k: float(v) for k, v in angles.items() if np.isfinite(v)}
        prof = back_profiles[n] if back_profiles else None
        back = (prof[0], posture.roundness_index(prof[1])) if prof is not None else None
        draw_overlay(img, lm, angles, back=back)
        out[name] = {'title': title, 'jpg': _to_jpeg(img, width=1100, quality=88), 'angles': angles}
    cap.release()
    return out


# --------------------------------------------------------------------------- #
# Photo Analysis
# --------------------------------------------------------------------------- #
def load_image(path_or_bytes):
    if isinstance(path_or_bytes, (bytes, bytearray)):
        arr = np.frombuffer(path_or_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    else:
        img = cv2.imread(path_or_bytes)
    if img is None:
        raise AnalysisError("Couldn't read this image. Use JPG, PNG or WEBP.")
    return img


def analyze_image(path_or_bytes, profile_key=fp.DEFAULT_PROFILE, crank_position='auto',
                  max_width=1600, backend='auto', flexibility='average', pains=()):
    """
    Analyse a single side-on photo.

    backend: 'auto', 'rtmpose', or 'mediapipe'.
    crank_position: 'bottom' (6 o'clock), 'top' (12 o'clock), 'other' or 'auto'.
    """
    img = _resize(load_image(path_or_bytes), max_width)

    backend = resolve_backend(backend)
    detector = pb.make_backend(backend, mode='image')
    try:
        raw = detector.process(img)
    finally:
        detector.close()
    if not raw:
        raise AnalysisError("No rider detected. Use a side-on photo with the whole body in frame.")

    raw_xy = {k: [v[0], v[1]] for k, v in raw.items()}
    side = core.detect_side(raw_xy)
    lm = core.get_primary_landmarks(raw_xy, side)
    angles = core.analyze_posture(lm)
    knee = angles.get('knee', 0)

    if crank_position == 'auto':
        crank_position = 'bottom' if knee >= 125 else ('top' if 0 < knee <= 95 else 'other')

    metrics = {'torso': angles.get('back', 0), 'shoulder': angles.get('arm_torso', 0),
               'elbow': angles.get('elbow', 0), 'knee_ext': 0, 'knee_flex': 0, 'hip_closed': 0}
    if crank_position == 'bottom':
        metrics['knee_ext'] = knee
    elif crank_position == 'top':
        metrics['knee_flex'] = knee
        metrics['hip_closed'] = angles.get('hip', 0)

    warnings = []
    if crank_position == 'other':
        warnings.append("Pedals aren't at 6 o'clock (BDC) or 12 o'clock (TDC). "
                        "For saddle height measurement, take a photo with the pedal nearest the camera at 6 o'clock.")
    elif crank_position == 'top':
        warnings.append("Pedal is at TDC (12 o'clock). For complete saddle height verification, capture at 6 o'clock.")
    if not angles.get('knee'):
        warnings.append("Couldn't see the leg clearly (hip / knee / ankle).")
    warnings.append("A photo represents a static snapshot. For dynamic pedalling biomechanics, upload a 10–30s video.")

    ranges, _ = fp.personalize(profile_key, flexibility, pains)
    statuses = {k: fp.status_for(v, ranges[k]) for k, v in metrics.items()}
    vis = img.copy()
    draw_overlay(vis, lm, angles, statuses)

    res = {
        'mode': 'image',
        'backend': backend,
        'backend_name': pb.BACKENDS[backend]['name'],
        'profile': profile_key,
        'metrics': metrics,
        'raw_angles': angles,
        'statuses': statuses,
        'score': fp.fit_score(metrics, profile_key),
        'recommendations': fp.build_recommendations(metrics, profile_key, {'mode': 'image'}),
        'warnings': warnings,
        'snapshots': {'photo': {'title': 'Your Position', 'jpg': _to_jpeg(vis, width=1400, quality=90),
                                'angles': angles}},
        'biomech': {},
        'quality': {},
        'extra': {'side': side, 'crank_position': crank_position},
    }
    return score_result(res, profile_key, flexibility, pains)


def save_snapshots(result, out_dir):
    """Write snapshot JPEGs to disk (for PDF generation). Returns {key: path}."""
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for k, s in result['snapshots'].items():
        if s.get('jpg'):
            p = os.path.join(out_dir, f"snap_{k}.jpg")
            with open(p, 'wb') as fh:
                fh.write(s['jpg'])
            paths[k] = p
    return paths
