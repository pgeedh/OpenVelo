// OpenVelo Fit Studio: router and views.
import {
  $, $$, h, icon, api, upload, toast, store, deg, mm, num0, pct, dateFmt, clock,
  statusBadge, reducedMotion,
} from "./util.js";
import { poseAt, drawPose, fitCanvas, containRect } from "./overlay.js";
import { renderStrokeChart, chartTitle } from "./chart.js";
import { openLive, initLive, closeLive } from "./live.js";
import { logoAnim, loaderBlock } from "./logo.js";
import { createIntake } from "./intake.js";

const app = {
  config: null,
  demo: null,
  wizard: { profile: null, flexibility: "average", pains: new Set(), file: null, sample: null },
  fit: null,          // current session data
  player: null,       // video/overlay controller
  pendingDelete: null,
};

// ======================================================================
// Router
// ======================================================================
const TITLES = { home: "OpenVelo · Bike fit from a phone video", fits: "My fits · OpenVelo", new: "New fit · OpenVelo", analyzing: "Analysing · OpenVelo", fit: "Fit report · OpenVelo" };

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  return { view: parts[0] || "home", id: parts[1], params: new URLSearchParams(query) };
}

let firstRoute = true;
async function route() {
  const { view, id, params } = parseHash();
  const known = ["home", "fits", "new", "analyzing", "fit"];
  const name = known.includes(view) ? view : "home";
  if (name !== "fit") app.player?.destroy();
  if (name !== "home") stopHero();
  closeLive();
  $$("[data-view]").forEach((s) => (s.hidden = s.dataset.view !== name));
  $$("[data-nav]").forEach((a) => a.toggleAttribute("aria-current", a.dataset.nav === name || (name === "fit" && a.dataset.nav === "fits")));
  $$("[data-nav][aria-current]").forEach((a) => a.setAttribute("aria-current", "page"));
  $("#btn-new-fit").hidden = name === "new" || name === "analyzing";
  document.title = TITLES[name];

  if (name === "home") await showHome();
  else if (name === "fits") await showFits();
  else if (name === "new") showWizard(params.get("step") || (store.get("onboarded") ? "rider" : "welcome"));
  else if (name === "analyzing") watchJob(id);
  else if (name === "fit") await showFit(id);

  if (!firstRoute) {
    window.scrollTo({ top: 0 });
    const heading = $(`[data-view="${name}"] h1:not([hidden])`);
    const panelHeading = name === "new" ? $(`[data-panel]:not([hidden]) h1`) : null;
    (panelHeading || heading || $("#main")).focus({ preventScroll: true });
  }
  firstRoute = false;
}

// ======================================================================
// Home & history
// ======================================================================
let hero = null;

function stopHero() {
  if (hero) {
    cancelAnimationFrame(hero.raf);
    hero.video.pause();
    hero.video.removeAttribute("src");
    hero.video.load();
  }
  hero = null;
}

function startHero(demo) {
  stopHero();
  const canvas = $("#hero-canvas");
  const track = demo?.track;
  if (!canvas || !track) return;
  // Frame the rider (all frames) inside the hero, cropping the source video around them
  let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
  for (const arr of Object.values(track.joints)) for (const p of arr) if (p) {
    x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]);
  }
  const aspect = track.size[0] / track.size[1];
  const ball = track.joints.ball;
  const video = h("video", { muted: true, loop: true, playsinline: true, preload: "auto", src: demo.media_url });
  video.muted = true;
  hero = { raf: 0, video };
  const still = track.t[0] + (track.t[track.t.length - 1] - track.t[0]) * 0.31;

  const draw = () => {
    if (!hero) return;
    const { ctx, width, height } = fitCanvas(canvas);
    const bh = y1 - y0;
    const s = (height * 0.74) / bh;
    const rect = { w: s * aspect, h: s, x: 0, y: 0 };
    rect.x = width / 2 - (x0 + (x1 - x0) / 2) * rect.w;
    rect.y = height * 0.44 - (y0 + bh / 2) * rect.h;
    ctx.fillStyle = "#0c0d10";
    ctx.fillRect(0, 0, width, height);
    if (video.readyState >= 2) {
      ctx.globalAlpha = 0.85;
      ctx.drawImage(video, rect.x, rect.y, rect.w, rect.h);
      ctx.globalAlpha = 1;
      // Fade the frame edges into the panel so letterboxing never shows
      for (const [x, w, from] of [[0, width * 0.22, 1], [width * 0.78, width * 0.22, 0]]) {
        const g = ctx.createLinearGradient(x, 0, x + w, 0);
        g.addColorStop(from ? 0 : 1, "rgba(12,13,16,1)");
        g.addColorStop(from ? 1 : 0, "rgba(12,13,16,0)");
        ctx.fillStyle = g;
        ctx.fillRect(x, 0, w, height);
      }
      const gb = ctx.createLinearGradient(0, height * 0.6, 0, height);
      gb.addColorStop(0, "rgba(12,13,16,0)");
      gb.addColorStop(1, "rgba(12,13,16,0.9)");
      ctx.fillStyle = gb;
      ctx.fillRect(0, height * 0.6, width, height * 0.4);
    }
    const t = video.readyState >= 1 ? video.currentTime : still;
    const pose = poseAt(track, t);
    const trail = [];
    for (let k = Math.max(0, pose.index - 18); k <= pose.index; k++) if (ball[k]) trail.push(ball[k]);
    drawPose(ctx, pose, rect, { crank: track.crank, trail });
    $("#hero-knee").textContent = deg(pose.angles.knee, 0);
    $("#hero-hip").textContent = deg(pose.angles.hip, 0);
    $("#hero-crank").textContent = pose.theta == null ? "–" : `${Math.round(pose.theta)}°`;
    if (!reducedMotion()) hero.raf = requestAnimationFrame(draw);
  };
  if (reducedMotion()) {
    video.addEventListener("loadeddata", () => { video.currentTime = still; }, { once: true });
    video.addEventListener("seeked", draw);
  } else {
    video.addEventListener("loadeddata", () => video.play().catch(() => {}), { once: true });
  }
  hero.raf = requestAnimationFrame(draw);
}

