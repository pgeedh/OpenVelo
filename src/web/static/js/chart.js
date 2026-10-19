// Joint angle vs crank angle: the fitted curve across every stroke, the raw
// per-frame samples behind it, and the rider's target bands.
import { h, svg, deg } from "./util.js";

const M = { t: 20, r: 12, b: 34, l: 44 };
const NAMES = { knee: "Knee angle", hip: "Hip angle", ankle: "Ankle angle" };
const CLOCK = { 0: "TDC", 90: "3 o'clock", 180: "BDC", 270: "9 o'clock", 360: "TDC" };

function bandsFor(joint, ranges) {
  if (joint === "knee") {
    return [
      { range: ranges.knee_ext, label: "Extension target" },
      { range: ranges.knee_flex, label: "Bend target" },
    ];
  }
  if (joint === "hip") return [{ range: ranges.hip_closed, label: "Closed-hip target" }];
  return [];
}

export function renderStrokeChart(root, tableRoot, keyRoot, stroke, ranges, joint) {
  root.replaceChildren();
  tableRoot.replaceChildren();
  keyRoot.replaceChildren();
  // Draw in CSS pixels so text stays 11px at every width
  const W = Math.max(300, Math.round(root.clientWidth || 640));
  const H = Math.round(Math.min(300, Math.max(220, W * 0.42)));
  const IW = W - M.l - M.r, IH = H - M.t - M.b;
  const curve = stroke?.curves?.[joint];
  if (!curve) {
    root.append(h("p", { class: "t-small t-muted" }, "Not enough clear strokes to model this joint."));
    return { setCursor() {} };
  }
  const theta = stroke.curves.theta;
  const st = stroke.samples.theta, sv = stroke.samples[joint] || [];
  const bands = bandsFor(joint, ranges).filter((b) => b.range);

  // Y domain: data + bands, rounded out to 10°
  const vals = [...curve, ...sv.filter((v) => v != null), ...bands.flatMap((b) => b.range)];
  const lo = Math.floor((Math.min(...vals) - 4) / 10) * 10;
  const hi = Math.ceil((Math.max(...vals) + 4) / 10) * 10;
  const x = (t) => M.l + (t / 360) * IW;
  const y = (v) => M.t + IH - ((v - lo) / (hi - lo)) * IH;

  const i_max = curve.indexOf(Math.max(...curve));
  const i_min = curve.indexOf(Math.min(...curve));
  const label = `${NAMES[joint]} across the pedal stroke: ranges from ${deg(curve[i_min])} at ${theta[i_min]}° crank to ${deg(curve[i_max])} at ${theta[i_max]}° crank.`;
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": label });

  // Grid + axes (recessive hairlines)
  const grid = svg("g", { class: "grid" });
  const axis = svg("g", { class: "axis" });
  const step = hi - lo > 80 ? 20 : 10;
  for (let v = lo; v <= hi; v += step) {
    grid.append(svg("line", { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }));
    axis.append(svg("text", { x: M.l - 8, y: y(v) + 4, "text-anchor": "end" }, `${v}°`));
  }
  const narrow = W < 520;
  for (const t of [0, 90, 180, 270, 360]) {
    grid.append(svg("line", { x1: x(t), x2: x(t), y1: M.t, y2: M.t + IH }));
    const lab = narrow ? (t % 180 === 0 ? CLOCK[t] : `${t}°`) : `${t}° ${CLOCK[t]}`;
    axis.append(svg("text", { x: x(t), y: H - 12, "text-anchor": t === 0 ? "start" : t === 360 ? "end" : "middle" }, lab));
  }
  chart.append(grid);

  // Target bands
  for (const b of bands) {
    const [b0, b1] = b.range;
    chart.append(svg("rect", { class: "band", x: M.l, width: IW, y: y(b1), height: Math.max(1, y(b0) - y(b1)), rx: 2 }));
  }
  keyRoot.append(
    h("span", {}, h("i", { class: "line" }), "Fitted curve"),
    h("span", {}, h("i", { class: "dot" }), "Individual frames"),
    ...bands.map((b) => h("span", {}, h("i"), `${b.label} ${b.range[0]}–${b.range[1]}°`)));

  // Per-frame samples (context), then the fitted curve (the data)
  const dots = svg("g", { class: "samples" });
  st.forEach((t, i) => {
    if (sv[i] != null) dots.append(svg("circle", { cx: x(t), cy: y(sv[i]), r: 1.6 }));
  });
  chart.append(dots);
  const pts = [...theta, 360].map((t, i) => `${x(t).toFixed(1)},${y(curve[i % curve.length]).toFixed(1)}`);
  chart.append(svg("polyline", { class: "series", points: pts.join(" ") }));

  // Label only the extremes
  const ext = svg("g", { class: "extreme" });
  for (const [i, below] of [[i_max, false], [i_min, true]]) {
    const cx = x(theta[i]), cy = y(curve[i]);
    ext.append(svg("circle", { cx, cy, r: 4.5 }));
    ext.append(svg("text", { x: cx, y: below ? Math.min(cy + 18, M.t + IH - 4) : Math.max(cy - 10, M.t - 6), "text-anchor": cx > W - 80 ? "end" : cx < 80 ? "start" : "middle" },
      `${below ? "Min" : "Max"} ${deg(curve[i])}`));
  }
  chart.append(ext);
  chart.append(axis);

  // Playback cursor (synced to the video)
  const cursor = svg("g", { class: "cursor", visibility: "hidden" });
  const cLine = svg("line", { y1: M.t, y2: M.t + IH });
  const cDot = svg("circle", { r: 5 });
  cursor.append(cLine, cDot);
  chart.append(cursor);

  // Hover crosshair + tooltip, snapped to the 5° grid
  const hover = svg("g", { class: "hover", visibility: "hidden" });
  const hLine = svg("line", { y1: M.t, y2: M.t + IH });
  hover.append(hLine);
  chart.append(hover);
  const hit = svg("rect", { x: M.l, y: M.t, width: IW, height: IH, fill: "transparent" });
  chart.append(hit);
  const tip = h("div", { class: "chart-tip", hidden: true });
  const show = (clientX) => {
    const r = chart.getBoundingClientRect();
    const sx = ((clientX - r.left) / r.width) * W;
    const t = Math.max(0, Math.min(355, Math.round((((sx - M.l) / IW) * 360) / 5) * 5));
    const i = theta.indexOf(t);
    hLine.setAttribute("x1", x(t));
    hLine.setAttribute("x2", x(t));
    hover.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.replaceChildren(h("strong", {}, deg(curve[i])), " ", h("span", {}, `at ${t}° crank`));
    tip.style.left = `${(x(t) / W) * 100}%`;
    tip.style.top = `${(y(curve[i]) / H) * 100}%`;
  };
  const hide = () => {
    hover.setAttribute("visibility", "hidden");
    tip.hidden = true;
  };
  hit.addEventListener("pointermove", (e) => show(e.clientX));
  hit.addEventListener("pointerdown", (e) => show(e.clientX));
  hit.addEventListener("pointerleave", hide);

  root.append(chart, tip);

  // Table view (every 30°)
  const rows = theta.map((t, i) => [t, curve[i]]).filter(([t]) => t % 30 === 0);
  tableRoot.append(
    h("table", { class: "data-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Crank angle"), h("th", { scope: "col" }, NAMES[joint]))),
      h("tbody", {}, rows.map(([t, v]) => h("tr", {}, h("td", {}, `${t}°${CLOCK[t] ? ` (${CLOCK[t]})` : ""}`), h("td", {}, deg(v)))))),
  );

  return {
    setCursor(t) {
      if (t == null || !isFinite(t)) {
        cursor.setAttribute("visibility", "hidden");
        return;
      }
      const k = (t % 360) / 5;
      const i0 = Math.floor(k) % curve.length, i1 = (i0 + 1) % curve.length;
      const v = curve[i0] + (curve[i1] - curve[i0]) * (k - Math.floor(k));
      cLine.setAttribute("x1", x(t));
      cLine.setAttribute("x2", x(t));
      cDot.setAttribute("cx", x(t));
      cDot.setAttribute("cy", y(v));
      cursor.setAttribute("visibility", "visible");
    },
  };
}

export const chartTitle = (joint) => `${NAMES[joint]} through the pedal stroke`;
