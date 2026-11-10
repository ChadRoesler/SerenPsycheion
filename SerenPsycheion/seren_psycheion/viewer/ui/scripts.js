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
  const n = v.night, w = v.work_hours;
  const night = change => saveVoice({ night: change }, "nightMsg");
  $("nightOn").checked = n.enabled; $("nightOn").onchange = e => night({ enabled: e.target.checked });
  $("nightStart").value = n.start; $("nightStart").onchange = e => night({ start: e.target.value });
  $("nightEnd").value = n.end; $("nightEnd").onchange = e => night({ end: e.target.value });
  slider("nightDimmer", n.dimmer, x => night({ dimmer: x }));
  slider("nightVolume", n.volume, x => night({ volume: x }));
  slider("nightBrightness", n.brightness, x => night({ brightness: x }));

  const work = change => saveVoice({ work_hours: change }, "workMsg");
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
  $("feedRows").innerHTML = shown.map(e => `<tr class="${e.scratching ? "scratching" : ""}">` +
    `<td class="n">${new Date(e.t * 1000).toLocaleTimeString()}</td><td>${escapeHtml(describe(e))}</td>` +
    `<td class="n">${e.ms ? e.ms + " ms" : ""}</td><td>${escapeHtml(e.showed || "")}</td></tr>`).join("");
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
}

// ── Build-ups: the face's, counted and fired here ───────────────────
async function loadBuildups() {
  let r;
  try { r = await api("/api/buildups"); } catch (e) { return; }
  $("buOn").checked = r.enabled;
  $("buFiring").textContent = r.firing ? `${r.firing} is going now` : "";
  if (!r.buildups.length) { $("buList").innerHTML = `<p class="muted tiny">This face declares none.</p>`; return; }
  $("buList").innerHTML = r.buildups.map(b => {
    const what = b.touch === "scratching" ? "scratching" : b.touch.replace("_", " ");
    const flow = [b.pose, b.after].filter(Boolean).join(" then ") + (b.sound ? `, with ${b.sound}` : "");
    const state = b.running ? `${Math.round(b.progress * 100)}% of the way`
      : b.resting_s ? `resting, ${b.resting_s}s` : b.last_skipped ? `last one didn't fire: ${b.last_skipped}` : "waiting";
    return `<div class="bu"><div class="row"><b>${escapeHtml(b.name)}</b>` +
      `<span class="lab tiny">${escapeHtml(b.seconds)}s of ${escapeHtml(what)} &rarr; ${escapeHtml(flow)}</span>` +
      `<button data-bu="${escapeHtml(b.name)}">Test</button></div>` +
      `<div class="meter"><div class="fill" style="width:${Math.round(b.progress * 100)}%"></div></div>` +
      `<p class="muted tiny">${escapeHtml(state)}</p></div>`;
  }).join("");
  $("buList").querySelectorAll("button[data-bu]").forEach(btn => btn.onclick = () => testBuildup(btn.dataset.bu));
}

async function testBuildup(name) {
  const headers = { "Content-Type": "application/json" }, tok = getToken();
  if (tok) headers.Authorization = "Bearer " + tok;
  const r = await fetch(`/api/buildups/${encodeURIComponent(name)}/test`, { method: "POST", headers });
  if (r.ok) { say("buMsg", `${name}: on its way.`); return; }
  let why = `${r.status}`;
  try { why = (await r.json()).detail || why; } catch (_) { /* not JSON */ }
  say("buMsg", `${name}: ${why}`, true);
}

document.addEventListener("DOMContentLoaded", () => {
  $("buOn").onchange = async e => {
    try { await api("/api/buildups", { method: "POST", body: JSON.stringify({ enabled: e.target.checked }) });
          say("buMsg", e.target.checked ? "On." : "Off until you switch them back (or Psycheion restarts)."); }
    catch (err) { say("buMsg", why(err), true); }
  };
  loadBuildups(); setInterval(loadBuildups, 1000);
  $("feedMore").onclick = () => { FEED_SHOWN += FEED_PAGE; paintFeed(); };
  loadSoma(); loadTouch();
  setInterval(loadSoma, 5000);
  setInterval(loadTouch, 1000);
});
