"""
OpenVelo posture: back roundness and riding-posture checks.

Pose models give only two points on the trunk (shoulder and hip), so they can't
see whether the back is flat or hunched. OpenVelo reads the back from the
rider's silhouette instead (MediaPipe's person segmentation mask):

  hip -> shoulder chord, split into N stations
    -> from each station, march along the dorsal normal until leaving the mask
    -> back contour (one point per station)
    -> signed offset of each contour point from the straight line joining the
       contour's ends, as % of back length (+ = bulging out = rounded)
    -> roundness index = peak offset of the curve, per frame
    -> per-stroke medians, heat map (time x back position), average shapes

Conventions match kinematics.py: image pixels, x right, y down.
"""
import numpy as np

STATIONS = np.linspace(0.18, 0.94, 15)  # fraction of hip->shoulder; skips glutes and neck
MASK_ON = 0.5
MAX_REACH = 0.75                        # search up to this x chord length from the bone line

# Roundness index (% of back length) thresholds, calibrated on the bundled clips:
# an upright, flat back reads ~2% (jersey folds, shoulder blades); visibly hunched
# road and TT positions read 7-10%.
NEUTRAL_MAX = 4.0
ROUNDED_MIN = 6.5
VERDICTS = {
    'neutral': ('ok', 'Neutral back'),
    'slight': ('watch', 'Slight rounding'),
    'rounded': ('fix', 'Rounded back'),
}


def verdict_for(index):
    if index is None or not np.isfinite(index):
        return None
    return 'neutral' if index < NEUTRAL_MAX else 'slight' if index < ROUNDED_MIN else 'rounded'


def dorsal_normal(hip, shoulder, facing):
    """Unit normal to hip->shoulder pointing out of the rider's back."""
    u = np.asarray(shoulder, float) - np.asarray(hip, float)
    L = np.linalg.norm(u)
    if L < 1:
        return None, 0.0
    u /= L
    # Facing right, rotating u by -90 deg on screen points backwards/upwards
    n = np.array([u[1], -u[0]]) if facing == 'right' else np.array([-u[1], u[0]])
    return n, L


def back_profile(mask, hip, shoulder, facing):
    """
    Back contour for one frame.
    Returns (contour[N,2] px, offset[N] as fraction of back length) or None.
    """
    if mask is None or hip is None or shoulder is None:
        return None
    n, L = dorsal_normal(hip, shoulder, facing)
    if n is None or L < 20:
        return None
    hgt, wid = mask.shape[:2]
    hip = np.asarray(hip, float)
    base = hip[None, :] + STATIONS[:, None] * (np.asarray(shoulder, float) - hip)[None, :]
    steps = np.arange(0, MAX_REACH * L, 1.0)
    pts = base[:, None, :] + steps[None, :, None] * n[None, None, :]          # N x S x 2
    xi = np.clip(np.round(pts[..., 0]).astype(int), 0, wid - 1)
    yi = np.clip(np.round(pts[..., 1]).astype(int), 0, hgt - 1)
    inside = mask[yi, xi] >= MASK_ON
    contour = np.full((len(STATIONS), 2), np.nan)
    for i, row in enumerate(inside):
        if not row[: max(2, int(0.15 * len(row)))].any():
            continue  # the bone line isn't inside the silhouette here
        out = np.where(~row)[0]
        first_in = np.argmax(row)
        out = out[out > first_in]
        if not len(out) or out[0] >= len(row) - 1:
            continue  # never left the mask: merged with background or bike
        contour[i] = pts[i, out[0] - 1]
    ok = np.isfinite(contour[:, 0])
    if ok.sum() < 0.7 * len(STATIONS) or not ok[0] or not ok[-1]:
        return None
    return contour, _offsets(contour, facing)


def _offsets(contour, facing):
    """Signed distance of each contour point from its end-to-end line, / line length."""
    a, b = contour[0], contour[-1]
    n, L = dorsal_normal(a, b, facing)
    if n is None:
        return np.full(len(contour), np.nan)
    return ((contour - a) @ n) / L