function fitListItem(s) {
  const thumb = h("img", { class: "fit-thumb", src: s.thumb, alt: "", loading: "lazy" });
  thumb.addEventListener("error", () => (thumb.style.visibility = "hidden"), { once: true });
  return h("li", {},
    h("a", { class: "fit-item", href: `#/fit/${s.id}` },
      thumb,
      h("div", { style: "min-width:0" },
        h("div", { class: "fit-item-title" }, `${s.rider_name || s.title || "Fit"} · ${app.config.profiles[s.profile]?.short || s.profile_name}`),
        h("div", { class: "fit-item-meta" }, `${dateFmt(s.created)} · ${s.mode === "image" ? "Photo" : "Video"}`)),
      h("span", { class: "score-pill", "aria-label": `Score ${s.score ?? "not available"}` }, s.score ?? "–")));
}

async function loadSessions() {
  try {
    const { sessions } = await api("/api/sessions");
    return sessions.filter((s) => s.id !== app.pendingDelete);
  } catch {
    return [];
  }
}

function emptyFits() {
  return h("div", { class: "empty" },
    icon("history", "icon icon-xl"),
    h("p", { class: "t-h3", style: "color: var(--color-text)" }, "No fits yet"),
    h("p", {}, "Your analyses are saved here so you can compare them after each adjustment."),
    h("a", { class: "btn btn-primary", href: "#/new" }, icon("plus"), "Start your first fit"));
}

async function showHome() {
  const sessions = await loadSessions();
  const box = $("#home-recent");
  box.replaceChildren(sessions.length ? h("ul", { class: "fit-list" }, sessions.slice(0, 6).map(fitListItem)) : emptyFits());
  if (!app.demo && app.config?.demo_available) {
    try { app.demo = await api("/api/sessions/demo"); } catch { /* demo optional */ }
  }
  $("#link-demo").hidden = !app.config?.demo_available;
  startHero(app.demo);
}

async function showFits() {
  const sessions = await loadSessions();
  const list = $("#fits-list");
  const demoItem = app.config?.demo_available
    ? h("p", { class: "t-small t-muted", style: "margin-top: var(--s-6)" }, "Looking for an example? ", h("a", { href: "#/fit/demo" }, "Open the sample fit report"), ".")
    : null;
  list.replaceChildren(sessions.length ? h("ul", { class: "fit-list" }, sessions.map(fitListItem)) : emptyFits(), demoItem || "");
}

// ======================================================================
// Wizard
// ======================================================================
const STEPS = ["rider", "setup", "video"];

/** Copy intake answers into the wizard state and the stored defaults. */
function applyIntake(ans) {
  const w = app.wizard;
  if (ans.profile) { w.profile = ans.profile; store.set("profile", ans.profile); }
  if (ans.flexibility) w.flexibility = ans.flexibility;
  w.pains = new Set(ans.pains || []);
  if ("height" in ans) store.set("height", ans.height ? String(ans.height) : "");
  if (ans.crank) store.set("crank", String(ans.crank));
  if ("name" in ans) store.set("name", ans.name || "");
}

function buildWizard() {
  app.wizard.profile = store.get("profile", app.config.default_profile);
  app.intake = createIntake({ config: app.config, onChange: applyIntake });
  applyIntake(app.intake.answers);
}

