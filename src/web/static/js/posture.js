// Back & posture card: back roundness read from the rider's silhouette, plotted
// on a shared time axis (displacement, roundness map, average back shape) and
// synced to the video.
import { h, svg, icon, statusBadge, num0 } from "./util.js";

const M = { t: 14, r: 12, b: 28, l: 44 };
const H = 190;
const BADGE = { ok: "badge-ok", watch: "badge-watch", fix: "badge-fix" };
const VERDICT_STATUS = { neutral: "ok", slight: "watch", rounded: "fix" };

/** Status of a roundness index (%) against the backend's thresholds. */
export function backStatus(v, thresholds) {
  if (v == null || !thresholds) return null;
  return v < thresholds[0] ? "ok" : v < thresholds[1] ? "watch" : "fix";
}

/** Diverging colour for a back offset (%): teal = arched, amber -> red = rounded. */
function heatColor(v, lim) {
  const a = Math.min(1, Math.abs(v) / lim), alpha = (0.15 + 0.85 * a).toFixed(3);
  if (v < 0) return `rgba(43, 179, 192, ${alpha})`;
  return `rgba(${Math.round(240 - 10 * a)}, ${Math.round(180 - 120 * a)}, ${Math.round(40 + 30 * a)}, ${alpha})`;
}

/** Shared frame: time axis, optional back-position labels, playback cursor. */
function timeChart(root, post, W, label, { backAxis = false, right = 0 } = {}) {
  const IW = W - M.l - M.r - right, IH = H - M.t - M.b;
  const { t0, t1 } = post.heat;
  const x = (t) => M.l + ((t - t0) / (t1 - t0 || 1)) * IW;
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": label });
  const axis = svg("g", { class: "axis" });
  const step = [1, 2, 5, 10, 15, 30, 60].find((s) => (t1 - t0) / s <= (W < 420 ? 4 : 7)) || 60;
  for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-6; t += step) {
    axis.append(svg("text", { x: x(t), y: H - 8, "text-anchor": "middle" }, `${t}s`));
  }
  if (backAxis) {
    chart.append(svg("rect", { class: "heat-bg", x: M.l, y: M.t, width: IW, height: IH, rx: 3 }));
    axis.append(svg("text", { x: M.l - 8, y: M.t + 10, "text-anchor": "end" }, "Shldr"),
      svg("text", { x: M.l - 8, y: M.t + IH, "text-anchor": "end" }, "Hips"));
  }
  chart.append(axis);
  const cursor = svg("line", { class: "cursor-line", y1: M.t, y2: M.t + IH, visibility: "hidden" });
  root.append(chart);
  return {
    chart, x, IW, IH,
    // Call after drawing so the cursor sits on top
    cursor() {
      chart.append(cursor);
      return (t) => {
        if (t == null) return cursor.setAttribute("visibility", "hidden");
        cursor.setAttribute("x1", x(t));
        cursor.setAttribute("x2", x(t));
        cursor.setAttribute("visibility", "visible");
      };
    },
    // Click anywhere on the plot to seek
    seekable(onSeek) {
      chart.addEventListener("click", (e) => {
        const r = chart.getBoundingClientRect();
        const sx = ((e.clientX - r.left) / r.width) * W;
        if (sx >= M.l && sx <= M.l + IW) onSeek?.(t0 + ((sx - M.l) / IW) * (t1 - t0));
      });
    },
  };
}

