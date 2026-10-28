"""
Side-by-side analysis video: the ride with skeleton, back contour and pedal box
on the left, and a live panel on the right that fills in as the clip plays
(hip/shoulder displacement, back roundness map, average back shape per stroke,
stroke timing and the current stroke's back verdict).

Built only from a stored fit result and its source video, so any saved fit can
be exported.
"""
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from src import engine
from src import posture as po

OUT_H = 1280          # output height; the panel is 720 wide (9:16)
PANEL_W = 720
_FONT_PATHS = ('/System/Library/Fonts/HelveticaNeue.ttc', '/System/Library/Fonts/Helvetica.ttc',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'C:/Windows/Fonts/segoeui.ttf')
_fonts = {}


def _font(size):
    if size not in _fonts:
        for p in _FONT_PATHS:
            if os.path.exists(p):
                _fonts[size] = ImageFont.truetype(p, size)
                break
        else:
            _fonts[size] = ImageFont.load_default(size)
    return _fonts[size]


def _rgb(bgr):
    return tuple(int(c) for c in bgr[::-1])


class _Panel:
    """Static chart frames drawn once; dynamic marks drawn per frame."""

    def __init__(self, post, duration):
        self.post, self.dur = post, max(duration, 1e-3)
        self.boxes = {'disp': (90, 80, 690, 370), 'heat': (100, 500, 640, 720), 'shape': (100, 840, 690, 1050)}
        self.base = np.full((OUT_H, PANEL_W, 3), po._BG, np.uint8)
        self.texts = []  # static text, (xy, text, size, rgb, anchor)
        self.lim = max(1.0, post['thresholds'][1] * 1.4)
        self._static()
        x0, y0, x1, y1 = self.boxes['heat']
        cols = post['heat']['cols']
        n = len(post['stations'])
        heat = np.zeros((n, len(cols), 3), np.uint8)
        heat[:] = (26, 25, 24)
        for c, col in enumerate(cols):
            for s, v in enumerate(col):
                if v is not None:
                    heat[n - 1 - s, c] = po.diverging_bgr(v, self.lim)
        self.heat = cv2.resize(heat, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        disp = post.get('displacement') or {}
        vals = [v for k in ('hip', 'shoulder') for v in disp.get(k, []) if v is not None]
        top = max(vals) if vals else 1.0
        self.disp_hi = 4 * next((s for s in (1, 2, 5, 10, 20, 50, 100) if 4 * s >= top), int(np.ceil(top / 4)))
        self._disp_ticks()

    def x(self, t, box):
        return int(box[0] + min(max(t, 0), self.dur) / self.dur * (box[2] - box[0]))

    def _static(self):
        T = self.texts
        for key, title in (('disp', 'Hip & Shoulder Displacement'), ('heat', 'Back Roundness Map'),
                           ('shape', 'Average Back Shape')):
            x0, y0, x1, y1 = self.boxes[key]
            cv2.rectangle(self.base, (x0, y0), (x1, y1), (26, 25, 24), -1)
            T.append(((PANEL_W // 2, y0 - 38), title, 26, (236, 236, 232), 'mt'))
            step = next(s for s in (1, 2, 5, 10, 15, 30, 60) if self.dur / s <= 6)
            for t in np.arange(0, self.dur + 1e-6, step):
                xx = self.x(t, (x0, y0, x1, y1))
                cv2.line(self.base, (xx, y1), (xx, y1 + 5), po._MUTED, 1)
                T.append(((xx, y1 + 10), f"{t:.0f}", 16, _rgb(po._MUTED), 'mt'))
            T.append((((x0 + x1) // 2, y1 + 32), 'Time (s)', 16, _rgb(po._MUTED), 'mt'))
            if key != 'disp':
                T.append(((x0 - 8, y0 + 2), 'Top', 15, _rgb(po._MUTED), 'rt'))
                T.append(((x0 - 8, y1), 'Bottom', 15, _rgb(po._MUTED), 'rb'))
        x0, y0, x1, y1 = self.boxes['heat']
        for k in range(60):  # colour bar
            v = self.lim - 2 * self.lim * k / 59
            ya = int(y0 + k / 60 * (y1 - y0))
            cv2.rectangle(self.base, (x1 + 18, ya), (x1 + 32, int(y0 + (k + 1) / 60 * (y1 - y0))),
                          po.diverging_bgr(v, self.lim), -1)
        T.append(((x1 + 25, y0 - 4), f"+{self.lim:.0f}%", 15, _rgb(po._MUTED), 'mb'))
        T.append(((x1 + 25, y1 + 4), f"-{self.lim:.0f}%", 15, _rgb(po._MUTED), 'mt'))

    def _disp_ticks(self):
        x0, y0, x1, y1 = self.boxes['disp']
        for k in range(5):
            yy = int(y1 - k / 4 * (y1 - y0))
            cv2.line(self.base, (x0, yy), (x1, yy), po._GRID, 1)
            self.texts.append(((x0 - 8, yy), f"{self.disp_hi * k / 4:.0f}", 16, _rgb(po._MUTED), 'rm'))
        unit = (self.post.get('displacement') or {}).get('unit', 'px')
        self.texts.append(((x0 - 8, y0 - 14), f"Rise ({unit})", 15, _rgb(po._MUTED), 'rb'))
        for i, (name, col) in enumerate((('Hip', po._HIP_BGR), ('Shoulder', po._SH_BGR))):
            lx = x1 - 210 + i * 90
            cv2.line(self.base, (lx, y0 + 20), (lx + 22, y0 + 20), col, 3, cv2.LINE_AA)
            self.texts.append(((lx + 28, y0 + 20), name, 16, (236, 236, 232), 'lm'))

    def draw(self, t):
        """Panel image and its text items at time t (seconds from clip start)."""
        img = self.base.copy()
        texts = list(self.texts)
        post = self.post
        t_rel = t - post['heat']['t0']

        # Displacement traces up to now
        disp = post.get('displacement')
        if disp:
            box = self.boxes['disp']
            for key, col in (('shoulder', po._SH_BGR), ('hip', po._HIP_BGR)):
                pts = [(self.x(tt - post['heat']['t0'], box), int(box[3] - v / self.disp_hi * (box[3] - box[1])))
                       for tt, v in zip(disp['t'], disp[key]) if v is not None and tt <= t]
                if len(pts) > 1:
                    cv2.polylines(img, [np.array(pts, np.int32)], False, col, 3, cv2.LINE_AA)
                    cv2.circle(img, pts[-1], 6, col, -1, cv2.LINE_AA)

        # Roundness map revealed up to now
        x0, y0, x1, y1 = self.boxes['heat']
        w = self.x(t_rel, self.boxes['heat']) - x0
        if w > 0:
            img[y0:y1, x0:x0 + w] = self.heat[:, :w]

        # Back shape of every completed stroke
        strokes = post['strokes'] or []
        done = [s for s in strokes if s['t1'] <= t]
        box = self.boxes['shape']
        n = len(post['stations'])
        slot = (box[2] - box[0]) / max(len(strokes), 8)
        for s in done:
            cx = self.x((s['t0'] + s['t1']) / 2 - post['heat']['t0'], box)
            col = po._STATUS_BGR[po.VERDICTS[s['verdict']][0]] if s.get('verdict') else po._MUTED
            pts = [(int(cx + v / self.lim * slot * 0.9), int(box[3] - 8 - i / (n - 1) * (box[3] - box[1] - 16)))
                   for i, v in enumerate(s['shape']) if v is not None]
            if len(pts) > 1:
                cv2.polylines(img, [np.array(pts, np.int32)], False, col, 3, cv2.LINE_AA)
            if len(strokes) <= 16:
                texts.append(((cx + 8, box[3] - 10), str(s['n']), 18, (236, 236, 232), 'lb'))
        cur_x = self.x(t_rel, box)
        cv2.line(img, (cur_x, box[1]), (cur_x, box[3]), po._GRID, 1)

        # Stroke timing from the whole strokes completed so far
        y = 1124
        whole = [s for s in done if s['duration'] >= 0.8 * np.median([x['duration'] for x in strokes])]
        if whole:
            d = [s['duration'] for s in whole]
            fast, slow = whole[int(np.argmin(d))], whole[int(np.argmax(d))]
            lines = [f"Avg stroke time: {np.mean(d):.2f}s",
                     f"Fastest: Stroke {fast['n']} - {fast['duration']:.2f}s",
                     f"Slowest: Stroke {slow['n']} - {slow['duration']:.2f}s"]
        else:
            lines = ["Avg stroke time: -", "Fastest: -", "Slowest: -"]
        lines.append(f"Back curve: {post['roundness']:.1f}% overall")
        for i, s in enumerate(lines):
            texts.append(((40, y + i * 42), s, 22, (236, 236, 232), 'lm'))

        # Current (or last) stroke verdict
        cur = next((s for s in strokes if s['t0'] <= t <= s['t1']), done[-1] if done else None)
        if cur and cur.get('verdict'):
            status, label = po.VERDICTS[cur['verdict']]
            col = po._STATUS_BGR[status]
            cx, cy = 560, 1170
            texts.append(((cx, cy - 58), f"Stroke {cur['n']}", 26, (236, 236, 232), 'mb'))
            cv2.circle(img, (cx, cy), 34, col, -1, cv2.LINE_AA)
            if status == 'ok':
                cv2.polylines(img, [np.array([(cx - 15, cy), (cx - 4, cy + 12), (cx + 16, cy - 12)], np.int32)],
                              False, (255, 255, 255), 6, cv2.LINE_AA)
            elif status == 'watch':
                cv2.line(img, (cx, cy - 17), (cx, cy + 5), (255, 255, 255), 7, cv2.LINE_AA)
                cv2.circle(img, (cx, cy + 16), 4, (255, 255, 255), -1, cv2.LINE_AA)
            else:
                for a, b in (((-13, -13), (13, 13)), ((-13, 13), (13, -13))):
                    cv2.line(img, (cx + a[0], cy + a[1]), (cx + b[0], cy + b[1]), (255, 255, 255), 6, cv2.LINE_AA)
            texts.append(((cx, cy + 50), label, 26, _rgb(col), 'mt'))
        return img, texts


def _left_frame(frame, track, i, scale):
    """Source frame with skeleton, angles, back contour and pedal box."""
    img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    h, w = img.shape[:2]
    P = lambda p: np.array([p[0] * w, p[1] * h])  # noqa: E731
    lm = {k: P(v[i]) for k, v in track['joints'].items() if v[i] is not None}
    ang = {k: track['angles'][src][i] for k, src in
           (('knee', 'knee'), ('hip', 'hip'), ('elbow', 'elbow'), ('arm_torso', 'shoulder'))
           if track['angles'][src][i] is not None}
    back = None
    if track.get('back') and track['back'][i] is not None and track['back_index'][i] is not None:
        back = (np.array([P(p) for p in track['back'][i]]), track['back_index'][i])
    engine.draw_overlay(img, lm, ang, back=back)
    foot = [lm[k] for k in ('ankle', 'heel', 'toe', 'ball') if k in lm]
    if len(foot) >= 3:
        pad = int(14 * max(1.0, h / 900))
        (xa, ya), (xb, yb) = np.min(foot, 0).astype(int) - pad, np.max(foot, 0).astype(int) + pad
        cv2.rectangle(img, (xa, ya), (xb, yb), (70, 60, 230), 3, cv2.LINE_AA)
    return img


def _content_box(cap):
    """(x0, y0, x1, y1) of the picture inside any black letterbox/pillarbox bars."""
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    frames = []
    for k in (0.25, 0.5, 0.75):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * k))
        ok, f = cap.read()
        if ok:
            frames.append(f.max(axis=2))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    if not frames:
        return None
    lit = np.max(frames, axis=0) > 24
    xs, ys = np.where(lit.any(axis=0))[0], np.where(lit.any(axis=1))[0]
    if not len(xs) or not len(ys):
        return None
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def _writer(path, size, fps):
    """H.264 via imageio-ffmpeg when installed (plays everywhere), else OpenCV mp4v."""
    try:
        import imageio_ffmpeg
        gen = imageio_ffmpeg.write_frames(path, size, fps=fps, codec='libx264', pix_fmt_out='yuv420p',
                                          quality=7, macro_block_size=2, ffmpeg_log_level='error')
        gen.send(None)
        return lambda img: gen.send(np.ascontiguousarray(img[:, :, ::-1])), gen.close
    except ImportError:
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, size)
        return vw.write, vw.release


def render_analysis_video(result, video_path, out_path, progress_cb=None):
    """Write the side-by-side analysis MP4 for a stored video fit. Returns out_path."""
    track, post = result.get('track'), result.get('posture')
    if result.get('mode') != 'video' or not track or not (post or {}).get('available'):
        raise engine.AnalysisError("The analysis video needs a video fit with back & posture data "
                                   "(MediaPipe engine, OpenVelo 3.0 or later).")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise engine.AnalysisError("The original video is no longer available.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    sw, sh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x0, y0, x1, y1 = _content_box(cap) or (0, 0, sw, sh)
    scale = OUT_H / (y1 - y0)
    left_w = int(round((x1 - x0) * scale / 2)) * 2
    times = np.asarray(track['t'])
    panel = _Panel(post, post['heat']['t1'] - post['heat']['t0'])
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    write, close = _writer(out_path, (left_w + PANEL_W, OUT_H), fps)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    n = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = n / fps
            n += 1
            if t > times[-1] + 0.5:
                break
            i = int(np.clip(np.searchsorted(times, t), 0, len(times) - 1))
            left = _left_frame(frame, track, i, scale)[int(y0 * scale):int(y1 * scale), int(x0 * scale):]
            left = cv2.resize(left[:, :left_w], (left_w, OUT_H))
            right, texts = panel.draw(t)
            out = Image.fromarray(np.hstack([left, right])[:, :, ::-1])
            d = ImageDraw.Draw(out)
            for (x, y), s, size, col, anchor in texts:
                d.text((x + left_w, y), s, font=_font(size), fill=col, anchor=anchor)
            write(np.asarray(out)[:, :, ::-1])
            if progress_cb and n % 15 == 0:
                progress_cb(min(1.0, n / total))
    finally:
        cap.release()
        close()
    return out_path