function showWizard(step) {
  if (step !== "welcome" && !STEPS.includes(step)) step = "rider";
  $("#stepper").hidden = step === "welcome";
  if (step === "welcome") $("#welcome-logo").replaceChildren(logoAnim({ mode: "draw", size: 156 }));
  const idx = STEPS.indexOf(step);
  $$(".stepper li").forEach((li, i) => (li.dataset.state = i < idx ? "done" : i === idx ? "current" : "todo"));
  $$(".stepper li").forEach((li, i) => li.toggleAttribute("aria-current", i === idx) && li.setAttribute("aria-current", "step"));
  $$("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== step));
  // Replay the staged entrance on each step change (infrequent, so worth the motion)
  const panel = $(`[data-panel="${step}"]`);
  if (step === "rider") app.intake.start();
  panel.classList.remove("enter");
  void panel.offsetWidth;
  panel.classList.add("enter");
}

function goStep(step) {
  const cur = parseHash().params.get("step") || (store.get("onboarded") ? "rider" : "welcome");
  if (cur === "rider" && step !== "rider" && step !== "welcome") applyIntake(app.intake.finalize());
  location.hash = `#/new?step=${step}`;
}

function setVideoError(msg) {
  const el = $("#video-error");
  el.hidden = !msg;
  el.replaceChildren(...(msg ? [icon("triangle-alert", "icon icon-sm"), msg] : []));
}

function setFile(file, sample = null) {
  const w = app.wizard;
  w.file = file;
  w.sample = sample;
  setVideoError("");
  $("#file-empty").hidden = !!(file || sample);
  $("#file-chosen").hidden = !(file || sample);
  if (!file && !sample) return;

  const isImage = file ? file.type.startsWith("image/") : /\.(jpe?g|png|webp)$/i.test(sample);
  const src = file ? URL.createObjectURL(file) : `/api/samples/${sample}`;
  const name = file ? file.name : "Sample ride (road bike on a trainer)";
  $("#file-name").textContent = name;
  const checks = $("#file-checks");
  checks.replaceChildren();
  const addCheck = (ok, text) => checks.append(h("li", { class: ok ? "ok" : "warn" }, icon(ok ? "circle-check" : "triangle-alert"), text));
  const media = isImage ? h("img", { src, alt: `Preview of ${name}` }) : h("video", { src, muted: true, playsinline: true, controls: true, preload: "metadata" });
  $("#file-media").replaceChildren(media);
  const size = file ? `${(file.size / 1e6).toFixed(1)} MB` : "";

  if (isImage) {
    media.addEventListener("load", () => {
      $("#file-meta").textContent = [`${media.naturalWidth} × ${media.naturalHeight}`, size].filter(Boolean).join(" · ");
      addCheck(media.naturalWidth >= media.naturalHeight, media.naturalWidth >= media.naturalHeight ? "Landscape photo" : "Portrait photo. Landscape frames the bike better");
      addCheck(false, "A photo gives a static check only. Use a video for the full pedal-stroke analysis.");
    }, { once: true });
  } else {
    media.addEventListener("loadedmetadata", () => {
      const d = media.duration;
      $("#file-meta").textContent = [isFinite(d) ? `${d.toFixed(1)} s` : null, media.videoWidth ? `${media.videoWidth} × ${media.videoHeight}` : null, size].filter(Boolean).join(" · ");
      if (isFinite(d)) addCheck(d >= 8, d >= 8 ? "Long enough for 10+ pedal strokes" : "Short clip. 20–30 s gives tighter results");
      if (isFinite(d) && d > 60) addCheck(false, "Only the first 45 seconds are analysed");
      if (media.videoWidth) addCheck(media.videoWidth >= media.videoHeight, media.videoWidth >= media.videoHeight ? "Landscape orientation" : "Portrait video. The bike may be cut off. Landscape works best");
    }, { once: true });
    media.addEventListener("error", () => {
      $("#file-meta").textContent = size;
      addCheck(true, "This browser can't preview the file, but the studio can still analyse it");
    }, { once: true });
  }
}

async function submitFit(e) {
  e.preventDefault();
  const w = app.wizard;
  if (!w.file && !w.sample) {
    setVideoError("Choose a video, record one, or use the sample ride to continue.");
    $("#dropzone").focus();
    return;
  }
  const form = new FormData();
  if (w.file) form.append("file", w.file);
  else form.append("sample_id", w.sample);
  form.append("profile", w.profile);
  form.append("flexibility", w.flexibility);
  form.append("pains", [...w.pains].join(","));
  form.append("crank_mm", store.get("crank", "172.5"));
  form.append("backend", store.get("engine", "auto"));
  const height = store.get("height", "");
  if (height) form.append("rider_height", height);
  form.append("rider_name", store.get("name", ""));

  const btn = $("#btn-analyze");
  btn.disabled = true;
  location.hash = "#/analyzing/upload";
  try {
    const { job_id } = await upload("/api/jobs", form, (f) => setProgress(f * 0.05, `Uploading · ${Math.round(f * 100)}%`, "upload"));
    location.replace(`#/analyzing/${job_id}`);
    setFile(null);
  } catch (err) {
    showJobError(err.message);
  } finally {
    btn.disabled = false;
  }
}

function initWizard() {
  $$("[data-next]").forEach((b) => b.addEventListener("click", () => {
    if (b.hasAttribute("data-onboarded")) store.set("onboarded", "1");
    goStep(b.dataset.next);
  }));
  $("#fit-form").addEventListener("submit", submitFit);
  $("#btn-intake-continue").addEventListener("click", () => goStep("setup"));
  $("#btn-chat-restart").addEventListener("click", () => app.intake.restart());
  const input = $("#in-file");
  input.addEventListener("change", () => input.files[0] && setFile(input.files[0]));
  const dz = $("#dropzone");
  dz.tabIndex = 0;
  dz.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.click()));
  ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, () => dz.classList.remove("is-over")));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (!/^(video|image)\//.test(f.type) && !/\.(mp4|mov|m4v|webm|avi|mkv|jpe?g|png|webp)$/i.test(f.name)) {
      setVideoError("That file isn't a video or photo. Use MP4, MOV, WebM, JPG or PNG.");
      return;
    }
    setFile(f);
  });
  $("#btn-change-file").addEventListener("click", () => { setFile(null); input.value = ""; input.click(); });
  $("#btn-use-sample").addEventListener("click", () => setFile(null, "videos/testvideo2.mp4"));
  $("#btn-live").addEventListener("click", () => openLive((file) => { setFile(file); toast("Recording ready. Check it, then analyse.", { tone: "success" }); }));
}

// ======================================================================
// Job progress
// ======================================================================
const STAGES = ["upload", "detect", "track", "model", "report"];
let pollTimer = null;

function setProgress(frac, msg, stage) {
  const p = Math.round(Math.max(0, Math.min(1, frac)) * 100);
  $("#progress-fill").style.width = `${p}%`;
  $("#progress-bar").setAttribute("aria-valuenow", String(p));
  $("#progress-pct").textContent = `${p}%`;
  if (msg) $("#progress-msg").textContent = msg;
  const si = STAGES.indexOf(stage);
  $$("#stage-list li").forEach((li, i) => (li.dataset.state = i < si ? "done" : i === si ? "active" : "todo"));
}

function showJobError(msg) {
  clearTimeout(pollTimer);
  $("#progress-logo").replaceChildren(logoAnim({ mode: "static", size: 132 }));
  $("#progress-error").hidden = false;
  $("#progress-error-msg").textContent = msg;
  $("#progress-title").textContent = "Analysis stopped";
  $("#progress-error").querySelector("a").focus();
}

