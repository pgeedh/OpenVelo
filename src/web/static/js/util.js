// Small DOM, formatting and network helpers shared by every view.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Create an element: h("div", { class: "x", onclick }, child, "text"). */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "html") el.innerHTML = v; // only ever used with static markup
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const c of children.flat()) if (c != null) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

export function icon(name, cls = "icon") {
  const s = svg("svg", { class: cls, "aria-hidden": "true" });
  s.append(svg("use", { href: `#i-${name}` }));
  return s;
}

// ---------------------------------------------------------------- format
const nf1 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
export const deg = (v, digits = 1) => (v == null || !isFinite(v) ? "–" : `${(digits ? nf1 : nf0).format(v)}°`);
export const mm = (v, signed = false) =>
  v == null || !isFinite(v) ? "–" : `${signed && v > 0 ? "+" : ""}${nf0.format(v)} mm`;
export const num0 = (v) => (v == null || !isFinite(v) ? "–" : nf0.format(v));
export const pct = (v) => (v == null ? "–" : new Intl.NumberFormat(undefined, { style: "percent" }).format(v));
export const dateFmt = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(d);
};
export function clock(t) {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export const STATUS = {
  ok: { label: "In range", icon: "circle-check", cls: "badge-ok" },
  watch: { label: "Close", icon: "info", cls: "badge-watch" },
  fix: { label: "Adjust", icon: "triangle-alert", cls: "badge-fix" },
  na: { label: "Not measured", icon: "info", cls: "" },
};
export function statusBadge(status) {
  const s = STATUS[status] || STATUS.na;
  return h("span", { class: `badge ${s.cls}` }, icon(s.icon), s.label);
}


// ---------------------------------------------------------------- storage
export const store = {
  get(key, fallback = null) {
    try {
      const v = localStorage.getItem(`openvelo.${key}`);
      return v == null ? fallback : v;
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`openvelo.${key}`, value);
    } catch {
      /* storage blocked: settings last for this visit only */
    }
  },
};

// ---------------------------------------------------------------- network
export async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status}).`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

/** POST a FormData with upload progress (fetch can't report it). */
export function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* keep empty */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body.detail || `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error("Unable to reach OpenVelo. Check that the studio is still running."));
    xhr.send(form);
  });
}

// ---------------------------------------------------------------- toasts
export function toast(message, { tone = "info", action, duration = 5000 } = {}) {
  const region = $("#toasts");
  const el = h(
    "div",
    { class: "toast", dataset: { tone } },
    icon(tone === "error" ? "triangle-alert" : tone === "success" ? "circle-check" : "info"),
    h("span", {}, message),
  );
  const close = () => {
    if (!el.isConnected) return;
    el.classList.add("is-leaving");
    el.addEventListener("animationend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 400);
  };
  const actions = h("div", { class: "toast-actions" });
  if (action) {
    actions.append(
      h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => { action.run(); close(); } }, action.label),
    );
  }
  actions.append(h("button", { class: "icon-btn", type: "button", "aria-label": "Dismiss notification", onclick: close }, icon("x", "icon icon-sm")));
  el.append(actions);
  region.append(el);
  // Errors and toasts with an action stay until dismissed.
  if (tone !== "error" && !action) setTimeout(close, duration);
  else if (action) setTimeout(close, duration * 2);
  return close;
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
