// Animated OpenVelo mark: a stroke version of the logo that can draw itself
// in ("draw") and then roll ("ride": a gap travels round each wheel).
import { h, svg } from "./util.js";

/**
 * mode: "draw" (draw in once, then ride), "ride" (loop) or "static".
 * Motion is CSS-only and gated behind prefers-reduced-motion.
 */
export function logoAnim({ mode = "ride", size = 96, label = null } = {}) {
  const el = svg("svg", {
    class: `logo-anim is-${mode}`,
    viewBox: "206 280 286 162",
    width: size,
    height: Math.round(size * 0.566),
    role: label ? "img" : null,
    "aria-label": label,
    "aria-hidden": label ? null : "true",
  },
    svg("ellipse", { class: "lw lw-rear", cx: 287, cy: 375, rx: 59.75, ry: 38.75, transform: "rotate(-38 287 375)", pathLength: 100 }),
    svg("ellipse", { class: "lw lw-front", cx: 447, cy: 374, rx: 54.75, ry: 42, transform: "rotate(-50 447 374)", pathLength: 100 }),
    svg("path", { class: "lf", d: "M283 378 L471 303", pathLength: 100 }),
    svg("path", { class: "lb", d: "M410 302 L471 302", pathLength: 100 }));
  if (mode === "draw") {
    // Hand over to the riding loop once the frame has finished drawing.
    el.querySelector(".lb").addEventListener("animationend", () => el.classList.replace("is-draw", "is-ride"), { once: true });
  }
  return el;
}

/** Centered loader: animated mark plus a status line. */
export function loaderBlock(text, size = 88) {
  return h("div", { class: "loader-block" }, logoAnim({ mode: "ride", size }), text ? h("p", {}, text) : null);
}
