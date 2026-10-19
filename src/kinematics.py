"""
OpenVelo kinematics: crank-phase-resolved joint angle modelling.

Why this exists
---------------
Reading angles frame by frame and taking the max/min (or a per-stroke median)
has three weaknesses on real phone footage:

1. **Left/right label swaps.** Side-on, pose models regularly swap the near
   and far leg for a few frames. A swap turns a 145 deg knee into a 70 deg one
   and pollutes every statistic.
2. **Sampling aliasing.** At 30 fps and 90 rpm there are only ~20 frames per
   revolution, so the true extreme of the knee angle usually falls between
   frames.
3. **Noise at the extremes.** Max/min are the statistics most sensitive to
   keypoint jitter, which is why per-stroke IQRs of 3-4 deg are typical.

The pipeline below fixes all three:

  raw keypoints
    -> Viterbi leg-identity tracking (near vs far limb, temporally consistent)
    -> confidence gating + Hampel outlier rejection + short-gap interpolation
    -> light zero-phase Savitzky-Golay smoothing
    -> robust crank-circle fit on the ball of the foot -> crank angle per frame
    -> phase folding: every frame becomes a sample of angle(theta)
    -> robust (Huber IRLS) Fourier-series regression of each angle on theta
    -> extremes from the fitted curve, 95% CI by bootstrapping whole strokes

Conventions
-----------
Image coordinates (x right, y down), pixels of the analysed frame.
Crank angle theta: 0 = top dead centre (12 o'clock), 90 = forward horizontal
(3 o'clock), 180 = bottom dead centre, increasing in the pedalling direction.
"""
import numpy as np

try:
    from scipy.signal import hilbert, savgol_filter
except Exception:  # scipy is optional; degrade gracefully
    hilbert = savgol_filter = None

SIDE_JOINTS = ['ear', 'shoulder', 'elbow', 'wrist', 'hip', 'knee', 'ankle', 'heel', 'toe']
LEG_GROUP = ['knee', 'ankle', 'heel', 'toe']
ARM_GROUP = ['elbow', 'wrist']
TRACK_JOINTS = SIDE_JOINTS + ['ball']

BALL_OF_FOOT = 0.72      # fraction along heel->toe where the pedal spindle sits
CONF_MIN = 0.35          # keypoints below this confidence are treated as missing
HARMONICS = 4            # Fourier terms per angle curve
THIGH_SHANK_FRACTION_OF_HEIGHT = 0.491  # Winter (2009) anthropometrics