def roundness_index(offset):
    """Peak outward bulge of the back curve, % of back length."""
    if offset is None or not np.isfinite(offset).any():
        return np.nan
    return float(100.0 * max(0.0, np.nanmax(offset)))


def _smooth_rows(M, k=5):
    """Centred moving median along time for each station (robust to mask flicker)."""
    out = M.copy()
    T = M.shape[0]
    for t in range(T):
        seg = M[max(0, t - k // 2): t + k // 2 + 1]
        with np.errstate(all='ignore'):
            ok = np.isfinite(seg).any(axis=0)
            out[t, ok] = np.nanmedian(seg[:, ok], axis=0)
    return out


def summarize(profiles, times, stroke_id=None, tracks=None, mm_per_px=None, fps=30.0):
    """
    Per-frame profiles -> JSON-friendly posture summary.
    profiles: list of (contour, offset) or None, one per analysed frame.
    """
    times = np.asarray(times, float)
    T, N = len(profiles), len(STATIONS)
    offs = np.full((T, N), np.nan)
    backlen = np.full(T, np.nan)
    for t, p in enumerate(profiles):
        if p is not None:
            offs[t] = p[1]
            backlen[t] = np.linalg.norm(p[0][-1] - p[0][0])
    valid = np.isfinite(offs).any(axis=1)
    if valid.sum() < max(5, 0.2 * T):
        return None
    offs = _smooth_rows(offs, k=max(3, int(round(fps * 0.15)) | 1))
    index = np.array([roundness_index(o) for o in offs])
    idx_ok = index[np.isfinite(index)]
    overall = float(np.median(idx_ok))
    blen = float(np.nanmedian(backlen))

    # Strokes: per-stroke median roundness and shape, plus timing
    strokes = []
    if stroke_id is not None:
        for s in sorted(set(int(x) for x in stroke_id if x >= 0)):
            sel = np.where(stroke_id == s)[0]
            if len(sel) < 3:
                continue
            ri = index[sel][np.isfinite(index[sel])]
            with np.errstate(all='ignore'):
                shape = np.nanmedian(offs[sel], axis=0)
            strokes.append({
                'n': len(strokes) + 1,
                't0': round(float(times[sel[0]]), 3), 't1': round(float(times[sel[-1]]), 3),
                'duration': round(float(times[sel[-1]] - times[sel[0]] + 1.0 / fps), 3),
                'roundness': round(float(np.median(ri)), 2) if len(ri) else None,
                'shape': [None if not np.isfinite(v) else round(float(v) * 100, 2) for v in shape],
            })
            strokes[-1]['verdict'] = verdict_for(strokes[-1]['roundness'])
    timing = None
    # Strokes cut off by the start or end of the clip aren't real revolutions
    whole = [s for s in strokes if s['duration'] >= 0.8 * np.median([x['duration'] for x in strokes])]
    if len(whole) >= 2:
        durs = [s['duration'] for s in whole]
        fast, slow = whole[int(np.argmin(durs))], whole[int(np.argmax(durs))]
        timing = {'avg': round(float(np.mean(durs)), 3),
                  'fastest': {'n': fast['n'], 'duration': fast['duration']},
                  'slowest': {'n': slow['n'], 'duration': slow['duration']},
                  'cv_pct': round(float(100 * np.std(durs) / np.mean(durs)), 1)}
    worst = max((s for s in strokes if s['roundness'] is not None), key=lambda s: s['roundness'], default=None)

    # Heat map: offsets binned to <= 160 time columns, in % of back length
    cols = min(160, T)
    edges = np.linspace(0, T, cols + 1).astype(int)
    heat = []
    for a, b in zip(edges[:-1], edges[1:]):
        with np.errstate(all='ignore'):
            col = np.nanmean(offs[a:max(b, a + 1)], axis=0)
        heat.append([None if not np.isfinite(v) else round(float(v) * 100, 2) for v in col])
    with np.errstate(all='ignore'):
        avg_shape = np.nanmedian(offs, axis=0) * 100

    # Vertical displacement of hip and shoulder (up = +), relative to their lowest point
    disp = None
    if tracks is not None:
        step = max(1, T // 400)
        disp = {'t': [round(float(x), 3) for x in times[::step]], 'unit': 'mm' if mm_per_px else 'px'}
        for j in ('hip', 'shoulder'):
            y = -tracks[j][:, 1]
            # Robust baseline: a tracker glitch on the first frames shouldn't set zero
            y = np.clip(y - np.nanpercentile(y, 2), 0, None) * (mm_per_px or 1.0)
            disp[j] = [None if not np.isfinite(v) else round(float(v), 1) for v in y[::step]]

    v = verdict_for(overall)
    return {
        'available': True,
        'stations': [round(float(s), 3) for s in STATIONS],
        'roundness': round(overall, 2),
        'roundness_p90': round(float(np.percentile(idx_ok, 90)), 2),
        'bulge_mm': round(overall / 100 * blen * mm_per_px, 1) if mm_per_px else None,
        'back_length_px': round(blen, 1),
        'verdict': v, 'status': VERDICTS[v][0], 'label': VERDICTS[v][1],
        'thresholds': [NEUTRAL_MAX, ROUNDED_MIN],
        'rounded_share': round(float(np.mean(idx_ok >= ROUNDED_MIN)), 3),
        'frame_index': [None if not np.isfinite(x) else round(float(x), 1) for x in index],
        'heat': {'t0': float(times[0]), 't1': float(times[-1]), 'cols': heat},
        'avg_shape': [None if not np.isfinite(x) else round(float(x), 2) for x in avg_shape],
        'strokes': strokes,
        'worst_stroke': worst['n'] if worst else None,
        'timing': timing,
        'displacement': disp,
    }


def posture_checks(res):
    """Combine back shape with the trunk and arm angles into a posture checklist and score."""
    post = res.get('posture') or {}
    st = res.get('statuses') or {}
    m = res.get('metrics') or {}
    rng = res.get('ranges') or {}
    bio = res.get('biomech') or {}
    checks = []
    if post.get('available'):
        checks.append({'key': 'back', 'label': 'Back shape', 'status': post['status'],
                       'value': f"{post['roundness']:.1f}% curve",
                       'detail': {'neutral': 'Your spine stays long and flat from hips to shoulders.',
                                  'slight': 'Your upper back curves a little. Hinge from the hips and keep the chest open.',
                                  'rounded': 'Your back hunches over the bars. Usually a sign the reach is too long or the bars too low.'}
                       [post['verdict']]})
    for key, label, tip in (('torso', 'Torso angle', 'Trunk lean versus horizontal'),
                            ('shoulder', 'Shoulder angle', 'Reach from hip to elbow'),
                            ('elbow', 'Elbow bend', 'Soft elbows absorb road buzz')):
        if m.get(key) and st.get(key) and st[key] != 'na':
            lo, hi = rng.get(key, (None, None))
            checks.append({'key': key, 'label': label, 'status': st[key],
                           'value': f"{m[key]:.0f}°", 'detail': f"{tip}; target {lo:g}–{hi:g}°." if lo is not None else tip})
    rock = bio.get('hip_rock_mm')
    if rock is not None:
        s = 'ok' if rock <= 10 else 'watch' if rock <= 15 else 'fix'
        checks.append({'key': 'hip_rock', 'label': 'Pelvis stability', 'status': s, 'value': f"{rock:.0f} mm",
                       'detail': 'Hip bounce per stroke; more than 10 mm suggests the saddle is too high.'})
    if not checks:
        return None
    pts = {'ok': 1.0, 'watch': 0.5, 'fix': 0.0}
    w = {'back': 2.0}
    total = sum(w.get(c['key'], 1.0) for c in checks)
    score = round(100 * sum(w.get(c['key'], 1.0) * pts.get(c['status'], 0) for c in checks) / total)
    return {'score': score, 'checks': checks}


def back_recommendation(post, metrics=None, ranges=None):
    """Plan item for a rounded back (None when the back is fine)."""
    if not post or not post.get('available') or post['verdict'] == 'neutral':
        return None
    bulge = f" (about {post['bulge_mm']:.0f} mm of curve)" if post.get('bulge_mm') else ""
    sh, lo = (metrics or {}).get('shoulder'), ((ranges or {}).get('shoulder') or (None,))[0]
    if post['verdict'] == 'rounded':
        if sh and lo and sh < lo:
            # Short reach + hunched back: the rider is cramped, not stretched
            return {'priority': 2, 'metric': 'back',
                    'title': 'Flatten your back: give yourself more room',
                    'detail': f"Your back rounds by {post['roundness']:.1f}% of its length{bulge} while the reach "
                              "is short, so you're folding to fit. Try a stem 10 mm longer, then roll the pelvis "
                              "forward and hinge from the hips."}
        return {'priority': 2, 'metric': 'back',
                'title': 'Flatten your back: shorten the reach or raise the bars',
                'detail': f"Your back rounds by {post['roundness']:.1f}% of its length{bulge}. Try a stem 10 mm "
                          "shorter or one 5 mm spacer under it, then roll the pelvis forward and hinge from the hips."}
    return {'priority': 3, 'metric': 'back',
            'title': 'Keep the chest open to avoid rounding',
            'detail': f"Your upper back curves slightly ({post['roundness']:.1f}%){bulge}. Cue a long spine and "
                      "relaxed shoulders; if it worsens late in rides, check reach."}


# --------------------------------------------------------------------------- #
# Static panel (PDF report and the "Back & posture" key frame)
# --------------------------------------------------------------------------- #
_BG, _GRID, _TXT, _MUTED = (18, 17, 16), (52, 50, 48), (236, 236, 232), (150, 148, 144)
_STATUS_BGR = {'ok': (140, 205, 70), 'watch': (60, 180, 245), 'fix': (90, 90, 235)}
_HIP_BGR, _SH_BGR = (44, 227, 226), (235, 170, 80)


def diverging_bgr(v, lim):
    """Offset (%) -> colour: arched (teal) .. flat (dark) .. rounded (amber -> red)."""
    x = float(np.clip(v / lim, -1, 1)) if lim else 0.0
    dark = np.array([40, 34, 30], float)
    if x < 0:
        return tuple(int(c) for c in dark + (-x) * (np.array([200, 180, 40]) - dark))
    if x < 0.5:
        return tuple(int(c) for c in dark + (2 * x) * (np.array([40, 170, 240]) - dark))
    return tuple(int(c) for c in np.array([40, 170, 240]) + (2 * x - 1) * (np.array([70, 60, 230]) - np.array([40, 170, 240])))


def _text(img, s, org, scale=0.5, col=_TXT, th=1, anchor='l'):
    import cv2
    (w, h), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    x, y = org
    if anchor == 'c':
        x -= w // 2
    elif anchor == 'r':
        x -= w
    cv2.putText(img, s, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale, col, th, cv2.LINE_AA)


def _frame(img, x0, y0, x1, y1, title):
    import cv2
    cv2.rectangle(img, (x0, y0), (x1, y1), (26, 25, 24), -1)
    _text(img, title, ((x0 + x1) // 2, y0 - 10), 0.6, _TXT, 1, 'c')


def _time_axis(img, x0, x1, y, t0, t1):
    import cv2
    span = max(t1 - t0, 1e-6)
    step = next(s for s in (1, 2, 5, 10, 15, 30, 60) if span / s <= 8)
    t = np.ceil(t0 / step) * step
    while t <= t1 + 1e-6:
        x = int(x0 + (t - t0) / span * (x1 - x0))
        cv2.line(img, (x, y), (x, y + 4), _MUTED, 1)
        _text(img, f"{t:.0f}s", (x, y + 18), 0.38, _MUTED, 1, 'c')
        t += step


def render_panel(post, width=1400):
    """Charts-only panel: displacement, roundness map, average back shape, stats."""
    import cv2
    W, H = width, int(width * 0.62)
    img = np.full((H, W, 3), _BG, np.uint8)
    pad, gap = 70, 54
    colw = (W - 3 * pad // 2) // 2
    L = (pad, pad // 2 + colw)
    R = (W - pad // 2 - colw + pad // 2, W - pad // 2)
    t0, t1 = post['heat']['t0'], post['heat']['t1']
    tx = lambda t, a, b: int(a + (t - t0) / max(t1 - t0, 1e-6) * (b - a))  # noqa: E731

    # 1. Vertical displacement (left top)
    y0, y1 = 70, int(H * 0.46)
    _frame(img, L[0], y0, L[1], y1, 'Hip & shoulder vertical displacement')
    disp = post.get('displacement')
    if disp:
        vals = [v for k in ('hip', 'shoulder') for v in disp[k] if v is not None]
        hi = max(vals) if vals else 1
        hi = max(hi, 1e-6) * 1.1
        for k in range(5):
            yy = int(y1 - k / 4 * (y1 - y0))
            cv2.line(img, (L[0], yy), (L[1], yy), _GRID, 1)
            _text(img, f"{hi * k / 4:.0f}", (L[0] - 8, yy + 4), 0.38, _MUTED, 1, 'r')
        for key, col in (('hip', _HIP_BGR), ('shoulder', _SH_BGR)):
            pts = [(tx(t, *L), int(y1 - v / hi * (y1 - y0))) for t, v in zip(disp['t'], disp[key]) if v is not None]
            if len(pts) > 1:
                cv2.polylines(img, [np.array(pts, np.int32)], False, col, 2, cv2.LINE_AA)
        _text(img, f"Rise ({disp['unit']})", (L[0] - 50, y0 - 10), 0.38, _MUTED)
        cv2.line(img, (L[1] - 190, y0 + 18), (L[1] - 170, y0 + 18), _HIP_BGR, 3)
        _text(img, 'Hip', (L[1] - 164, y0 + 23), 0.42, _TXT)
        cv2.line(img, (L[1] - 120, y0 + 18), (L[1] - 100, y0 + 18), _SH_BGR, 3)
        _text(img, 'Shoulder', (L[1] - 94, y0 + 23), 0.42, _TXT)
    _time_axis(img, L[0], L[1], y1, t0, t1)

    # 2. Back roundness map (right top): x = time, y = position along the back
    _frame(img, R[0], y0, R[1] - 50, y1, 'Back roundness map')
    cols = post['heat']['cols']
    lim = max(post['thresholds'][1] * 1.4, 1.0)
    n = len(post['stations'])
    hx0, hx1 = R[0], R[1] - 50
    for c, col in enumerate(cols):
        xa = int(hx0 + c / len(cols) * (hx1 - hx0))
        xb = int(hx0 + (c + 1) / len(cols) * (hx1 - hx0))
        for s, v in enumerate(col):
            if v is None:
                continue
            ya = int(y1 - (s + 1) / n * (y1 - y0))
            yb = int(y1 - s / n * (y1 - y0))
            cv2.rectangle(img, (xa, ya), (max(xa, xb - 1), yb), diverging_bgr(v, lim), -1)
    _text(img, 'Shoulders', (hx0 - 6, y0 + 12), 0.36, _MUTED, 1, 'r')
    _text(img, 'Hips', (hx0 - 6, y1 - 2), 0.36, _MUTED, 1, 'r')
    for k in range(40):  # colour bar
        v = lim - 2 * lim * k / 39
        ya = int(y0 + k / 40 * (y1 - y0))
        cv2.rectangle(img, (R[1] - 34, ya), (R[1] - 22, int(y0 + (k + 1) / 40 * (y1 - y0))), diverging_bgr(v, lim), -1)
    _text(img, f"+{lim:.0f}%", (R[1] - 40, y0 - 4), 0.36, _MUTED)
    _text(img, f"-{lim:.0f}%", (R[1] - 40, y1 + 14), 0.36, _MUTED)
    _time_axis(img, hx0, hx1, y1, t0, t1)

    # 3. Average back shape per stroke (left bottom)
    y2, y3 = y1 + gap + 20, H - 40
    _frame(img, L[0], y2, L[1], y3, 'Average back shape per stroke')
    strokes = post['strokes'] or []
    amp = (L[1] - L[0]) / max(len(strokes), 8) * 0.45 / lim
    for st in strokes:
        cx = tx((st['t0'] + st['t1']) / 2, *L)
        pts = [(int(cx + v * amp), int(y3 - 6 - s / (n - 1) * (y3 - y2 - 14)))
               for s, v in enumerate(st['shape']) if v is not None]
        col = _STATUS_BGR[VERDICTS[st['verdict']][0]] if st.get('verdict') else _MUTED
        cv2.line(img, (cx, y2 + 4), (cx, y3 - 4), _GRID, 1)
        if len(pts) > 1:
            cv2.polylines(img, [np.array(pts, np.int32)], False, col, 2, cv2.LINE_AA)
    if len(strokes) <= 14:
        for st in strokes:
            _text(img, str(st['n']), (tx((st['t0'] + st['t1']) / 2, *L) + 6, y3 - 8), 0.42, _TXT)
    _text(img, 'Shoulders', (L[0] - 6, y2 + 12), 0.36, _MUTED, 1, 'r')
    _text(img, 'Hips', (L[0] - 6, y3 - 2), 0.36, _MUTED, 1, 'r')
    _time_axis(img, L[0], L[1], y3, t0, t1)

    # 4. Stats + verdict (right bottom)
    x = R[0] + 10
    y = y2 + 16
    tm = post.get('timing')
    lines = [f"Back curve: {post['roundness']:.1f}% of back length" +
             (f" ({post['bulge_mm']:.0f} mm)" if post.get('bulge_mm') else ''),
             f"Rounded in {post['rounded_share'] * 100:.0f}% of frames"]
    if tm:
        lines += [f"Avg stroke time: {tm['avg']:.2f}s",
                  f"Fastest: Stroke {tm['fastest']['n']} - {tm['fastest']['duration']:.2f}s",
                  f"Slowest: Stroke {tm['slowest']['n']} - {tm['slowest']['duration']:.2f}s"]
    for i, s in enumerate(lines):
        _text(img, s, (x, y + i * 38), 0.6, _TXT)
    col = _STATUS_BGR[post['status']]
    cx, cy = R[1] - 120, y2 + 110
    cv2.circle(img, (cx, cy), 30, col, -1, cv2.LINE_AA)
    if post['status'] == 'watch':
        cv2.line(img, (cx, cy - 15), (cx, cy + 4), _BG, 6, cv2.LINE_AA)
        cv2.circle(img, (cx, cy + 14), 4, _BG, -1, cv2.LINE_AA)
    elif post['status'] == 'ok':
        cv2.polylines(img, [np.array([(cx - 13, cy), (cx - 3, cy + 10), (cx + 14, cy - 10)], np.int32)], False, _BG, 5, cv2.LINE_AA)
    else:
        cv2.line(img, (cx - 11, cy - 11), (cx + 11, cy + 11), _BG, 5, cv2.LINE_AA)
        cv2.line(img, (cx - 11, cy + 11), (cx + 11, cy - 11), _BG, 5, cv2.LINE_AA)
    _text(img, post['label'], (cx, cy + 62), 0.75, col, 2, 'c')
    if post.get('worst_stroke'):
        _text(img, f"Most rounded: stroke {post['worst_stroke']}", (cx, cy - 46), 0.5, _MUTED, 1, 'c')
    return img
