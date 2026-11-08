// ── SerenPsycheion: the console ─────────────────────────────────────
// The body every 5 s, the touch feed every second. A setting is saved the moment
// it changes. Forms are filled from the body once, then left to the person, so a
// poll never yanks a slider out from under a hand.
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TRUTH = new Set([0, 1, 6, 7]);
let FEED = [], FEED_LAST = 0, FILLED_ROBOT = false, FILLED_VOICE = false;
// The feed keeps the last FEED_KEEP touches and shows FEED_SHOWN of them: a good scratch is dozens a minute,
// and a page that grows a row for each one gets long fast. "Show more" shows FEED_PAGE more.
const FEED_KEEP = 200, FEED_PAGE = 25;
let FEED_SHOWN = FEED_PAGE;

const $ = id => document.getElementById(id);
function say(id, text, bad) { const m = $(id); m.textContent = text || ""; m.className = "msg" + (text ? (bad ? " bad" : " ok") : ""); }
function why(e) { return /^401/.test(e.message) ? "Psycheion wants its token (🔑 Token, top right)." : e.message; }
function pill(id, text, ok) { const p = $(id); p.textContent = text; p.style.color = ok ? "var(--ok)" : "var(--danger)"; }
function ago(ms) {
  const s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
}

function paintRails(leds) {
  for (const [railId, from] of [["railA", 0], ["railB", 6]]) {
    const rail = $(railId); rail.innerHTML = "";
    for (let i = from; i < from + 6; i++) {
      const hex = (leds || "").slice(i * 6, i * 6 + 6) || "000000";
      const d = document.createElement("span");
      d.className = "led" + (TRUTH.has(i) ? " truth" : "");
      // the LEDs top out at 168: show that as full so a dim color is still visible here
      const c = [0, 2, 4].map(k => Math.min(255, Math.round(parseInt(hex.slice(k, k + 2), 16) * 255 / 168)));
      d.style.background = `rgb(${c.join(",")})`;
      d.title = `LED ${i}: #${hex}` + (TRUTH.has(i) ? (i % 6 === 0 ? " (mic / reply)" : " (camera)") : "");
      rail.append(d);
    }
  }
}

function slider(id, value, onSave) {
  const input = $(id), out = input.parentElement.querySelector("output");
  if (value != null) { input.value = value; if (out) out.textContent = value; }
  input.oninput = () => { if (out) out.textContent = input.value; };
  input.onchange = () => onSave(Number(input.value));
}

async function saveRobot(change) {
  try {
    const s = await api("/api/soma/robot/settings", { method: "POST", body: JSON.stringify(change) });
    say("robotMsg", `Saved. Applied now: lights ${s.applied.dimmer}, speaker ${s.applied.volume}, screen ${s.applied.brightness}.`);
  } catch (e) { say("robotMsg", why(e), true); }
}

async function saveVoice(change, msgId) {
  try {
    await api("/api/soma/voice/settings", { method: "POST", body: JSON.stringify(change) });
    say(msgId, "Saved.");
  } catch (e) { say(msgId, why(e), true); }
}

function fillRobot(s) {
  slider("setDimmer", s.dimmer, v => saveRobot({ dimmer: v }));
  slider("setVolume", s.volume, v => saveRobot({ volume: v }));
  slider("setBrightness", s.brightness, v => saveRobot({ brightness: v }));
}

function fillVoice(v) {
  const n = v.night, o = v.overwhelm, w = v.work_hours;
  const night = change => saveVoice({ night: change }, "nightMsg");
  $("nightOn").checked = n.enabled; $("nightOn").onchange = e => night({ enabled: e.target.checked });
  $("nightStart").value = n.start; $("nightStart").onchange = e => night({ start: e.target.value });
  $("nightEnd").value = n.end; $("nightEnd").onchange = e => night({ end: e.target.value });
  slider("nightDimmer", n.dimmer, x => night({ dimmer: x }));
  slider("nightVolume", n.volume, x => night({ volume: x }));
  slider("nightBrightness", n.brightness, x => night({ brightness: x }));

  const over = change => saveVoice({ overwhelm: change }, "overMsg");
  $("overOn").checked = o.enabled; $("overOn").onchange = e => over({ enabled: e.target.checked });
  for (const [id, key] of [["overSeconds", "seconds"], ["overGap", "gap_seconds"], ["overRest", "rest_seconds"]]) {
    $(id).value = o[key]; $(id).onchange = e => over({ [key]: Math.round(Number(e.target.value)) });
  }
  const work = change => saveVoice({ work_hours: change }, "overMsg");
  const days = $("workDays"); days.innerHTML = "";
  DAYS.forEach((name, i) => {
    const box = Object.assign(document.createElement("input"), { type: "checkbox", checked: w.days.includes(i) });
    box.onchange = () => work({ days: [...days.querySelectorAll("input")].map((b, k) => b.checked ? k : -1).filter(k => k >= 0) });
    const lab = document.createElement("label"); lab.append(box, name); days.append(lab);
  });
  $("workStart").value = w.start; $("workStart").onchange = e => work({ start: Math.round(Number(e.target.value)) });
  $("workEnd").value = w.end; $("workEnd").onchange = e => work({ end: Math.round(Number(e.target.value)) });
}