function watchJob(id) {
  clearTimeout(pollTimer);
  $("#progress-logo").replaceChildren(logoAnim({ mode: "ride", size: 132 }));
  $("#progress-error").hidden = true;
  $("#progress-title").textContent = "Building your fit";
  if (!id || id === "upload") {
    setProgress(0, "Uploading", "upload");
    return;
  }
  const tick = async () => {
    if (parseHash().id !== id) return;
    try {
      const job = await api(`/api/jobs/${id}`);
      if (job.status === "error") return showJobError(job.error);
      setProgress(job.progress, job.message, job.stage === "done" ? "report" : job.stage);
      if (job.status === "done") {
        toast("Your fit report is ready.", { tone: "success" });
        location.replace(`#/fit/${job.session_id}`);
        return;
      }
    } catch (err) {
      return showJobError(err.message.includes("not found") ? "This analysis is no longer running. Start a new fit." : err.message);
    }
    pollTimer = setTimeout(tick, 400);
  };
  tick();
}

// ======================================================================
// Fit report
// ======================================================================
async function showFit(id) {
  let data;
  const page = $('[data-view="fit"] .page');
  page.classList.add("fit-loading");
  $("#fit-title").textContent = "Loading fit…";
  const msg = $("#stage-message");
  msg.hidden = false;
  msg.replaceChildren(loaderBlock("Loading your fit"));
  try {
    data = await api(`/api/sessions/${encodeURIComponent(id)}`);
  } catch (err) {
    page.classList.remove("fit-loading");
    msg.replaceChildren(h("p", {}, "This fit couldn't be loaded."));
    $("#fit-title").textContent = "Fit not found";
    $("#fit-meta").replaceChildren(h("span", {}, err.message), h("a", { href: "#/fits" }, "Back to my fits"));
    return;
  }
  app.fit = data;
  document.title = `${data.rider?.name || "Fit report"} · ${data.profile_name} · OpenVelo`;
  renderFitHeader(data);
  renderSide(data);
  renderSnapshots(data);
  setupPlayer(data);
  renderChart();
  page.classList.remove("fit-loading");
}

function renderFitHeader(d) {
  $("#fit-title").textContent = d.rider?.name ? `${d.rider.name}'s fit` : d.id === "demo" ? "Sample fit report" : "Fit report";
  const q = d.quality?.grade;
  const qBadge = q ? h("span", { class: `badge ${q === "good" ? "badge-ok" : q === "fair" ? "badge-watch" : "badge-fix"}` },
    icon(q === "good" ? "circle-check" : "triangle-alert"), `${q[0].toUpperCase()}${q.slice(1)} capture`) : null;
  $("#fit-meta").replaceChildren(
    h("span", {}, dateFmt(d.created)),
    h("span", { "aria-hidden": "true" }, "·"),
    h("span", {}, d.mode === "image" ? "Photo" : `${num0(d.quality?.duration_s)} s video`),
    qBadge || "");
  const sel = $("#fit-profile");
  sel.replaceChildren(...Object.entries(app.config.profiles).map(([k, p]) => h("option", { value: k, selected: k === d.profile }, p.short)));
  $("#btn-pdf").href = `/api/sessions/${d.id}/report.pdf`;
  $("#btn-delete").hidden = d.id === "demo";
}

function scoreVerdict(score) {
  if (score == null) return ["Not enough data", "We couldn't score this position."];
  if (score >= 85) return ["Dialled in", "Your position sits inside the targets for this style."];
  if (score >= 70) return ["Close", "A couple of small changes will get you there."];
  if (score >= 50) return ["Worth adjusting", "Work through the plan below, one change at a time."];
  return ["Needs work", "Start with the first adjustment and re-test after each change."];
}

