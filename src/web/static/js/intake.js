// Conversational intake: a chat-style assistant that collects the rider's
// details one question at a time. Fully offline: answers come from quick-reply
// chips or free text understood by small local parsers.
import { $, h, icon, store, reducedMotion } from "./util.js";

const PROFILE_WORDS = [
  ["tri_tt", /\b(tri|triathlon|ironman|70\.3|time ?trial|tt|aero ?bars?|aero)\b/i],
  ["mtb_gravel", /\b(gravel|mtb|mountain|off[- ]?road|trail|cx|cyclo-?cross|bikepacking)\b/i],
  ["road_race", /\b(race|racing|crits?|criterium|competitive|aggressive|fast group|road race)\b/i],
  ["road_endurance", /\b(endurance|sportive|gran fondo|fondo|commut\w*|comfort|leisure|long rides?|touring|road|fitness)\b/i],
];
const FLEX_WORDS = [
  ["flexible", /\b(palms?|floor|very flexible|flexible|yoga|bendy)\b/i],
  ["limited", /\b(limited|stiff|tight|not (very )?flexible|can'?t|cannot|shins?|knees?|above)\b/i],
  ["average", /\b(toes?|fingertips?|average|normal|ok(ay)?|medium|so-?so)\b/i],
];
const PAIN_WORDS = [
  ["knee_back", /\b(back|rear) of (my |the )?knees?\b|\bbehind (my |the )?knees?\b|\bknee\w* \w+ behind\b|\bhamstring/i],
  ["knee_front", /\b(front of (my |the )?knees?|kneecaps?|patella|knees?)\b/i],
  ["lower_back", /\b(lower back|back pain|back|lumbar|spine)\b/i],
  ["neck_shoulders", /\b(neck|shoulders?|traps?)\b/i],
  ["hands", /\b(hands?|wrists?|fingers?|palms?|numb hands?)\b/i],
  ["saddle", /\b(saddle|seat|sit ?bones?|groin|perine\w*|butt|bum|numb(ness)? down)\b/i],
];
const NONE_WORDS = /^(no|none|nothing|nope|nah|fine|all good|no pain|nothing hurts|i'?m fine)\b/i;
const SKIP_WORDS = /^(skip|pass|rather not|prefer not|n\/?a|-)$/i;

const fmtList = (items) => new Intl.ListFormat(undefined, { style: "long", type: "conjunction" }).format(items);

function parseHeight(text) {
  const t = text.toLowerCase().replace(/[″"]/g, "in").replace(/[′’]/g, "'");
  let m = t.match(/(\d)\s*(?:'|ft|foot|feet)\s*(\d{1,2}(?:\.\d)?)?/);
  if (m) return Math.round((Number(m[1]) * 12 + Number(m[2] || 0)) * 2.54);
  m = t.match(/(\d(?:[.,]\d{1,2})?)\s*m\b/);
  if (m) return Math.round(Number(m[1].replace(",", ".")) * 100);
  m = t.match(/(\d{2,3}(?:[.,]\d)?)/);
  if (m) {
    const v = Number(m[1].replace(",", "."));
    return v < 3 ? Math.round(v * 100) : v;
  }
  return null;
}

function matchFirst(table, text) {
  for (const [key, re] of table) if (re.test(text)) return key;
  return null;
}

function buildQuestions(cfg) {
  const prof = cfg.profiles;
  return [
    {
      id: "name",
      ask: () => "Hi, I'm the OpenVelo fit assistant. Six quick questions and we'll set targets that suit you. First, what should I call you?",
      chips: () => [{ label: "Skip", value: "" }],
      placeholder: "Your first name",
      parse(text) {
        const name = text.trim().replace(/^(i'?m|my name is|call me|it'?s)\s+/i, "").slice(0, 40);
        if (!name || SKIP_WORDS.test(name)) return { value: "", reply: "No problem, we'll keep it anonymous." };
        const nice = name.charAt(0).toUpperCase() + name.slice(1);
        return { value: nice, reply: `Nice to meet you, ${nice}.` };
      },
      show: (v) => v || "Not given",
    },
    {
      id: "profile",
      ask: (s) => `${s.name ? `${s.name}, what` : "What"} kind of riding do you mostly do?`,
      chips: () => Object.entries(prof).map(([k, p]) => ({ label: p.short, value: k, icon: { road_endurance: "bike", road_race: "zap", tri_tt: "wind", mtb_gravel: "mountain" }[k] })),
      placeholder: "For example: sportives and long weekend rides",
      parse(text, chip) {
        const key = chip ?? matchFirst(PROFILE_WORDS, text);
        if (!key) return { error: "I didn't catch the type of riding. Pick one of the options, or describe it in a few words, like \"crits\" or \"Ironman training\"." };
        return { value: key, reply: `${prof[key].short}. ${prof[key].blurb}` };
      },
      show: (v) => prof[v]?.short || "Not set",
    },
    {
      id: "height",
      ask: () => "How tall are you? Centimetres or feet and inches both work.",
      chips: () => [{ label: "Skip", value: "" }],
      placeholder: "178 cm or 5'10\"",
      parse(text, chip) {
        if (chip === "" || SKIP_WORDS.test(text.trim())) return { value: null, reply: "No problem. Adjustments will be shown in degrees rather than millimetres." };
        const cm = parseHeight(text);
        if (!cm || cm < 100 || cm > 230) return { error: "That doesn't look like a height I can use. Try something like 178 cm or 5'10\"." };
        return { value: cm, reply: `${cm} cm. That lets me convert your angles into millimetre adjustments.` };
      },
      show: (v) => (v ? `${v} cm` : "Not given"),
    },
    {
      id: "flexibility",
      ask: () => "Stand with straight legs and reach for the floor. Where do your hands get to?",
      chips: () => [
        { label: "Above my toes", value: "limited" },
        { label: "My toes", value: "average" },
        { label: "Palms on the floor", value: "flexible" },
      ],
      placeholder: "For example: just past my knees",
      parse(text, chip) {
        const key = chip ?? matchFirst(FLEX_WORDS, text);
        if (!key) return { error: "Tell me roughly where your hands reach: above your toes, your toes, or the floor." };
        const reply = {
          limited: "Thanks. I'll open your hip and torso targets so the position stays sustainable.",
          average: "Average flexibility. I'll use the standard targets.",
          flexible: "Nice. You can hold a lower position, so I'll tighten the targets slightly.",
        }[key];
        return { value: key, reply };
      },
      show: (v) => cfg.flexibility[v]?.label || "Average",
    },
    {
      id: "pains",
      multi: true,
      ask: () => "Does anything hurt or go numb when you ride? Pick any that apply, or describe it.",
      chips: () => [...Object.entries(cfg.pains).map(([k, label]) => ({ label, value: k })), { label: "Nothing hurts", value: "none", exclusive: true }],
      placeholder: "For example: my knees ache at the front after an hour",
      parse(text, chips) {
        let keys = chips;
        if (!keys) {
          if (NONE_WORDS.test(text.trim())) keys = [];
          else {
            const lower = text.toLowerCase();
            keys = PAIN_WORDS.filter(([, re]) => re.test(lower)).map(([k]) => k);
            // "back of the knee" also trips the generic knee and back rules
            if (keys.includes("knee_back")) {
              const plainKnee = /\bfront of (my |the )?knees?|kneecap/.test(lower);
              keys = keys.filter((k) => (k !== "knee_front" || plainKnee) && (k !== "lower_back" || /lower back|back pain|\bmy back\b/.test(lower)));
            }
            if (!keys.length) return { error: "I couldn't match that to an area. Try words like knee, back, neck, hands or saddle, or pick from the options." };
          }
        }
        keys = keys.filter((k) => k !== "none");
        if (!keys.length) return { value: [], reply: "Great, no discomfort. I'll use the standard targets." };
        const names = keys.map((k) => cfg.pains[k].toLowerCase());
        return { value: keys, reply: `Noted: ${fmtList(names)}. I'll shift your targets to take load off ${keys.length > 1 ? "those areas" : "that area"}.` };
      },
      show: (v) => (v?.length ? fmtList(v.map((k, i) => (i ? cfg.pains[k].toLowerCase() : cfg.pains[k]))) : "None"),
    },
    {
      id: "crank",
      ask: () => "Last one. What crank length do you run? It's stamped on the inside of the crank arm.",
      chips: () => [
        { label: "165 mm", value: 165 }, { label: "170 mm", value: 170 }, { label: "172.5 mm", value: 172.5 },
        { label: "175 mm", value: 175 }, { label: "Not sure", value: "unsure" },
      ],
      placeholder: "172.5",
      parse(text, chip) {
        if (chip === "unsure" || /not sure|don'?t know|no idea|unsure/i.test(text)) {
          return { value: 172.5, reply: "I'll use 172.5 mm, the most common road size. Only the millimetre scale depends on it." };
        }
        const v = chip ?? Number((text.match(/\d{3}(?:[.,]\d)?/) || [""])[0].replace(",", "."));
        if (!v || v < 150 || v > 185) return { error: "Crank lengths are usually between 150 and 185 mm. Try something like 172.5, or choose Not sure." };
        return { value: v, reply: `${v} mm, got it.` };
      },
      show: (v) => `${v ?? 172.5} mm`,
    },
  ];
}

/**
 * Mount the intake into the wizard. `onChange(answers)` fires whenever an
 * answer changes so the wizard can keep its state in sync.
 */
export function createIntake({ config, onChange }) {
  const Q = buildQuestions(config);
  const log = $("#chat-log");
  const quick = $("#chat-quick");
  const input = $("#chat-input");
  const send = $("#chat-send");

  let answers = {};
  try { answers = JSON.parse(store.get("intake", "{}")) || {}; } catch { answers = {}; }
  let current = null;   // index of the question being asked
  let editing = false;  // re-asking one question from the summary
  let picked = new Set();
  let busy = false;

  const save = () => {
    store.set("intake", JSON.stringify(answers));
    onChange?.(answers);
  };
  const scroll = () => { log.scrollTop = log.scrollHeight; };
  const complete = () => Q.every((q) => q.id in answers);

  function bubble(role, ...content) {
    const el = role === "bot"
      ? h("div", { class: "msg msg-bot" },
          h("span", { class: "msg-avatar", "aria-hidden": "true" }, svgLogo()),
          h("div", { class: "msg-body" }, h("span", { class: "sr-only" }, "Assistant: "), ...content))
      : h("div", { class: "msg msg-user" }, h("div", { class: "msg-body" }, h("span", { class: "sr-only" }, "You: "), ...content));
    log.append(el);
    scroll();
    return el;
  }

  async function botSay(...content) {
    busy = true;
    const typing = h("div", { class: "msg msg-bot msg-typing", "aria-hidden": "true" },
      h("span", { class: "msg-avatar" }, svgLogo()), h("div", { class: "msg-body" }, h("i"), h("i"), h("i")));
    log.append(typing);
    scroll();
    await new Promise((r) => setTimeout(r, reducedMotion() ? 150 : 420));
    typing.remove();
    busy = false;
    return bubble("bot", ...content);
  }

  function renderQuick(q) {
    if (!q) return quick.replaceChildren();
    picked = new Set(q.multi ? (answers[q.id] || []) : []);
    const chips = q.chips(answers);
    const items = chips.map((c) => {
      const b = h("button", {
        type: "button", class: "quick-chip",
        "aria-pressed": q.multi ? String(picked.has(c.value)) : null,
        onclick: () => {
          if (busy) return;
          if (!q.multi) return answer(c.label, c.value);
          if (c.exclusive) return answer(c.label, []);
          picked.has(c.value) ? picked.delete(c.value) : picked.add(c.value);
          b.setAttribute("aria-pressed", String(picked.has(c.value)));
          done.disabled = false;
        },
      }, c.icon ? icon(c.icon, "icon icon-sm") : null, q.multi ? icon("check-bold", "icon icon-sm chip-tick") : null, c.label);
      return b;
    });
    const done = q.multi ? h("button", {
      type: "button", class: "quick-chip quick-done",
      onclick: () => {
        if (busy) return;
        const keys = [...picked];
        answer(keys.length ? fmtList(keys.map((k) => config.pains[k])) : "Nothing hurts", keys);
      },
    }, icon("arrow-right", "icon icon-sm"), "Done") : null;
    quick.replaceChildren(...items, done || "");
    quick.setAttribute("aria-label", `Suggested answers: ${q.ask(answers)}`);
    input.placeholder = q.placeholder || "Type your answer";
  }

  async function ask(i) {
    current = i;
    const q = Q[i];
    quick.replaceChildren();
    await botSay(q.ask(answers));
    renderQuick(q);
  }

  async function answer(text, chipValue) {
    if (current == null || busy) return;
    const q = Q[current];
    bubble("user", text || "Skip");
    input.value = "";
    const res = q.parse(text, chipValue === undefined ? undefined : chipValue);
    if (res.error) {
      await botSay(res.error);
      renderQuick(q);
      input.focus();
      return;
    }
    answers[q.id] = res.value;
    save();
    quick.replaceChildren();
    await botSay(res.reply);
    const next = editing ? -1 : Q.findIndex((qq, idx) => idx > current && !(qq.id in answers));
    editing = false;
    if (next === -1) {
      current = null;
      await summary();
    } else {
      await ask(next);
    }
    input.focus({ preventScroll: true });
  }

  function summaryCard() {
    return h("dl", { class: "intake-summary" }, Q.map((q, i) =>
      h("div", { class: "intake-row" },
        h("dt", {}, { name: "Name", profile: "Riding", height: "Height", flexibility: "Flexibility", pains: "Discomfort", crank: "Cranks" }[q.id]),
        h("dd", {}, q.show(answers[q.id])),
        h("button", { type: "button", class: "link-btn intake-edit", "aria-label": `Change ${q.id === "pains" ? "discomfort" : q.id}`, onclick: () => { if (!busy) { editing = true; ask(i); } } }, "Change"))));
  }

  async function summary() {
    quick.replaceChildren();
    input.placeholder = "Anything to change? Use the Change links above";
    await botSay(h("p", {}, "Here's what I have:"), summaryCard(), h("p", {}, "All set. Next, we'll set up your camera."));
    $("#btn-intake-continue").focus({ preventScroll: true });
  }

  function submitText() {
    const text = input.value.trim();
    if (!text || busy) return;
    if (current == null) {
      bubble("user", text);
      input.value = "";
      botSay("To change an answer, use the Change link next to it in the summary above.");
      return;
    }
    answer(text, undefined);
  }

  send.addEventListener("click", submitText);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitText(); }
  });

  return {
    /** Start (or resume) the conversation when the step opens. */
    start() {
      if (log.childElementCount) return;
      if (complete()) {
        bubble("bot", h("p", {}, `Welcome back${answers.name ? `, ${answers.name}` : ""}. Here's what I have from last time:`), summaryCard());
        current = null;
        return;
      }
      const first = Q.findIndex((q) => !(q.id in answers));
      ask(first === -1 ? 0 : first);
    },
    restart() {
      answers = {};
      save();
      log.replaceChildren();
      editing = false;
      ask(0);
      input.focus();
    },
    /** Fill sensible defaults for anything unanswered (Continue at any time). */
    finalize() {
      answers = { name: "", profile: config.default_profile, height: null, flexibility: "average", pains: [], crank: 172.5, ...answers };
      save();
      return answers;
    },
    get answers() { return answers; },
  };
}

function svgLogo() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", "18");
  s.setAttribute("height", "10");
  const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
  u.setAttribute("href", "#logo-bike");
  s.append(u);
  return s;
}