function displacementChart(root, post, W) {
  const d = post.displacement;
  const top = Math.max(1, ...[...d.hip, ...d.shoulder].filter((v) => v != null));
  const hi = 4 * ([1, 2, 5, 10, 20, 50, 100].find((s) => 4 * s >= top) || Math.ceil(top / 4));
  const c = timeChart(root, post, W, `Hip and shoulder vertical displacement, up to ${num0(hi)} ${d.unit}.`);
  const y = (v) => M.t + c.IH - (v / hi) * c.IH;
  const grid = svg("g", { class: "grid" }), axis = c.chart.querySelector(".axis");
  for (let k = 0; k <= 4; k++) {
    const v = (hi * k) / 4;
    grid.append(svg("line", { x1: M.l, x2: M.l + c.IW, y1: y(v), y2: y(v) }));
    axis.append(svg("text", { x: M.l - 8, y: y(v) + 4, "text-anchor": "end" }, num0(v)));
  }
  c.chart.prepend(grid);
  for (const [key, cls] of [["shoulder", "series series-2"], ["hip", "series"]]) {
    const pts = d.t.map((t, i) => (d[key][i] == null ? null : `${c.x(t).toFixed(1)},${y(d[key][i]).toFixed(1)}`));
    c.chart.append(svg("polyline", { class: cls, points: pts.filter(Boolean).join(" ") }));
  }
  return c.cursor();
}

function heatMap(root, post, W, onSeek) {
  const lim = Math.max(1, post.thresholds[1] * 1.4);
  const c = timeChart(root, post, W, "Back roundness map: position along the back over time; orange and red mark rounding.",
    { backAxis: true, right: 30 });
  const cols = post.heat.cols, n = post.stations.length;
  const canvas = document.createElement("canvas");
  canvas.width = cols.length;
  canvas.height = n;
  const ctx = canvas.getContext("2d");
  cols.forEach((col, i) => col.forEach((v, s) => {
    if (v == null) return;
    ctx.fillStyle = heatColor(v, lim);
    ctx.fillRect(i, n - 1 - s, 1, 1);
  }));
  c.chart.append(svg("image", { href: canvas.toDataURL(), x: M.l, y: M.t, width: c.IW, height: c.IH, preserveAspectRatio: "none", style: "image-rendering: pixelated" }));
  const bx = W - M.r - 14, axis = c.chart.querySelector(".axis");
  for (let k = 0; k < 24; k++) {
    c.chart.append(svg("rect", { x: bx, width: 10, y: M.t + (k / 24) * c.IH, height: c.IH / 24 + 0.5, fill: heatColor(lim - (2 * lim * k) / 23, lim) }));
  }
  axis.append(svg("text", { x: bx + 5, y: M.t - 3, "text-anchor": "middle" }, `+${Math.round(lim)}%`),
    svg("text", { x: bx + 5, y: M.t + c.IH + 12, "text-anchor": "middle" }, `−${Math.round(lim)}%`));
  c.seekable(onSeek);
  return c.cursor();
}

function shapeChart(root, post, W, onSeek) {
  const strokes = post.strokes || [];
  const lim = Math.max(1, post.thresholds[1] * 1.4), n = post.stations.length;
  const c = timeChart(root, post, W, `Average back shape for each of ${strokes.length} pedal strokes.`, { backAxis: true });
  const slot = c.IW / Math.max(strokes.length, 8);
  const groups = strokes.map((s) => {
    const cx = c.x((s.t0 + s.t1) / 2);
    const g = svg("g", { class: `stroke-shape s-${VERDICT_STATUS[s.verdict] || "na"}` });
    const pts = s.shape.map((v, i) => (v == null ? null
      : `${(cx + (v / lim) * slot * 0.9).toFixed(1)},${(M.t + c.IH - 4 - (i / (n - 1)) * (c.IH - 8)).toFixed(1)}`));
    g.append(svg("line", { x1: cx, x2: cx, y1: M.t + 4, y2: M.t + c.IH - 4 }), svg("polyline", { points: pts.filter(Boolean).join(" ") }),
      svg("title", {}, `Stroke ${s.n}: ${s.roundness?.toFixed(1)}% curve, ${s.duration.toFixed(2)} s`));
    if (strokes.length <= 16) g.append(svg("text", { x: cx + 5, y: M.t + c.IH - 6 }, String(s.n)));
    c.chart.append(g);
    return g;
  });
  c.seekable(onSeek);
  const setCursor = c.cursor();
  let active = -1;
  return (t) => {
    setCursor(t);
    const i = t == null ? -1 : strokes.findIndex((s) => t >= s.t0 && t <= s.t1 + 0.02);
    if (i === active) return;
    groups[active]?.classList.remove("active");
    groups[i]?.classList.add("active");
    active = i;
  };
}