function renderSide(d) {
  // Score
  const [title, sub] = scoreVerdict(d.score);
  const C = 2 * Math.PI * 48;
  const statuses = Object.values(d.statuses || {}).filter((s) => s !== "na");
  const inRange = statuses.filter((s) => s === "ok").length;
  const ring = h("div", { class: "score-ring" });
  ring.innerHTML = `<svg viewBox="0 0 112 112" aria-hidden="true"><circle class="track" cx="56" cy="56" r="48" fill="none" stroke-width="10"/><circle class="value" cx="56" cy="56" r="48" fill="none" stroke-width="10" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C}"/></svg>`;
  ring.append(h("div", { class: "score-ring-label" }, h("div", {}, h("strong", {}, d.score ?? "–"), h("span", {}, "fit score"))));
  $("#score-card").replaceChildren(ring,
    h("div", { class: "score-text" },
      h("p", { class: "t-h2" }, title),
      h("p", { class: "t-small t-secondary" }, sub),
      h("p", { class: "t-caption" }, `${inRange} of ${statuses.length} angles in range for ${app.config.profiles[d.profile]?.short || d.profile_name}`)));
  requestAnimationFrame(() => ring.querySelector(".value").setAttribute("stroke-dashoffset", String(C * (1 - (d.score || 0) / 100))));

  // Plan
  const recs = d.recommendations || [];
  $("#plan").replaceChildren(recs.length
    ? h("ol", { class: "plan" }, recs.map((r, i) => h("li", { class: "plan-item", dataset: { priority: r.priority } },
        h("span", { class: "plan-num", "aria-hidden": "true" }, i + 1),
        h("div", {}, h("p", { class: "plan-title" }, r.title), h("p", { class: "plan-detail" }, r.detail)))))
    : h("div", { class: "plan-ok" }, icon("circle-check"),
        h("div", {}, h("p", { class: "t-h3" }, "No changes needed"), h("p", { class: "t-small t-secondary" }, `Every measured angle sits inside the ${d.profile_name} targets. If you're comfortable, leave it.`))));
  if (d.range_notes?.length) {
    $("#plan").append(h("p", { class: "t-caption", style: "margin-top: var(--s-3)" }, `Targets personalised: ${d.range_notes.join(" ")}`));
  }
  setupSimulator(d);

  // Metrics
  $("#metrics").replaceChildren(...Object.entries(app.config.metrics).map(([k, meta]) => metricRow(d, k, meta)).filter(Boolean));

  // Pedalling
  const b = d.biomech || {};
  const kv = (label, value) => h("div", { class: "readout" }, h("div", { class: "readout-label" }, label), h("div", { class: "readout-value" }, value));
  $("#biomech").replaceChildren(
    kv("Cadence", b.cadence_rpm ? `${num0(b.cadence_rpm)} rpm` : "–"),
    kv("Strokes modelled", num0(b.cycles_detected)),
    kv("Ankle range", deg(b.ankle_range, 0)),
    kv("Hip rock", mm(b.hip_rock_mm)),
    kv("Knee vs pedal at 3 o'clock", mm(b.kops_mm, true)),
    kv("Knee at bottom (180°)", deg(b.knee_at_bdc)));
  if (d.mode === "video") {
    $("#biomech").append(h("p", { class: "t-caption", style: "grid-column: 1 / -1" },
      b.scale_source ? `Millimetres scaled from ${b.scale_source}.` : "Add your height when starting a fit to see millimetre values."));
  }

  // Quality
  const q = d.quality || {};
  const qItems = [];
  if (q.detection_rate != null) qItems.push([q.detection_rate >= 0.8, `Rider found in ${pct(q.detection_rate)} of ${num0(q.frames_analysed)} frames`]);
  if (q.knee_model_sd != null) qItems.push([q.knee_model_sd <= 3.5, `Frame scatter around the stroke model: ±${q.knee_model_sd.toFixed(1)}°`]);
  if (q.swaps_fixed) qItems.push([true, `${q.swaps_fixed} left/right leg swaps corrected`]);
  if (q.side_overlap != null) qItems.push([q.side_overlap <= 0.35, q.side_overlap <= 0.35 ? "Camera is side-on" : "Camera is angled: both shoulders are visible"]);
  (d.warnings || []).forEach((w) => qItems.push([false, w]));
  $("#quality").replaceChildren(...qItems.map(([ok, text]) => h("li", { class: ok ? "ok" : "warn" }, icon(ok ? "circle-check" : "triangle-alert"), text)));
  const g = q.grade;
  $("#quality-badge").replaceChildren(g ? h("span", { class: `badge ${g === "good" ? "badge-ok" : g === "fair" ? "badge-watch" : "badge-fix"}` }, `${g[0].toUpperCase()}${g.slice(1)}`) : "");

  // Coach
  renderCoach(d.coach);
}

function renderCoach(coach) {
  $("#coach").textContent = coach?.text || "";
}

function metricRow(d, key, meta) {
  const v = d.metrics?.[key];
  if (!v) return null;
  const [lo, hi] = d.ranges[key];
  const det = d.metric_detail?.[key];
  const ci = det?.ci;
  const span = Math.max(hi - lo, 10);
  let min = lo - span * 1.2, max = hi + span * 1.2;
  min = Math.min(min, v - 4, ci ? ci[0] - 2 : v);
  max = Math.max(max, v + 4, ci ? ci[1] + 2 : v);
  const P = (x) => `${((x - min) / (max - min)) * 100}%`;
  const half = ci ? (ci[1] - ci[0]) / 2 : null;
  return h("li", { class: "metric" },
    h("div", { class: "metric-top" },
      h("span", { class: "metric-name" }, meta.short),
      h("span", { class: "metric-value num" }, deg(v), half != null ? h("small", { "aria-label": `plus or minus ${half.toFixed(1)} degrees` }, `±${half.toFixed(1)}`) : "")),
    h("div", { class: "metric-sub" }, h("span", {}, `Target ${lo}–${hi}°`), statusBadge(d.statuses[key])),
    h("div", { class: "gauge", role: "img", "aria-label": `${meta.short} ${deg(v)}, target ${lo} to ${hi} degrees` },
      h("div", { class: "gauge-band", style: `left:${P(lo)};width:calc(${P(hi)} - ${P(lo)})` }),
      ci ? h("div", { class: "gauge-ci", style: `left:${P(ci[0])};width:calc(${P(ci[1])} - ${P(ci[0])})` }) : "",
      h("div", { class: "gauge-pin", style: `left:${P(v)}` })),
    h("details", {},
      h("summary", {}, icon("chevron-right"), "What this means"),
      h("p", {}, meta.explain),
      det?.at_deg != null ? h("p", {}, `Reached at ${Math.round(det.at_deg)}° of crank rotation, from ${det.strokes} strokes.`) : ""));
}

function setupSimulator(d) {
  const b = d.biomech || {};
  const box = $("#simulator");
  const ok = d.mode === "video" && b.mm_per_px && b.thigh_px && b.shank_px && d.metrics?.knee_ext;
  box.hidden = !ok;
  if (!ok) return;
  const L1 = b.thigh_px, L2 = b.shank_px;
  const reach0 = Math.sqrt(L1 * L1 + L2 * L2 - 2 * L1 * L2 * Math.cos((d.metrics.knee_ext * Math.PI) / 180));
  const [lo, hi] = d.ranges.knee_ext;
  const range = $("#sim-range");
  const update = () => {
    const dmm = Number(range.value);
    const r = reach0 + dmm / b.mm_per_px;
    const c = Math.max(-1, Math.min(1, (L1 * L1 + L2 * L2 - r * r) / (2 * L1 * L2)));
    const k = (Math.acos(c) * 180) / Math.PI;
    $("#sim-value").textContent = `${dmm > 0 ? "Raise " : dmm < 0 ? "Lower " : ""}${Math.abs(dmm)} mm`;
    $("#sim-knee").textContent = deg(k);
    $("#sim-knee").style.color = k >= lo && k <= hi ? "var(--color-ok)" : "";
    $("#sim-target").textContent = `${lo}–${hi}°`;
  };
  range.value = String(Math.round(Math.max(-20, Math.min(20, b.saddle_adj_mm || 0))));
  range.oninput = update;
  update();
}

