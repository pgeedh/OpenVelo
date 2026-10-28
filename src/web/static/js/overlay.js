// Draws the tracked skeleton, joint angles and crank geometry onto a canvas.
// Coordinates arrive normalised (0–1) from the backend's per-frame track.
import { cssVar } from "./util.js";

const BONES = [
  ["ear", "shoulder"], ["shoulder", "elbow"], ["elbow", "wrist"], ["shoulder", "hip"],
  ["hip", "knee"], ["knee", "ankle"], ["ankle", "heel"], ["heel", "toe"], ["toe", "ankle"],
];
const DOTS = ["ear", "shoulder", "elbow", "wrist", "hip", "knee", "ankle"];
const ARCS = [
  { joint: "knee", a: "hip", b: "ankle", key: "knee" },
  { joint: "hip", a: "shoulder", b: "knee", key: "hip" },
  { joint: "elbow", a: "shoulder", b: "wrist", key: "elbow" },
];

/** Index of the last frame with t <= time (binary search). */
export function frameIndex(times, time) {
  let lo = 0, hi = times.length - 1;
  if (time <= times[0]) return 0;
  if (time >= times[hi]) return hi;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= time) lo = mid; else hi = mid;
  }
  return lo;
}

/** Linearly interpolated pose at `time`: { joints: {name:[x,y]}, angles: {...}, theta }. */
export function poseAt(track, time) {
  const i = frameIndex(track.t, time);
  const j = Math.min(i + 1, track.t.length - 1);
  const span = track.t[j] - track.t[i];
  const f = span > 0 ? Math.min(1, Math.max(0, (time - track.t[i]) / span)) : 0;
  const joints = {};
  for (const [name, arr] of Object.entries(track.joints)) {
    const p = arr[i], q = arr[j];
    if (p && q) joints[name] = [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f];
    else if (p || q) joints[name] = p || q;
  }
  const angles = {};
  for (const [k, arr] of Object.entries(track.angles || {})) {
    const p = arr[i], q = arr[j];
    angles[k] = p != null && q != null ? p + (q - p) * f : p ?? q;
  }
  let theta = track.theta ? track.theta[i] : null;
  if (track.theta && track.theta[j] != null && theta != null) {
    let d = track.theta[j] - theta;
    if (d < -180) d += 360;
    if (d > 180) d -= 360;
    theta = (theta + d * f + 360) % 360;
  }
  // Back contour isn't interpolated: it's a silhouette edge, so use the nearest frame
  const k = f < 0.5 ? i : j;
  return { index: i, joints, angles, theta, back: track.back?.[k], backIndex: track.back_index?.[k] };
}

/** Size a canvas to its CSS box at device pixel ratio; returns the 2D context. */
export function fitCanvas(canvas) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const { width, height } = canvas.getBoundingClientRect();
  const w = Math.max(1, Math.round(width * dpr)), hh = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== w || canvas.height !== hh) {
    canvas.width = w;
    canvas.height = hh;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width, height };
}

/** Rectangle a media of aspect (mw:mh) occupies inside a box with object-fit: contain. */
export function containRect(mw, mh, bw, bh) {
  const s = Math.min(bw / mw, bh / mh);
  const w = mw * s, h = mh * s;
  return { x: (bw - w) / 2, y: (bh - h) / 2, w, h };
}

function palette() {
  return {
    accent: cssVar("--color-accent") || "#c3f260",
    status: { ok: cssVar("--color-ok"), watch: cssVar("--color-watch"), fix: cssVar("--color-fix") },
    bone: "rgba(255,255,255,0.95)",
    shadow: "rgba(0,0,0,0.55)",
    pill: "rgba(14,16,20,0.82)",
    text: "#f4f5f7",
  };
}

function angleLabel(ctx, cx, y, text, col) {
  ctx.font = "600 12px Geist, system-ui, sans-serif";
  const w = ctx.measureText(text).width + 14;
  const x = cx - w / 2;
  ctx.fillStyle = col.pill;
  ctx.beginPath();
  ctx.roundRect(x, y - 11, w, 22, 11);
  ctx.fill();
  ctx.fillStyle = col.text;
  ctx.fillText(text, x + 7, y + 4);
}

/**
 * Draw one pose. `rect` maps normalised coords to canvas pixels.
 * layers: { skeleton, angles, crank }. `crank` is {cx, cy, r} normalised (r by width).
 */