# --------------------------------------------------------------------------- #
# Geometry primitives (vectorised over leading dims)
# --------------------------------------------------------------------------- #
def angle3(a, b, c):
    """Inner angle ABC in degrees."""
    a, b, c = np.asarray(a, float), np.asarray(b, float), np.asarray(c, float)
    v1, v2 = a - b, c - b
    dot = np.sum(v1 * v2, axis=-1)
    n = np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1)
    with np.errstate(invalid='ignore', divide='ignore'):
        cos = np.clip(dot / n, -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def acute_to_horizontal(a, b):
    """Acute angle (0-90) of segment a->b against the horizontal."""
    v = np.asarray(b, float) - np.asarray(a, float)
    return np.degrees(np.arctan2(np.abs(v[..., 1]), np.abs(v[..., 0])))


def reach_for_knee(L1, L2, theta_deg):
    """Hip-ankle distance for a knee angle (law of cosines)."""
    return float(np.sqrt(L1 ** 2 + L2 ** 2 - 2 * L1 * L2 * np.cos(np.radians(theta_deg))))


def knee_for_reach(L1, L2, d):
    """Knee angle for a hip-ankle distance (inverse law of cosines)."""
    cos = (L1 ** 2 + L2 ** 2 - d ** 2) / (2 * L1 * L2)
    return float(np.degrees(np.arccos(np.clip(cos, -1, 1))))


# --------------------------------------------------------------------------- #
# 1. Raw tracks
# --------------------------------------------------------------------------- #
def _raw(frames, name):
    """(N,2) xy, (N,) conf, (N,) z for one keypoint name; NaN where missing."""
    n = len(frames)
    xy = np.full((n, 2), np.nan)
    conf = np.zeros(n)
    z = np.full(n, np.nan)
    for i, f in enumerate(frames):
        v = f.get(name)
        if v is None:
            continue
        xy[i] = v[0], v[1]
        conf[i] = v[2] if len(v) > 2 else 1.0
        if len(v) > 3 and v[3] is not None:
            z[i] = v[3]
    return xy, conf, z


# --------------------------------------------------------------------------- #
# 2. Leg / arm identity tracking (2-state Viterbi)
# --------------------------------------------------------------------------- #
def _group_points(frames, group, prefix):
    pts = np.stack([_raw(frames, f'{prefix}_{j}')[0] for j in group], axis=1)  # (N,G,2)
    conf = np.stack([_raw(frames, f'{prefix}_{j}')[1] for j in group], axis=1)  # (N,G)
    pts[conf < CONF_MIN * 0.5] = np.nan
    return pts, conf


def _mean_dist(a, b):
    d = np.linalg.norm(a - b, axis=-1)
    d = d[np.isfinite(d)]
    return float(d.mean()) if len(d) else 0.0


def viterbi_identity(L, R, scale, swap_prior=0.12):
    """
    Decide per frame whether the model's left/right labels for a limb group are
    swapped, such that the two limb trajectories are as continuous as possible.

    L, R: (N, G, 2) arrays. scale: typical segment length (px) for normalising.
    Returns a boolean array `swapped` (N,).

    Each frame has two states (0 = labels as given, 1 = swapped). The cost of a
    transition is the mean joint displacement of both limbs between frames
    (normalised by `scale`); a small prior penalises the swapped state so that
    the model's own labels win whenever the motion is ambiguous.
    """
    n = len(L)
    if n == 0:
        return np.zeros(0, bool)
    scale = max(scale, 1e-6)
    INF = 1e18
    cost = np.array([0.0, swap_prior])
    back = np.zeros((n, 2), int)
    prev = [(L[0], R[0]), (R[0], L[0])]
    for t in range(1, n):
        cur = [(L[t], R[t]), (R[t], L[t])]
        new = np.full(2, INF)
        for s in range(2):
            a_now, b_now = cur[s]
            for p in range(2):
                a_prev, b_prev = prev[p]
                c = cost[p] + (_mean_dist(a_now, a_prev) + _mean_dist(b_now, b_prev)) / scale
                if c < new[s]:
                    new[s], back[t, s] = c, p
            new[s] += swap_prior if s == 1 else 0.0
        cost = new
        # Carry forward the last tracked position for joints missing this frame
        old = prev
        prev = []
        for s in range(2):
            a_now, b_now = cur[s]
            a_prev, b_prev = old[back[t, s]]
            prev.append((np.where(np.isfinite(a_now), a_now, a_prev),
                         np.where(np.isfinite(b_now), b_now, b_prev)))
    states = np.zeros(n, int)
    states[-1] = int(np.argmin(cost))
    for t in range(n - 1, 0, -1):
        states[t - 1] = back[t, states[t]]
    return states.astype(bool)


def near_side_tracks(frames, facing_side):
    """
    Build identity-consistent near-side tracks for every joint.

    Returns (tracks, conf, info):
      tracks: {joint: (N,2)} raw (unsmoothed) px, NaN where missing
      conf:   {joint: (N,)}
      info:   {'swaps_fixed': int, 'near_side': 'left'|'right'}
    """
    out, conf = {}, {}

    # Thigh length for normalisation (either side)
    thigh = []
    for p in ('left', 'right'):
        h, _, _ = _raw(frames, f'{p}_hip')
        k, _, _ = _raw(frames, f'{p}_knee')
        d = np.linalg.norm(h - k, axis=1)
        thigh.extend(d[np.isfinite(d)])
    scale = float(np.median(thigh)) if thigh else 100.0

    # Which labelled side is nearer the camera?  MediaPipe gives a relative
    # depth (z, smaller = closer) which is direct evidence; otherwise fall back
    # to the facing rule (a rider facing image-left shows their left side).
    def _zmed(name):
        z = _raw(frames, name)[2]
        z = z[np.isfinite(z)]
        return float(np.median(z)) if len(z) else np.nan

    zl, zr = _zmed('left_hip'), _zmed('right_hip')
    near = facing_side
    if np.isfinite(zl) and np.isfinite(zr) and abs(zl - zr) > 0.02:
        near = 'left' if zl < zr else 'right'
    far = 'right' if near == 'left' else 'left'

    swaps_total = 0
    for group in (LEG_GROUP, ARM_GROUP):
        Ln, Lc = _group_points(frames, group, near)
        Rn, Rc = _group_points(frames, group, far)
        swapped = viterbi_identity(Ln, Rn, scale)
        swaps_total += int(swapped.sum())
        for gi, j in enumerate(group):
            xy = np.where(swapped[:, None], Rn[:, gi], Ln[:, gi])
            c = np.where(swapped, Rc[:, gi], Lc[:, gi])
            out[j], conf[j] = xy, c

    for j in ('ear', 'shoulder', 'hip'):
        xy, c, _ = _raw(frames, f'{near}_{j}')
        xy2, c2, _ = _raw(frames, f'{far}_{j}')
        # Use the far-side point only when the near one is missing
        use_far = (~np.all(np.isfinite(xy), axis=1)) & np.all(np.isfinite(xy2), axis=1)
        out[j] = np.where(use_far[:, None], xy2, xy)
        conf[j] = np.where(use_far, c2 * 0.8, c)

    return out, conf, {'swaps_fixed': swaps_total, 'near_side': near}


# --------------------------------------------------------------------------- #
# 3. Cleaning
# --------------------------------------------------------------------------- #
def hampel(arr, half_window=4, n_sigma=3.0):
    """Mark samples further than n_sigma robust SDs from the local median as NaN."""
    arr = np.array(arr, float)
    out = arr.copy()
    n = len(arr)
    for i in range(n):
        lo, hi = max(0, i - half_window), min(n, i + half_window + 1)
        w = arr[lo:hi]
        w = w[np.isfinite(w)]
        if len(w) < 3 or not np.isfinite(arr[i]):
            continue
        med = np.median(w)
        mad = 1.4826 * np.median(np.abs(w - med))
        if mad > 0 and abs(arr[i] - med) > n_sigma * mad:
            out[i] = np.nan
    return out


def fill_gaps(arr, max_gap):
    """Linear interpolation across interior NaN runs up to max_gap samples."""
    arr = np.array(arr, float)
    one_d = arr.ndim == 1
    if one_d:
        arr = arr[:, None]
    n = len(arr)
    idx = np.arange(n)
    for d in range(arr.shape[1]):
        col = arr[:, d]
        good = np.isfinite(col)
        if good.sum() < 2:
            continue
        filled = np.interp(idx, idx[good], col[good])
        i = 0
        while i < n:
            if good[i]:
                i += 1
                continue
            j = i
            while j < n and not good[j]:
                j += 1
            if i > 0 and j < n and (j - i) <= max_gap:
                col[i:j] = filled[i:j]
            i = j
        arr[:, d] = col
    return arr[:, 0] if one_d else arr


def smooth(arr, fps, window_s=0.12, poly=2):
    """Zero-phase Savitzky-Golay smoothing over each finite stretch."""
    arr = np.array(arr, float)
    win = int(round(window_s * fps)) | 1
    if savgol_filter is None or win < poly + 2:
        return arr
    finite = np.all(np.isfinite(arr), axis=-1) if arr.ndim > 1 else np.isfinite(arr)
    out = arr.copy()
    i, n = 0, len(arr)
    while i < n:
        if not finite[i]:
            i += 1
            continue
        j = i
        while j < n and finite[j]:
            j += 1
        if j - i >= win:
            out[i:j] = savgol_filter(arr[i:j], win, poly, axis=0)
        i = j
    return out


def clean_tracks(raw, conf, fps):
    tracks = {}
    gap = max(1, int(round(0.2 * fps)))
    for j, xy in raw.items():
        xy = xy.copy()
        xy[conf[j] < CONF_MIN] = np.nan
        for d in range(2):
            xy[:, d] = hampel(xy[:, d], half_window=max(3, int(0.15 * fps)))
        # a joint is valid only if both coordinates survived
        bad = ~np.all(np.isfinite(xy), axis=1)
        xy[bad] = np.nan
        xy = fill_gaps(xy, gap)
        tracks[j] = smooth(xy, fps)
    tracks['ball'] = tracks['heel'] + BALL_OF_FOOT * (tracks['toe'] - tracks['heel'])
    return tracks


# --------------------------------------------------------------------------- #
# 4. Crank geometry & phase
# --------------------------------------------------------------------------- #
def fit_circle_kasa(pts):
    """Algebraic least-squares circle fit. Returns (cx, cy, r) or None."""
    pts = np.asarray(pts, float)
    pts = pts[np.all(np.isfinite(pts), axis=1)]
    if len(pts) < 8:
        return None
    x, y = pts[:, 0], pts[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    try:
        sol, *_ = np.linalg.lstsq(A, x ** 2 + y ** 2, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = sol[0] / 2, sol[1] / 2
    r = np.sqrt(max(sol[2] + cx ** 2 + cy ** 2, 0))
    return float(cx), float(cy), float(r)


def fit_circle_robust(pts, iters=15):
    """
    Kasa initialisation followed by Huber-weighted geometric (orthogonal
    distance) Gauss-Newton refinement. Returns dict or None.
    """
    pts = np.asarray(pts, float)
    pts = pts[np.all(np.isfinite(pts), axis=1)]
    init = fit_circle_kasa(pts)
    if init is None:
        return None
    # A full pedal circle has its centre at the coordinate-wise median, which a
    # few wild points can't drag. Seed from whichever start fits the bulk better.
    mc = np.median(pts, axis=0)
    med = (float(mc[0]), float(mc[1]), float(np.median(np.hypot(*(pts - mc).T))))

    def _mad(c):
        return np.median(np.abs(np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1]) - c[2]))

    init = min((init, med), key=_mad)
    # Trimmed re-fits so gross outliers (mis-tracked feet) can't bias the start point
    keep = np.ones(len(pts), bool)
    for _ in range(4):
        cx, cy, r = init
        res = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r
        mad = 1.4826 * np.median(np.abs(res - np.median(res))) or 1e-6
        new_keep = np.abs(res - np.median(res)) <= 3 * mad
        if new_keep.sum() < 8 or (new_keep == keep).all():
            break
        keep = new_keep
        init = fit_circle_kasa(pts[keep]) or init
    cx, cy, r = init
    for _ in range(iters):
        dx, dy = pts[:, 0] - cx, pts[:, 1] - cy
        d = np.hypot(dx, dy)
        d[d == 0] = 1e-9
        res = d - r
        s = 1.4826 * np.median(np.abs(res - np.median(res))) or 1.0
        w = np.minimum(1.0, 1.345 * s / np.maximum(np.abs(res), 1e-9))
        J = np.column_stack([-dx / d, -dy / d, -np.ones_like(d)])
        W = np.sqrt(w)[:, None]
        try:
            step, *_ = np.linalg.lstsq(J * W, -res * W[:, 0], rcond=None)
        except np.linalg.LinAlgError:
            break
        cx, cy, r = cx + step[0], cy + step[1], r + step[2]
        if np.linalg.norm(step) < 1e-3:
            break
    d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    rms = float(np.sqrt(np.mean((d - r) ** 2)))
    ang = np.degrees(np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx))
    coverage = len(np.unique(((ang + 180) // 30).astype(int))) / 12.0
    return {'cx': float(cx), 'cy': float(cy), 'r': float(abs(r)), 'rms': rms, 'coverage': coverage}


def crank_angle(pts, cx, cy, fwd):
    """Crank angle (deg, 0 = TDC, 90 = forward) of points around (cx, cy)."""
    x = (pts[:, 0] - cx) * fwd
    y = pts[:, 1] - cy
    return np.degrees(np.arctan2(x, -y)) % 360.0


def _unwrap_deg(theta):
    out = np.full_like(theta, np.nan)
    good = np.isfinite(theta)
    if good.sum() < 2:
        return out
    out[good] = np.degrees(np.unwrap(np.radians(theta[good])))
    return out


def smooth_phase(theta, fps, window_s=0.35):
    """
    Remove per-frame jitter from a crank-angle series. Cadence is nearly constant
    within a stroke, so the unwrapped angle is a smooth ramp: smoothing it keeps
    the true crank-angle anchoring of the circle fit without the foot-keypoint noise.
    """
    unw = _unwrap_deg(theta)
    good = np.isfinite(unw)
    if good.sum() < 10:
        return theta
    win = int(round(window_s * fps)) | 1
    if savgol_filter is None or win < 5:
        return theta
    idx = np.arange(len(unw))
    filled = np.interp(idx, idx[good], unw[good])
    sm = savgol_filter(filled, win, 2)
    out = sm % 360.0
    out[~good] = np.nan
    return out


def hilbert_phase(sig):
    """Instantaneous phase (deg, 0 at signal peaks) of a quasi-periodic signal."""
    if hilbert is None:
        return None
    s = np.asarray(sig, float)
    good = np.isfinite(s)
    if good.sum() < 20:
        return None
    idx = np.arange(len(s))
    s = np.interp(idx, idx[good], s[good])
    s = s - np.median(s)
    ph = np.degrees(np.angle(hilbert(s))) % 360.0
    ph[~good] = np.nan
    return ph


# --------------------------------------------------------------------------- #
# 5. Robust Fourier-series regression on crank angle
# --------------------------------------------------------------------------- #
def _quiet(fn):
    """numpy + Apple Accelerate emits spurious matmul FP warnings; results are unaffected."""
    def wrapper(*a, **kw):
        with np.errstate(all='ignore'):
            return fn(*a, **kw)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


def _design(theta_deg, K=HARMONICS):
    th = np.radians(np.asarray(theta_deg, float))
    cols = [np.ones_like(th)]
    for k in range(1, K + 1):
        cols += [np.cos(k * th), np.sin(k * th)]
    return np.column_stack(cols)


@_quiet
def harmonic_fit(theta, y, K=HARMONICS, iters=12, weights=None):
    """
    Fit y(theta) = a0 + sum_k a_k cos(k theta) + b_k sin(k theta) with Huber IRLS.
    Returns dict(coef, resid_sd, r2, n) or None.
    """
    theta, y = np.asarray(theta, float), np.asarray(y, float)
    m = np.isfinite(theta) & np.isfinite(y)
    if m.sum() < 4 * K + 4:
        return None
    th, yy = theta[m], y[m]
    X = _design(th, K)
    base_w = np.ones(len(yy)) if weights is None else np.asarray(weights, float)[m]
    w = base_w.copy()
    coef = None
    for _ in range(iters):
        sw = np.sqrt(w)[:, None]
        try:
            coef, *_ = np.linalg.lstsq(X * sw, yy * sw[:, 0], rcond=None)
        except np.linalg.LinAlgError:
            return None
        r = yy - X @ coef
        s = 1.4826 * np.median(np.abs(r - np.median(r))) or 1e-6
        w = base_w * np.minimum(1.0, 1.345 * s / np.maximum(np.abs(r), 1e-9))
    r = yy - X @ coef
    ss_tot = np.sum((yy - yy.mean()) ** 2) or 1e-9
    return {'coef': coef, 'resid_sd': float(1.4826 * np.median(np.abs(r))),
            'r2': float(1 - np.sum(r ** 2) / ss_tot), 'n': int(m.sum())}


@_quiet
def eval_harmonic(coef, theta_deg):
    K = (len(coef) - 1) // 2
    return _design(theta_deg, K) @ coef


GRID = np.arange(0, 360, 1.0)


def curve_stats(coef):
    c = eval_harmonic(coef, GRID)
    i_max, i_min = int(np.argmax(c)), int(np.argmin(c))
    return {'max': float(c[i_max]), 'max_at': float(GRID[i_max]),
            'min': float(c[i_min]), 'min_at': float(GRID[i_min]),
            'mean': float(np.mean(c)), 'ptp': float(c[i_max] - c[i_min])}


@_quiet
def bootstrap_ci(theta, y, stroke_id, stat_fn, B=200, K=HARMONICS, seed=7):
    """95% CI of stat_fn(coef) by resampling whole pedal strokes."""
    m = np.isfinite(theta) & np.isfinite(y) & (stroke_id >= 0)
    ids = np.unique(stroke_id[m])
    if len(ids) < 4:
        return None
    rng = np.random.default_rng(seed)
    X_all = _design(theta[m], K)
    y_all = y[m]
    sid = stroke_id[m]
    rows_by_stroke = {s: np.where(sid == s)[0] for s in ids}
    vals = []
    for _ in range(B):
        pick = rng.choice(ids, size=len(ids), replace=True)
        rows = np.concatenate([rows_by_stroke[s] for s in pick])
        try:
            coef, *_ = np.linalg.lstsq(X_all[rows], y_all[rows], rcond=None)
        except np.linalg.LinAlgError:
            continue
        vals.append(stat_fn(coef))
    if len(vals) < 20:
        return None
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return [float(lo), float(hi)]


# --------------------------------------------------------------------------- #
# 6. Angle series
# --------------------------------------------------------------------------- #
def angle_series(t):
    s = {
        'knee': angle3(t['hip'], t['knee'], t['ankle']),
        'hip': angle3(t['shoulder'], t['hip'], t['knee']),
        'torso': acute_to_horizontal(t['hip'], t['shoulder']),
        'shoulder': angle3(t['hip'], t['shoulder'], t['elbow']),
        'elbow': angle3(t['shoulder'], t['elbow'], t['wrist']),
        'ankle': angle3(t['knee'], t['ankle'], t['toe']),
    }
    v = t['toe'] - t['heel']
    s['foot'] = np.degrees(np.arctan2(v[:, 1], np.abs(v[:, 0])))  # + = toe down
    return s


# --------------------------------------------------------------------------- #
# 7. Main entry point
# --------------------------------------------------------------------------- #
@_quiet
def analyze(frames, times, facing_side, crank_mm=172.5, rider_height_cm=None):
    """
    frames: list of per-frame raw keypoint dicts {name: (x, y, conf[, z])}
    times:  seconds for each frame
    Returns a JSON-friendly dict (numpy arrays only under 'tracks'/'angles').
    """
    times = np.asarray(times, float)
    n = len(frames)
    fps = (n - 1) / (times[-1] - times[0]) if n > 1 and times[-1] > times[0] else 30.0
    out = {'fps': float(fps), 'method': 'percentile', 'quality': {}, 'derived': {},
           'metrics': {}, 'detail': {}, 'curves': {}, 'events': {}}

    raw, conf, info = near_side_tracks(frames, facing_side)
    out['quality']['swaps_fixed'] = info['swaps_fixed']
    out['near_side'] = info['near_side']
    tracks = clean_tracks(raw, conf, fps)
    ang = angle_series(tracks)
    out['tracks'], out['angles'], out['times'] = tracks, ang, times

    knee = ang['knee']
    if np.isfinite(knee).sum() < 10:
        return out

    # Segment lengths and pixel scale
    L1 = float(np.nanmedian(np.linalg.norm(tracks['hip'] - tracks['knee'], axis=1)))
    L2 = float(np.nanmedian(np.linalg.norm(tracks['knee'] - tracks['ankle'], axis=1)))
    d = out['derived']
    d['thigh_px'], d['shank_px'] = L1, L2

    # ---------------- Crank phase ---------------- #
    fwd = 1.0 if facing_side == 'right' else -1.0
    circ = fit_circle_robust(tracks['ball'])
    theta = None
    mm_per_px, scale_source = None, None
    if circ:
        # Phase only needs the circle's centre, so accept it generously...
        phase_ok = (0.2 * L2 < circ['r'] < 0.8 * L2 and circ['rms'] < 0.3 * circ['r']
                    and circ['coverage'] >= 0.75)
        circ['ok'] = bool(phase_ok)
        d['crank_fit'] = circ
        if phase_ok:
            theta = crank_angle(tracks['ball'], circ['cx'], circ['cy'], fwd)
            unw = _unwrap_deg(theta)
            steps = np.diff(unw[np.isfinite(unw)])
            if len(steps) and np.median(steps) < 0:
                # Pedalling runs backwards in our convention -> facing was misjudged
                fwd = -fwd
                theta = crank_angle(tracks['ball'], circ['cx'], circ['cy'], fwd)
                out['quality']['facing_corrected'] = True
            out['phase_method'] = 'crank-circle'
            theta = smooth_phase(theta, fps)
            # ...but the radius drives the mm scale, and motion blur on the shoe
            # shrinks it. Only trust it when the implied thigh is anatomical.
            cand = crank_mm / circ['r']
            if circ['rms'] < 0.22 * circ['r'] and 330 <= L1 * cand <= 560:
                mm_per_px = cand
                scale_source = f'crank circle ({crank_mm:g} mm)'
    # Second phase estimate: Hilbert phase of leg extension (hip-ankle reach).
    reach = np.linalg.norm(tracks['hip'] - tracks['ankle'], axis=1)
    ph = hilbert_phase(reach)
    if ph is not None:
        if theta is not None:
            # Anchor it to true crank angle with the circular-mean offset, then
            # keep whichever phase explains the knee signal with less scatter.
            diff = np.radians(theta - ph)
            ok = np.isfinite(diff)
            offset = np.degrees(np.arctan2(np.sin(diff[ok]).mean(), np.cos(diff[ok]).mean()))
            cand = (ph + offset) % 360.0
            f_circ, f_hil = harmonic_fit(theta, knee), harmonic_fit(cand, knee)
            if f_circ and f_hil and f_hil['resid_sd'] < 0.9 * f_circ['resid_sd']:
                theta = cand
                out['phase_method'] = 'hilbert (crank-anchored)'
        else:
            # No usable circle: peaks of leg reach sit close to bottom dead centre
            theta = (ph + 180.0) % 360.0
            out['phase_method'] = 'hilbert'
    if rider_height_cm:
        # Rider height (anthropometric leg length) is the more reliable reference
        mm_per_px = (rider_height_cm * 10 * THIGH_SHANK_FRACTION_OF_HEIGHT) / (L1 + L2)
        scale_source = f'rider height ({rider_height_cm:g} cm)'
    d['mm_per_px'], d['scale_source'] = mm_per_px, scale_source
    d['facing'] = 'right' if fwd > 0 else 'left'

    if theta is None:
        return out

    unw = _unwrap_deg(theta)
    out['theta'] = theta
    finite = np.isfinite(unw)
    # Strokes: integer revolutions of the unwrapped crank angle
    stroke_id = np.full(n, -1)
    if finite.sum() > 2:
        start = np.nanmin(unw)
        stroke_id[finite] = np.floor((unw[finite] - start) / 360.0).astype(int)
        counts = np.bincount(stroke_id[finite])
        complete = {i for i, c in enumerate(counts) if c >= 0.6 * fps * 60 / 140}  # >= ~60% of a 140 rpm stroke
        stroke_id = np.where(np.isin(stroke_id, list(complete)), stroke_id, -1)
        # Cadence from the slope of unwrapped crank angle vs time
        A = np.column_stack([times[finite], np.ones(finite.sum())])
        slope = np.linalg.lstsq(A, unw[finite], rcond=None)[0][0]
        d['cadence_rpm'] = float(abs(slope) / 360.0 * 60.0)
    n_strokes = int(len(set(stroke_id[stroke_id >= 0])))
    d['strokes'] = n_strokes
    out['stroke_id'] = stroke_id

    # ---------------- Harmonic models ---------------- #
    fits = {}
    for k in ('knee', 'hip', 'ankle', 'torso', 'shoulder', 'elbow', 'foot'):
        fits[k] = harmonic_fit(theta, ang[k])
    if fits['knee'] is None or n_strokes < 2:
        return out
    out['method'] = 'harmonic'

    stats = {k: curve_stats(f['coef']) for k, f in fits.items() if f}
    curve_grid = np.arange(0, 360, 5.0)
    out['curves'] = {k: [round(float(v), 2) for v in eval_harmonic(f['coef'], curve_grid)]
                     for k, f in fits.items() if f}
    out['curves']['theta'] = curve_grid.tolist()
    out['fit_quality'] = {k: {'r2': f['r2'], 'resid_sd': f['resid_sd'], 'n': f['n']}
                          for k, f in fits.items() if f}

    def ci(key, fn):
        return bootstrap_ci(theta, ang[key], stroke_id, fn)

    mx = lambda c: float(np.max(eval_harmonic(c, GRID)))      # noqa: E731
    mn = lambda c: float(np.min(eval_harmonic(c, GRID)))      # noqa: E731
    me = lambda c: float(c[0])                                 # noqa: E731
    pp = lambda c: float(np.ptp(eval_harmonic(c, GRID)))      # noqa: E731

    spec = {
        'knee_ext':   ('knee', 'max', mx),
        'knee_flex':  ('knee', 'min', mn),
        'hip_closed': ('hip', 'min', mn),
        'hip_open':   ('hip', 'max', mx),
        'torso':      ('torso', 'mean', me),
        'shoulder':   ('shoulder', 'mean', me),
        'elbow':      ('elbow', 'mean', me),
        'ankle_range': ('ankle', 'ptp', pp),
    }
    for name, (src, stat, fn) in spec.items():
        if src not in stats:
            continue
        val = stats[src][stat]
        out['metrics'][name] = val
        out['detail'][name] = {
            'value': val, 'ci': ci(src, fn), 'method': 'harmonic',
            'at_deg': stats[src].get(f'{stat}_at') if stat in ('max', 'min') else None,
            'resid_sd': fits[src]['resid_sd'], 'strokes': n_strokes,
        }
    out['events'] = {'knee_ext_at': stats['knee']['max_at'], 'knee_flex_at': stats['knee']['min_at'],
                     'knee_at_bdc': float(eval_harmonic(fits['knee']['coef'], [180.0])[0])}

    # Per-stroke cross-check (classic method) for transparency
    per = {'knee_ext': [], 'knee_flex': []}
    for s in sorted(set(stroke_id[stroke_id >= 0])):
        seg = knee[stroke_id == s]
        if np.isfinite(seg).sum() >= 3:
            per['knee_ext'].append(np.nanmax(seg))
            per['knee_flex'].append(np.nanmin(seg))
    out['per_stroke'] = {k: {'median': float(np.median(v)),
                             'iqr': float(np.subtract(*np.percentile(v, [75, 25]))), 'n': len(v)}
                         for k, v in per.items() if v}

    # ---------------- Derived fit quantities ---------------- #
    if mm_per_px:
        # KOPS: knee ahead (+) / behind (-) the pedal spindle at 3 o'clock
        kx = (tracks['knee'][:, 0] - tracks['ball'][:, 0]) * fwd
        f = harmonic_fit(theta, kx)
        if f:
            d['kops_mm'] = float(eval_harmonic(f['coef'], [90.0])[0] * mm_per_px)
        # Hip rock: peak-to-peak of the *periodic* part of hip height
        f = harmonic_fit(theta, tracks['hip'][:, 1])
        if f:
            d['hip_rock_mm'] = float(np.ptp(eval_harmonic(f['coef'], GRID)) * mm_per_px)
        f = harmonic_fit(theta, tracks['hip'][:, 0] * fwd)
        if f:
            d['hip_sway_mm'] = float(np.ptp(eval_harmonic(f['coef'], GRID)) * mm_per_px)
        d['thigh_mm'], d['shank_mm'] = L1 * mm_per_px, L2 * mm_per_px

    return out


def saddle_change_mm(knee_now, knee_target, thigh_px, shank_px, mm_per_px):
    """Geometric saddle height change (mm, + = raise) to move peak knee angle to target."""
    if not (knee_now and thigh_px and shank_px and mm_per_px):
        return None
    d_now = reach_for_knee(thigh_px, shank_px, knee_now)
    d_tgt = reach_for_knee(thigh_px, shank_px, knee_target)
    return (d_tgt - d_now) * mm_per_px