function renderSnapshots(d) {
  const snaps = Object.values(d.snapshots || {});
  const card = $("#snapshots").closest(".card");
  card.hidden = !snaps.length;
  $("#snapshots").replaceChildren(...snaps.map((s) => h("figure", { class: "snap" },
    h("img", { src: s.url, alt: `${s.title}, annotated with joint angles`, loading: "lazy" }),
    h("figcaption", {}, h("span", {}, s.title)))));
}

function currentJoint() {
  return $("#chart-joint input:checked")?.value || "knee";
}

function renderChart() {
  const d = app.fit;
  const card = $("#chart-card");
  card.hidden = !d?.stroke;
  if (!d?.stroke) return;
  const joint = currentJoint();
  $("#chart-title").textContent = chartTitle(joint);
  app.chart = renderStrokeChart($("#chart"), $("#chart-table"), $("#chart-key"), d.stroke, d.ranges, joint);
  app.player?.redraw();
}

// ---------------------------------------------------------------- player
function setupPlayer(d) {
  app.player?.destroy();
  const video = $("#stage-video");
  const img = $("#stage-img");
  const canvas = $("#stage-canvas");
  const msg = $("#stage-message");
  const track = d.track;
  const layers = { skeleton: true, angles: true, crank: true };
  $$("#layer-toggles button").forEach((b) => (layers[b.dataset.layer] = b.getAttribute("aria-pressed") === "true"));
  msg.hidden = true;
  img.hidden = true;
  video.hidden = false;
  $("#transport").hidden = false;
  $("#layer-toggles").hidden = false;

  if (d.mode === "image" || !track) {
    // Photo: show the annotated still
    video.removeAttribute("src");
    video.hidden = true;
    img.hidden = false;
    img.src = Object.values(d.snapshots || {})[0]?.url || "";
    img.alt = "Your position, annotated with joint angles";
    $("#transport").hidden = true;
    $("#layer-toggles").hidden = true;
    $("#hud-chips").replaceChildren();
    const { ctx, width, height } = fitCanvas(canvas);
    ctx.clearRect(0, 0, width, height);
    app.player = { destroy() {}, redraw() {} };
    return;
  }

  let frameMode = false;   // server-decoded frames when the browser can't play the codec
  let fmTime = 0, fmTimer = null, raf = 0, vfc = 0;
  const duration = () => (frameMode ? track.t[track.t.length - 1] : video.duration || track.t[track.t.length - 1]);
  const now = () => (frameMode ? fmTime : video.currentTime);
  const scrub = $("#scrub");

  // Leg-extension events for scrubber ticks and the B shortcut
  const target = d.stroke?.events?.knee_ext_at;
  const bdcTimes = [];
  if (track.theta && target != null) {
    for (let i = 1; i < track.theta.length; i++) {
      const a = track.theta[i - 1], b = track.theta[i];
      if (a == null || b == null) continue;
      const da = (a - target + 360) % 360, db = (b - target + 360) % 360;
      if (da > 270 && db < 90) bdcTimes.push(track.t[i]);
    }
  }
  const marks = $("#scrub-marks");
  const total = track.t[track.t.length - 1] || 1;
  marks.replaceChildren(...bdcTimes.map((t) => h("i", { style: `left:${(t / total) * 100}%` })));

  function draw() {
    const t = now();
    const { ctx, width, height } = fitCanvas(canvas);
    ctx.clearRect(0, 0, width, height);
    const rect = containRect(track.size[0], track.size[1], width, height);
    const pose = poseAt(track, t);
    drawPose(ctx, pose, rect, { layers, crank: track.crank });
    app.chart?.setCursor(pose.theta);
    const chip = (k, v) => h("span", { class: "hud-chip" }, h("span", { class: "k" }, k), v);
    $("#hud-chips").replaceChildren(chip("Knee", deg(pose.angles.knee, 0)), chip("Hip", deg(pose.angles.hip, 0)),
      pose.theta != null ? chip("Crank", `${Math.round(pose.theta)}°`) : "");
    const dur = duration();
    scrub.value = String(Math.round((t / dur) * 1000));
    $("#scrub-fill").style.width = `${(t / dur) * 100}%`;
    $("#time-label").textContent = `${clock(t)} / ${clock(dur)}`;
  }

  function setPlaying(on) {
    $("#btn-play").setAttribute("aria-label", on ? "Pause" : "Play");
    $(".i-play").dataset.show = String(!on);
    $(".i-pause").dataset.show = String(on);
  }

  function pumpVideo() {
    draw();
    if (!video.paused) {
      vfc = video.requestVideoFrameCallback ? video.requestVideoFrameCallback(pumpVideo) : (raf = requestAnimationFrame(pumpVideo));
    }
  }

  function seek(t) {
    t = Math.max(0, Math.min(duration(), t));
    if (frameMode) {
      fmTime = t;
      img.src = `/api/sessions/${d.id}/frame?t=${t.toFixed(3)}&w=1280`;
    } else {
      video.currentTime = t;
    }
    draw();
  }

  function play() {
    if (frameMode) {
      if (fmTimer) return;
      setPlaying(true);
      const rate = Number($('input[name="speed"]:checked').value);
      fmTimer = setInterval(() => seek(fmTime + 0.1 * rate >= duration() ? 0 : fmTime + 0.1 * rate), 100);
    } else {
      video.play();
    }
  }
  function pause() {
    if (frameMode) {
      clearInterval(fmTimer);
      fmTimer = null;
      setPlaying(false);
    } else {
      video.pause();
    }
  }
  const toggle = () => ((frameMode ? fmTimer : !video.paused) ? pause() : play());

  const onPlay = () => { setPlaying(true); pumpVideo(); };
  const onPause = () => { setPlaying(false); draw(); };
  const onError = () => {
    if (frameMode) return;
    frameMode = true;
    video.hidden = true;
    img.hidden = false;
    img.alt = "";
    img.onload = draw;
    seek(0);
  };
  video.addEventListener("play", onPlay);
  video.addEventListener("pause", onPause);
  video.addEventListener("seeked", draw);
  video.addEventListener("loadeddata", draw);
  video.addEventListener("error", onError);
  video.loop = true;
  video.playbackRate = Number($('input[name="speed"]:checked').value);
  video.src = d.media_url;

  const ro = new ResizeObserver(draw);
  ro.observe(canvas);

  scrub.oninput = () => { pause(); seek((Number(scrub.value) / 1000) * duration()); };
  $("#btn-play").onclick = toggle;
  const step = (dt) => { pause(); seek(now() + dt); };
  $("#btn-prev").onclick = () => step(-1 / 30);
  $("#btn-next").onclick = () => step(1 / 30);
  $$('input[name="speed"]').forEach((r) => (r.onchange = () => {
    video.playbackRate = Number(r.value);
    if (fmTimer) { pause(); play(); }
  }));
  $$("#layer-toggles button").forEach((b) => (b.onclick = () => {
    const on = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", String(on));
    layers[b.dataset.layer] = on;
    draw();
  }));
  setPlaying(false);

  app.player = {
    toggle, step,
    nextBdc() {
      const t = now();
      const next = bdcTimes.find((x) => x > t + 0.05) ?? bdcTimes[0];
      if (next != null) { pause(); seek(next); }
    },
    redraw: draw,
    destroy() {
      pause();
      cancelAnimationFrame(raf);
      if (vfc && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(vfc);
      ro.disconnect();
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("seeked", draw);
      video.removeEventListener("loadeddata", draw);
      video.removeEventListener("error", onError);
      video.removeAttribute("src");
      video.load();
      app.player = null;
    },
  };
}

// ---------------------------------------------------------------- fit actions
async function rescore(profile) {
  const d = app.fit;
  try {
    const next = await api(`/api/sessions/${d.id}/rescore`, {
      method: "POST",
      body: JSON.stringify({ profile, flexibility: d.rider?.flexibility || "average", pains: d.rider?.pains || [] }),
    });
    app.fit = { ...d, ...next };
    renderSide(app.fit);
    renderChart();
    toast(`Scored against ${app.config.profiles[profile]?.short || next.profile_name}.`);
  } catch (err) {
    toast(`Unable to re-score: ${err.message}`, { tone: "error" });
  }
}

function deleteFit() {
  const id = app.fit.id;
  app.pendingDelete = id;
  location.hash = "#/fits";
  let undone = false;
  const timer = setTimeout(async () => {
    if (undone) return;
    try {
      await api(`/api/sessions/${id}`, { method: "DELETE" });
    } catch (err) {
      toast(`Unable to delete the fit: ${err.message}`, { tone: "error" });
    }
    if (app.pendingDelete === id) app.pendingDelete = null;
  }, 6000);
  toast("Fit deleted.", {
    action: {
      label: "Undo",
      run: () => {
        undone = true;
        clearTimeout(timer);
        app.pendingDelete = null;
        route();
      },
    },
    duration: 3000,
  });
}

async function openCompare() {
  const d = app.fit;
  const sessions = (await loadSessions()).filter((s) => s.id !== d.id);
  if (app.config.demo_available && d.id !== "demo") sessions.push({ id: "demo", title: "Sample fit", profile_name: "", created: null });
  const sel = $("#compare-with");
  if (!sessions.length) {
    toast("Save at least two fits to compare them. Make an adjustment, then record again.");
    return;
  }
  sel.replaceChildren(...sessions.map((s) => h("option", { value: s.id }, s.id === "demo" ? "Sample fit" : `${dateFmt(s.created)} · ${app.config.profiles[s.profile]?.short || s.profile_name} · score ${s.score ?? "–"}`)));
  const draw = async () => {
    const other = await api(`/api/sessions/${sel.value}`);
    const rows = Object.entries(app.config.metrics).map(([k, meta]) => {
      const a = d.metrics[k], b = other.metrics[k];
      if (!a || !b) return null;
      const [lo, hi] = d.ranges[k];
      const dist = (v) => (v < lo ? lo - v : v > hi ? v - hi : 0);
      const better = dist(a) < dist(b) - 0.5, worse = dist(a) > dist(b) + 0.5;
      const delta = a - b;
      return h("tr", {},
        h("th", { scope: "row" }, meta.short),
        h("td", {}, deg(b)), h("td", {}, deg(a)),
        h("td", { class: better ? "delta-good" : worse ? "delta-bad" : "" },
          `${delta > 0 ? "+" : ""}${delta.toFixed(1)}°`, better ? " · closer to target" : worse ? " · further from target" : ""));
    }).filter(Boolean);
    const note = other.backend && d.backend && other.backend !== d.backend
      ? h("p", { class: "field-error", style: "margin-bottom: var(--s-3)" }, icon("triangle-alert", "icon icon-sm"),
          `These fits used different pose engines (${other.backend_name} and ${d.backend_name}), which place joints slightly differently. Compare fits made with the same engine.`)
      : "";
    $("#compare-table").replaceChildren(note, h("table", { class: "data-table compare-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Angle"), h("th", { scope: "col" }, "Earlier"), h("th", { scope: "col" }, "This fit"), h("th", { scope: "col" }, "Change"))),
      h("tbody", {}, h("tr", {}, h("th", { scope: "row" }, "Fit score"), h("td", {}, other.score ?? "–"), h("td", {}, d.score ?? "–"), h("td", {}, d.score != null && other.score != null ? `${d.score - other.score > 0 ? "+" : ""}${d.score - other.score}` : "–")), rows)));
  };
  sel.onchange = draw;
  await draw();
  $("#dlg-compare").showModal();
}