async function loadSoma() {
  let r;
  try { r = await api("/api/soma"); }
  catch (e) { pill("pillRobot", "robot ?", false); pill("pillVoice", "voice ?", false); $("bodyFacts").textContent = why(e); return; }
  const robot = r.robot, voice = r.voice;
  pill("pillRobot", robot ? "robot" : "robot down", !!robot);
  pill("pillVoice", voice ? (voice.speaking ? "speaking" : voice.device ? "channel open" : "voice") : "voice down", !!voice);
  const nightNow = r.voice_settings && r.voice_settings.night_now;
  $("pillNight").hidden = !nightNow;
  const facts = [];
  if (robot) {
    facts.push(["Face", robot.face || "default"], ["Battery", `${robot.batteryLevel}%${robot.charging ? ", charging" : ""}`],
               ["Up", ago(robot.uptimeMs)], ["Memory free", `${Math.round(robot.freeInternalHeap / 1024)} KB`]);
    if (robot.resting) facts.push(["Resting in", robot.resting]);
    if (robot.settings && robot.settings.night) facts.push(["Night cap", "on"]);
    paintRails(robot.leds);
    if (!FILLED_ROBOT && robot.settings) { fillRobot(robot.settings); FILLED_ROBOT = true; }
  } else facts.push(["Robot", r.robot_error || "not answering"]);
  if (!voice) facts.push(["Voice", r.voice_error || "not answering"]);
  $("bodyFacts").innerHTML = facts.map(([k, v]) => `<span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b>`).join("");
  if (!FILLED_VOICE && r.voice_settings) { fillVoice(r.voice_settings); FILLED_VOICE = true; }
}

function describe(e) {
  switch (e.kind) {
    case "stroke": return `stroke, ${e.way === "front_to_back" ? "front to back" : "back to front"}`;
    case "poke": return `poke, ${e.where}`;
    case "finger_rest": return "a finger resting";
    case "hand": return "a whole hand";
    case "scritch": return "scritch";
    case "touch_spot": return `screen: ${e.spot || "spot " + e.index}`;
    case "nose_boop": return "screen: nose";
    case "forehead_kiss": return "screen: forehead";
    default: return e.kind;
  }
}

function paintFeed() {
  const shown = FEED.slice(0, FEED_SHOWN);
  $("feedRows").innerHTML = shown.map(e => `<tr class="${e.overwhelmed ? "over" : ""}">` +
    `<td class="n">${new Date(e.t * 1000).toLocaleTimeString()}</td><td>${escapeHtml(describe(e))}</td>` +
    `<td class="n">${e.ms ? e.ms + " ms" : ""}</td><td>${escapeHtml(e.overwhelmed ? "overwhelmed" : e.showed || "")}</td></tr>`).join("");
  $("feedMore").hidden = FEED.length <= FEED_SHOWN;
  $("feedCount").textContent = FEED.length ? `${shown.length} of the last ${FEED.length}` : "";
}

async function loadTouch() {
  let r;
  try { r = await api(`/api/soma/touch?after=${FEED_LAST}`); } catch (e) { return; }
  if (r.last < FEED_LAST) { FEED = []; FEED_LAST = 0; FEED_SHOWN = FEED_PAGE; paintFeed(); return; }   // the voice server restarted: its count did too
  FEED_LAST = r.last;
  if (r.events.length) {
    FEED = r.events.slice().reverse().concat(FEED).slice(0, FEED_KEEP);
    paintFeed();
  }
  const o = r.overwhelm || {};
  $("overFill").style.width = `${Math.round((o.progress || 0) * 100)}%`;
  $("overText").textContent = o.running ? `Scratching: ${Math.round(o.progress * 100)}% of the way.` : "Nothing running.";
}

document.addEventListener("DOMContentLoaded", () => {
  $("feedMore").onclick = () => { FEED_SHOWN += FEED_PAGE; paintFeed(); };
  loadSoma(); loadTouch();
  setInterval(loadSoma, 5000);
  setInterval(loadTouch, 1000);
});