export function drawPose(ctx, pose, rect, { layers = {}, crank = null, trail = null, backStatus = null } = {}) {
  const col = palette();
  const P = (p) => [rect.x + p[0] * rect.w, rect.y + p[1] * rect.h];
  const J = pose.joints;
  const scale = Math.max(0.7, Math.min(1.4, rect.h / 520));

  if (layers.back !== false && pose.back) {
    // Back contour, coloured by this frame's roundness
    const pts = pose.back.map(P);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const pass of [0, 1]) {
      ctx.strokeStyle = pass ? col.status[backStatus] || col.status.ok : col.shadow;
      ctx.lineWidth = (pass ? 3.5 : 7) * scale;
      ctx.beginPath();
      pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.stroke();
    }
  }

  if (layers.crank !== false && crank) {
    const c = P([crank.cx, crank.cy]);
    const r = crank.r * rect.w;
    ctx.save();
    ctx.setLineDash([4, 6]);
    ctx.strokeStyle = "rgba(255,255,255,0.45)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(c[0], c[1], r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
    if (J.ball) {
      const b = P(J.ball);
      ctx.strokeStyle = col.accent;
      ctx.lineWidth = 3 * scale;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(c[0], c[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.stroke();
      ctx.fillStyle = col.accent;
      ctx.beginPath();
      ctx.arc(c[0], c[1], 4 * scale, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  if (layers.crank !== false && J.heel && J.toe) {
    // Box around foot and pedal, tracked through the stroke
    const foot = ["ankle", "heel", "toe", "ball"].filter((k) => J[k]).map((k) => P(J[k]));
    const xs = foot.map((p) => p[0]), ys = foot.map((p) => p[1]), pad = 10 * scale;
    ctx.strokeStyle = col.status.fix;
    ctx.lineWidth = 2;
    ctx.strokeRect(Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) - Math.min(...xs) + 2 * pad, Math.max(...ys) - Math.min(...ys) + 2 * pad);
  }

  if (trail && trail.length > 1) {
    ctx.strokeStyle = col.accent;
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    for (let i = 1; i < trail.length; i++) {
      ctx.globalAlpha = (i / trail.length) * 0.6;
      const a = P(trail[i - 1]), b = P(trail[i]);
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  if (layers.skeleton !== false) {
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const pass of [0, 1]) {
      ctx.strokeStyle = pass ? col.bone : col.shadow;
      ctx.lineWidth = (pass ? 3 : 6) * scale;
      ctx.beginPath();
      for (const [a, b] of BONES) {
        if (!J[a] || !J[b]) continue;
        const p = P(J[a]), q = P(J[b]);
        ctx.moveTo(p[0], p[1]);
        ctx.lineTo(q[0], q[1]);
      }
      ctx.stroke();
    }
    for (const k of DOTS) {
      if (!J[k]) continue;
      const p = P(J[k]);
      ctx.fillStyle = col.shadow;
      ctx.beginPath();
      ctx.arc(p[0], p[1], 6.5 * scale, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = col.accent;
      ctx.beginPath();
      ctx.arc(p[0], p[1], 4.5 * scale, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  if (layers.angles !== false) {
    for (const { joint, a, b, key } of ARCS) {
      if (!J[joint] || !J[a] || !J[b] || pose.angles?.[key] == null) continue;
      const o = P(J[joint]), pa = P(J[a]), pb = P(J[b]);
      const a1 = Math.atan2(pa[1] - o[1], pa[0] - o[0]);
      const a2 = Math.atan2(pb[1] - o[1], pb[0] - o[0]);
      let d = a2 - a1;
      while (d > Math.PI) d -= 2 * Math.PI;
      while (d < -Math.PI) d += 2 * Math.PI;
      const r = 22 * scale;
      ctx.strokeStyle = col.accent;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(o[0], o[1], r, a1, a1 + d, d < 0);
      ctx.stroke();
      const mid = a1 + d / 2;
      // Label centred just outside the joint, opposite the angle's opening
      const lx = o[0] - Math.cos(mid) * (r + 14);
      const ly = o[1] - Math.sin(mid) * (r + 8);
      angleLabel(ctx, lx, ly, `${Math.round(pose.angles[key])}°`, col);
    }
  }
}