// ======================================================================
// Settings, theme, shortcuts
// ======================================================================
function applyTheme(pref) {
  const dark = pref === "dark" || (pref === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  // Suppress transitions so every colour doesn't animate at once
  const css = document.createElement("style");
  css.textContent = "*,*::before,*::after{transition:none!important}";
  document.head.append(css);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  void getComputedStyle(document.body).opacity;
  requestAnimationFrame(() => css.remove());
  $('meta[name="theme-color"]').content = dark ? "#0c0d10" : "#f5f7f9";
  $("#btn-theme use").setAttribute("href", dark ? "#i-sun" : "#i-moon");
  $("#btn-theme").setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  app.player?.redraw();
}

function openSettings() {
  const dlg = $("#dlg-settings");
  const theme = store.get("theme", "system");
  $$('#theme-choices input').forEach((r) => (r.checked = r.value === theme));
  const engine = store.get("engine", "auto");
  const avail = app.config.available_backends || {};
  const engines = [["auto", "Automatic"], ["mediapipe", "MediaPipe"], ["rtmpose", "RTMPose"]].filter(([k]) => k === "auto" || avail[k]);
  $("#engine-choices").replaceChildren(...engines.map(([k, label]) => h("label", {}, h("input", { type: "radio", name: "engine", value: k, checked: k === engine }), h("span", {}, label))));
  const hint = () => {
    const k = $('#engine-choices input:checked')?.value || "auto";
    $("#engine-hint").textContent = k === "auto" ? "MediaPipe Pose Heavy: bundled, fast, and gives depth cues for leg tracking." : app.config.backends[k]?.desc || "";
  };
  $$("#engine-choices input").forEach((r) => r.addEventListener("change", hint));
  hint();
  dlg.showModal();
}

function initSettings() {
  $("#btn-settings").addEventListener("click", openSettings);
  $("#dlg-settings").addEventListener("close", () => {
    if ($("#dlg-settings").returnValue !== "save") return;
    store.set("theme", $('#theme-choices input:checked')?.value || "system");
    store.set("engine", $('#engine-choices input:checked')?.value || "auto");
    applyTheme(store.get("theme", "system"));
    toast("Settings saved.", { tone: "success" });
  });
  $("#btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    store.set("theme", next);
    applyTheme(next);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => store.get("theme", "system") === "system" && applyTheme("system"));
  $("#btn-shortcuts").addEventListener("click", () => $("#dlg-shortcuts").showModal());
}

function initShortcuts() {
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest("input, select, textarea, [contenteditable]") || $("dialog[open]")) return;
    const view = parseHash().view;
    if (e.key === "?") { e.preventDefault(); $("#dlg-shortcuts").showModal(); return; }
    if (e.key === "n" || e.key === "N") { location.hash = "#/new"; return; }
    if (view !== "fit" || !app.player) return;
    if (e.key === " " && !e.target.closest("button, a")) { e.preventDefault(); app.player.toggle?.(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); app.player.step?.(e.shiftKey ? -1 : -1 / 30); }
    else if (e.key === "ArrowRight") { e.preventDefault(); app.player.step?.(e.shiftKey ? 1 : 1 / 30); }
    else if (e.key === "b" || e.key === "B") app.player.nextBdc?.();
    else if (e.key === "e" || e.key === "E") $("#btn-pdf").click();
  });
}

