// Live camera check: in-browser MediaPipe pose tracking with framing checks,
// then a 30-second recording handed back to the wizard as a File.
import { $, h, icon, clock, toast } from "./util.js";
import { drawPose, containRect, fitCanvas } from "./overlay.js";
import { loaderBlock } from "./logo.js";

const TASKS = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1";
const RECORD_S = 30;
const MIN_S = 10;
const IDX = { ear: [7, 8], shoulder: [11, 12], elbow: [13, 14], wrist: [15, 16], hip: [23, 24], knee: [25, 26], ankle: [27, 28], heel: [29, 30], toe: [31, 32] };

const CHECKS = [
  { id: "frame", title: "Rider and bike in frame", hint: "Step back until head and feet are visible." },
  { id: "side", title: "Camera side-on", hint: "Turn the camera square to the bike." },
  { id: "distance", title: "Good distance", hint: "The rider should fill about two-thirds of the height." },
  { id: "landscape", title: "Landscape orientation", hint: "Rotate your camera sideways." },
  { id: "light", title: "Enough light", hint: "Turn on more lights or face a window." },
  { id: "pedal", title: "Pedalling detected", hint: "Start pedalling at your normal cadence." },
];

let landmarker = null;
let state = null;

async function loadModel(setStatus) {
  if (landmarker) return landmarker;
  setStatus("Loading the pose model…");
  const { FilesetResolver, PoseLandmarker } = await import(`${TASKS}/vision_bundle.mjs`);
  const files = await FilesetResolver.forVisionTasks(`${TASKS}/wasm`);
  const make = (delegate) => PoseLandmarker.createFromOptions(files, {
    baseOptions: { modelAssetPath: "/models/pose_landmarker.task", delegate },
    runningMode: "VIDEO",
    numPoses: 1,
  });
  try {
    landmarker = await make("GPU");
  } catch {
    landmarker = await make("CPU");
  }
  return landmarker;
}

function kneeAngle(a, b, c) {
  const v1 = [a.x - b.x, a.y - b.y], v2 = [c.x - b.x, c.y - b.y];
  const cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (Math.hypot(...v1) * Math.hypot(...v2));
  return (Math.acos(Math.max(-1, Math.min(1, cos))) * 180) / Math.PI;
}

function evaluate(lm, video, luma, kneeHist) {
  const res = {};
  res.landscape = video.videoWidth > video.videoHeight;
  res.light = luma > 55;
  if (!lm) return { ...res, frame: false, side: false, distance: false, pedal: false };
  const zl = (lm[23].z + lm[25].z) / 2, zr = (lm[24].z + lm[26].z) / 2;
  const s = zl < zr ? 0 : 1; // near side
  const need = ["shoulder", "hip", "knee", "ankle", "heel", "toe", "wrist"].map((k) => lm[IDX[k][s]]);
  res.frame = need.every((p) => (p.visibility ?? 1) > 0.5 && p.x > 0.01 && p.x < 0.99 && p.y > 0.01 && p.y < 0.99);
  const sep = Math.hypot(lm[11].x - lm[12].x, lm[11].y - lm[12].y);
  const torso = Math.hypot((lm[11].x + lm[12].x - lm[23].x - lm[24].x) / 2, (lm[11].y + lm[12].y - lm[23].y - lm[24].y) / 2);
  res.side = torso > 0 && sep / torso < 0.35;
  const ys = lm.filter((p) => (p.visibility ?? 1) > 0.4).map((p) => p.y);
  const span = Math.max(...ys) - Math.min(...ys);
  res.distance = span > 0.42 && span < 0.95;
  const k = kneeAngle(lm[IDX.hip[s]], lm[IDX.knee[s]], lm[IDX.ankle[s]]);
  kneeHist.push({ t: performance.now(), k });
  while (kneeHist.length && performance.now() - kneeHist[0].t > 2500) kneeHist.shift();
  const ks = kneeHist.map((e) => e.k);
  res.pedal = ks.length > 10 && Math.max(...ks) - Math.min(...ks) > 35;
  res.side_index = s;
  return res;
}

function renderChecks(res) {
  const list = $("#live-checks");
  list.replaceChildren(...CHECKS.map((c) => {
    const pass = res ? !!res[c.id] : null;
    return h("li", { dataset: { pass: pass == null ? "" : String(pass) } },
      h("span", { class: "state" }, icon(pass ? "check-bold" : pass === false ? "x-bold" : "loader-circle")),
      h("div", {}, c.title, pass === false ? h("small", {}, c.hint) : null));
  }));
}

function measureLuma(video, scratch) {
  const ctx = scratch.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(video, 0, 0, 32, 18);
  const d = ctx.getImageData(0, 0, 32, 18).data;
  let sum = 0;
  for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
  return sum / (d.length / 4);
}

async function startStream(deviceId) {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { deviceId: deviceId ? { exact: deviceId } : undefined, width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 30 } },
  });
  const video = $("#live-video");
  video.srcObject = state.stream;
  await video.play();
}

async function fillCameras() {
  const sel = $("#live-camera");
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const current = state.stream?.getVideoTracks()[0]?.getSettings().deviceId;
  sel.replaceChildren(...devices.map((d, i) => h("option", { value: d.deviceId, selected: d.deviceId === current }, d.label || `Camera ${i + 1}`)));
}