/**
 * Fill the Back & posture card. Returns a setTime(t) for playback sync, or
 * null when the fit has no posture data (photos, RTMPose, pre-3.0 fits).
 */
export function renderPosture(card, d, onSeek) {
  const post = d.posture, check = d.posture_check;
  card.hidden = !post?.available && !check?.checks?.length;
  if (card.hidden) return null;
  const head = card.querySelector("[data-posture-badges]");
  const body = card.querySelector("[data-posture-body]");
  head.replaceChildren(
    post?.available ? h("span", { class: `badge ${BADGE[post.status]}` }, icon(post.status === "ok" ? "circle-check" : "triangle-alert"), post.label) : "",
    check ? h("span", { class: "badge" }, `Posture ${check.score}/100`) : "");
  body.replaceChildren();

  const kv = (label, value, sub) => h("div", { class: "readout" },
    h("div", { class: "readout-label" }, label), h("div", { class: "readout-value" }, value), h("div", { class: "readout-sub" }, sub || ""));
  if (post?.available) {
    const tm = post.timing;
    body.append(h("div", { class: "kv kv-5" },
      kv("Back curve", `${post.roundness.toFixed(1)}%`, post.bulge_mm ? `≈ ${num0(post.bulge_mm)} mm bulge` : "of back length"),
      kv("Rounded frames", `${Math.round(post.rounded_share * 100)}%`, post.worst_stroke ? `worst: stroke ${post.worst_stroke}` : ""),
      kv("Avg stroke", tm ? `${tm.avg.toFixed(2)} s` : "–", tm ? `${tm.cv_pct}% variation` : ""),
      kv("Fastest", tm ? `${tm.fastest.duration.toFixed(2)} s` : "–", tm ? `stroke ${tm.fastest.n}` : ""),
      kv("Slowest", tm ? `${tm.slowest.duration.toFixed(2)} s` : "–", tm ? `stroke ${tm.slowest.n}` : "")));
  }
  if (check?.checks?.length) {
    body.append(h("ul", { class: "posture-checks" }, check.checks.map((c) => h("li", {}, statusBadge(c.status),
      h("div", {}, h("p", { class: "posture-check-title" }, c.label, h("span", { class: "num t-secondary" }, c.value)),
        h("p", { class: "t-small t-secondary" }, c.detail))))));
  }

  const setters = [];
  if (post?.available) {
    const grid = h("div", { class: "posture-charts" });
    body.append(grid);
    const wide = card.clientWidth > 760;
    const W = Math.max(280, Math.round((card.clientWidth || 700) / (wide ? 3 : 1) - (wide ? 24 : 48)));
    const sw = (cls, label) => h("span", {}, h("i", { class: `sw ${cls}` }), label);
    const panel = (title, sub, key) => {
      const c = h("div", { class: "chart-wrap" });
      grid.append(h("figure", { class: "posture-chart" },
        h("figcaption", {}, h("span", { class: "posture-chart-title" }, title), h("span", { class: "t-caption" }, sub)),
        c, key ? h("div", { class: "chart-key" }, ...key) : ""));
      return c;
    };
    if (post.displacement) {
      setters.push(displacementChart(panel("Vertical displacement", `Rise above lowest point (${post.displacement.unit})`,
        [sw("sw-series", "Hip"), sw("sw-series-2", "Shoulder")]), post, W));
    }
    setters.push(heatMap(panel("Back roundness map", "Where along the back it bends · click to jump"), post, W, onSeek));
    setters.push(shapeChart(panel("Average back shape", "One curve per pedal stroke · click to jump",
      [sw("sw-ok", "Neutral"), sw("sw-watch", "Slight"), sw("sw-fix", "Rounded")]), post, W, onSeek));
  }
  body.append(h("p", { class: "t-caption" },
    "Back shape is traced from your silhouette, hips to shoulders, as how far it bows away from a straight line. A 2D estimate: loose clothing can add a little curve."));
  return (t) => setters.forEach((s) => s(t));
}