// ======================================================================
// Boot
// ======================================================================
function startSplash() {
  const splash = $("#splash");
  if (document.documentElement.classList.contains("no-splash")) return () => splash.remove();
  $("#splash-inner").prepend(logoAnim({ mode: "draw", size: 180 }));
  const started = performance.now();
  return () => {
    // Let the mark finish drawing once, then fade the splash away
    const wait = reducedMotion() ? 0 : Math.max(0, 1150 - (performance.now() - started));
    setTimeout(() => {
      splash.classList.add("is-done");
      try { sessionStorage.setItem("openvelo.splash", "1"); } catch { /* ignore */ }
      setTimeout(() => splash.remove(), 400);
    }, wait);
  };
}

async function boot() {
  applyTheme(store.get("theme", "system"));
  const endSplash = startSplash();
  try {
    app.config = await api("/api/config");
  } catch (err) {
    endSplash();
    toast(`Unable to load the studio: ${err.message}`, { tone: "error" });
    return;
  }
  buildWizard();
  initWizard();
  initLive();
  initSettings();
  initShortcuts();
  $("#fit-profile").addEventListener("change", (e) => rescore(e.target.value));
  $("#btn-delete").addEventListener("click", deleteFit);
  $("#btn-compare").addEventListener("click", openCompare);
  $$('#chart-joint input').forEach((r) => r.addEventListener("change", renderChart));
  window.addEventListener("hashchange", route);
  endSplash();
  let lastW = 0;
  new ResizeObserver(([e]) => {
    const w = Math.round(e.contentRect.width);
    if (w !== lastW && app.fit && parseHash().view === "fit") { lastW = w; renderChart(); }
  }).observe($("#chart"));
  await route();
}

boot();