function loop() {
  if (!state) return;
  const video = $("#live-video");
  const canvas = $("#live-canvas");
  if (video.readyState >= 2 && video.currentTime !== state.lastTime) {
    state.lastTime = video.currentTime;
    let lm = null;
    try {
      lm = landmarker.detectForVideo(video, performance.now()).landmarks?.[0] || null;
    } catch {
      lm = null;
    }
    if (++state.frame % 15 === 0) state.luma = measureLuma(video, state.scratch);
    const res = evaluate(lm, video, state.luma, state.kneeHist);
    state.ready = CHECKS.every((c) => res[c.id]);
    if (state.frame % 6 === 0) renderChecks(res);
    const { ctx, width, height } = fitCanvas(canvas);
    ctx.clearRect(0, 0, width, height);
    if (lm) {
      const s = res.side_index;
      const joints = Object.fromEntries(Object.entries(IDX).map(([k, ix]) => [k, [lm[ix[s]].x, lm[ix[s]].y]]));
      const ang = { knee: kneeAngle(lm[IDX.hip[s]], lm[IDX.knee[s]], lm[IDX.ankle[s]]), hip: kneeAngle(lm[IDX.shoulder[s]], lm[IDX.hip[s]], lm[IDX.knee[s]]) };
      drawPose(ctx, { joints, angles: ang }, containRect(video.videoWidth, video.videoHeight, width, height), { layers: { crank: false } });
    }
    if (!state.recorder) {
      $("#live-status").textContent = state.ready ? "All checks pass. Ready to record." : "Recording works anyway, but results are best when every check passes.";
    }
  }
  state.raf = requestAnimationFrame(loop);
}

function pickMime() {
  for (const m of ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"]) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

async function countdown() {
  const el = $("#live-count");
  for (const n of [3, 2, 1]) {
    el.textContent = String(n);
    await new Promise((r) => setTimeout(r, 1000));
    if (!state) return false;
  }
  el.textContent = "";
  return true;
}

async function toggleRecord() {
  const btn = $("#btn-record");
  if (state.recorder) {
    if ((performance.now() - state.recStart) / 1000 >= MIN_S) state.recorder.stop();
    return;
  }
  btn.disabled = true;
  if (!(await countdown())) return;
  const mime = pickMime();
  const chunks = [];
  state.recorder = new MediaRecorder(state.stream, mime ? { mimeType: mime, videoBitsPerSecond: 8_000_000 } : undefined);
  state.recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  state.recorder.onstop = () => {
    const type = state.recorder.mimeType || "video/webm";
    const ext = type.includes("mp4") ? "mp4" : "webm";
    const file = new File(chunks, `recording-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.${ext}`, { type });
    const done = state.onDone;
    closeLive();
    done(file);
  };
  state.recorder.start(1000);
  state.recStart = performance.now();
  $("#live-rec").hidden = false;
  btn.disabled = false;
  btn.replaceChildren(icon("check"), "Stop and use");
  state.timer = setInterval(() => {
    const t = (performance.now() - state.recStart) / 1000;
    $("#live-rec-time").textContent = clock(t).slice(0, -2);
    btn.disabled = t < MIN_S;
    $("#live-status").textContent = t < MIN_S ? `Keep pedalling. You can stop in ${Math.ceil(MIN_S - t)} s` : "Recording. Stops automatically at 30 s.";
    if (t >= RECORD_S) state.recorder.stop();
  }, 250);
}

export function closeLive() {
  if (!state) return;
  cancelAnimationFrame(state.raf);
  clearInterval(state.timer);
  if (state.recorder && state.recorder.state !== "inactive") {
    state.recorder.onstop = null;
    state.recorder.stop();
  }
  state.stream?.getTracks().forEach((t) => t.stop());
  state = null;
  const dlg = $("#dlg-live");
  if (dlg.open) dlg.close();
}

/** Open the live check. `onDone(file)` receives the recording. */
export async function openLive(onDone) {
  const dlg = $("#dlg-live");
  if (!navigator.mediaDevices?.getUserMedia) {
    toast("Camera access needs a secure page. Open the studio at http://127.0.0.1 on this computer.", { tone: "error" });
    return;
  }
  state = { onDone, kneeHist: [], frame: 0, luma: 128, lastTime: -1, scratch: h("canvas", { width: 32, height: 18 }) };
  const btn = $("#btn-record");
  btn.disabled = true;
  btn.replaceChildren(icon("circle-dot"), "Record 30 s");
  $("#live-rec").hidden = true;
  $("#live-count").textContent = "";
  renderChecks(null);
  dlg.showModal();
  const msg = $("#live-message");
  const setStatus = (t) => { $("#live-status").textContent = t; };
  msg.hidden = false;
  msg.textContent = "Allow camera access to continue.";
  try {
    await startStream();
    msg.replaceChildren(loaderBlock("Loading the pose model…"));
    await Promise.all([loadModel(setStatus), fillCameras()]);
    if (!state) return;
    msg.hidden = true;
    btn.disabled = false;
    state.raf = requestAnimationFrame(loop);
  } catch (err) {
    msg.textContent = err?.name === "NotAllowedError"
      ? "Camera access was blocked. Allow it in your browser's site settings, then try again."
      : `Unable to start the camera (${err?.message || err}). Upload a recorded video instead.`;
    setStatus("");
  }
}

export function initLive() {
  $("#btn-record").addEventListener("click", toggleRecord);
  $("#btn-live-close").addEventListener("click", closeLive);
  $("#btn-live-cancel").addEventListener("click", closeLive);
  $("#dlg-live").addEventListener("cancel", (e) => { e.preventDefault(); closeLive(); });
  $("#live-camera").addEventListener("change", (e) => state && startStream(e.target.value));
}
