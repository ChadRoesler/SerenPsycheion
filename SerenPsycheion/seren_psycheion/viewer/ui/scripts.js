// ── SerenPsycheion: the console ─────────────────────────────────────
// The body every 5 s, the touch feed every second. A setting is saved the moment
// it changes. Forms are filled from the body once, then left to the person, so a
// poll never yanks a slider out from under a hand.
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TRUTH = new Set([0, 1, 6, 7]);
let FEED = [], FEED_LAST = 0, FILLED_ROBOT = false, FILLED_VOICE = false;

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

async function loadTouch() {
  let r;
  try { r = await api(`/api/soma/touch?after=${FEED_LAST}`); } catch (e) { return; }
  if (r.last < FEED_LAST) { FEED = []; FEED_LAST = 0; return; }   // the voice server restarted: its count did too
  FEED_LAST = r.last;
  if (r.events.length) {
    FEED = r.events.slice().reverse().concat(FEED).slice(0, 100);
    $("feedRows").innerHTML = FEED.map(e => `<tr class="${e.overwhelmed ? "over" : ""}">` +
      `<td class="n">${new Date(e.t * 1000).toLocaleTimeString()}</td><td>${escapeHtml(describe(e))}</td>` +
      `<td class="n">${e.ms ? e.ms + " ms" : ""}</td><td>${escapeHtml(e.overwhelmed ? "overwhelmed" : e.showed || "")}</td></tr>`).join("");
  }
  const o = r.overwhelm || {};
  $("overFill").style.width = `${Math.round((o.progress || 0) * 100)}%`;
  $("overText").textContent = o.running ? `Scratching: ${Math.round(o.progress * 100)}% of the way.` : "Nothing running.";
}

// ── Set up ──────────────────────────────────────────────────────────
// Its own fetch: api() keeps only the status, and here the reason is the useful part
// ("this robot's firmware has no /api/config yet: ...").
function setupKeys() {
  const v = id => $(id).value.trim(), keys = {};
  if (v("suSsid")) keys.wifi = { ssid: v("suSsid"), password: $("suPass").value };
  keys.voice = v("suVoice") || "auto";
  if (v("suTsKey") || v("suTsName")) {
    keys.tailnet = {};
    if (v("suTsKey")) keys.tailnet.key = v("suTsKey");
    if (v("suTsName")) keys.tailnet.name = v("suTsName");
  }
  return keys;
}

async function setupCall(path, body) {
  const headers = { "Content-Type": "application/json" }, tok = getToken();
  if (tok) headers.Authorization = "Bearer " + tok;
  const r = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
  if (!r.ok) {
    let why = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); why = j.detail || why; } catch (_) { /* not JSON */ }
    throw new Error(r.status === 401 ? "Psycheion wants its token (🔑 Token, top right)." : why);
  }
  return r;
}

function forgetSecrets() { $("suPass").value = ""; $("suTsKey").value = ""; }

async function setupSend() {
  const keys = setupKeys(), rotate = $("suRotate").checked;
  const what = [keys.wifi && `Wi-Fi to ${keys.wifi.ssid}`, `voice server ${keys.voice}`,
                keys.tailnet && "the tailnet", rotate && "a new token"].filter(Boolean).join(", ");
  if (!confirm(`Send ${what} to the robot? It saves them and reboots.` +
               (keys.wifi ? "\n\nA wrong Wi-Fi password takes it off the network until it gets a soma.yaml card." : ""))) return;
  say("suMsg", "Sending...");
  try {
    const r = await (await setupCall("/api/soma/setup", { ...keys, rotate_token: rotate })).json();
    forgetSecrets(); $("suRotate").checked = false;
    say("suMsg", `The robot saved: ${r.changed.join(", ") || "nothing new"}.` +
        (r.rebooting ? " It's rebooting; give it half a minute." : "") +
        (r.token_rotated ? " New token written down by the voice server." : ""));
  } catch (e) { say("suMsg", e.message, true); }
}

async function setupCard() {
  say("suMsg", "Making it...");
  try {
    const text = await (await setupCall("/api/soma/card", setupKeys())).text();
    const a = Object.assign(document.createElement("a"),
                            { href: URL.createObjectURL(new Blob([text], { type: "text/yaml" })), download: "soma.yaml" });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    forgetSecrets();
    say("suMsg", "Downloaded. Copy it to the root of the robot's microSD card and boot it; it reads it and deletes it. " +
                 "It holds the robot's token, so don't leave it lying around.");
  } catch (e) { say("suMsg", e.message, true); }
}

// ── Firmware ────────────────────────────────────────────────────────
async function loadFirmware() {
  let f;
  try { f = await (await fetch("/api/soma/firmware", { headers: authHeaders() })).json(); }
  catch (e) { return; }
  if (f.detail) { say("fwMsg", f.detail, true); return; }
  $("fwFacts").innerHTML = [["The robot runs", f.robot || "?"], ["Armed", f.armed ? f.armed.version : "nothing"],
                            ["Releases from", f.repo || "?"]]
    .map(([k, v]) => `<span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b>`).join("");
  $("fwRows").innerHTML = (f.staged || []).map(s =>
    `<tr><td class="n">${escapeHtml(s.version)}</td><td>${escapeHtml(s.source)}</td>` +
    `<td class="n">${Math.round(s.size / 1024)} KB</td>` +
    `<td><button data-fw="${escapeHtml(s.version)}">Install</button></td></tr>`).join("") ||
    `<tr><td colspan="4" class="tiny">Nothing staged yet.</td></tr>`;
  $("fwRows").querySelectorAll("button[data-fw]").forEach(b => b.onclick = () => installFirmware(b.dataset.fw));
}

function authHeaders(extra) {
  const h = Object.assign({}, extra || {}), tok = getToken();
  if (tok) h.Authorization = "Bearer " + tok;
  return h;
}

async function fwCall(path, opts) {
  const r = await fetch(path, Object.assign({}, opts, { headers: authHeaders(opts.headers) }));
  if (!r.ok) {
    let why = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); why = j.detail || why; } catch (_) { /* not JSON */ }
    throw new Error(r.status === 401 ? "Psycheion wants its token (🔑 Token, top right)." : why);
  }
  return r.json();
}

async function fetchFirmware() {
  say("fwMsg", "Fetching the latest release and checking it...");
  try {
    const s = await fwCall("/api/soma/firmware/fetch", { method: "POST", headers: { "Content-Type": "application/json" },
                                                         body: JSON.stringify({ version: "latest" }) });
    say("fwMsg", `Staged ${s.version} (${s.source}), checked.`); loadFirmware();
  } catch (e) { say("fwMsg", e.message, true); }
}

async function uploadFirmware() {
  const file = $("fwFile").files[0], version = $("fwVersion").value.trim();
  if (!file || !version) { say("fwMsg", "Pick the .bin and say its version.", true); return; }
  say("fwMsg", "Sending it to the voice server...");
  try {
    const s = await fwCall(`/api/soma/firmware/upload?version=${encodeURIComponent(version)}`,
                           { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: file });
    say("fwMsg", `Staged ${s.version}.`); $("fwFile").value = ""; loadFirmware();
  } catch (e) { say("fwMsg", e.message, true); }
}

async function installFirmware(version) {
  if (!confirm(`Install firmware ${version}? The robot reboots, downloads it, and reboots again into it. ` +
               "Leave it plugged in until its face is back.")) return;
  try {
    const r = await fwCall("/api/soma/firmware/install", { method: "POST", headers: { "Content-Type": "application/json" },
                                                           body: JSON.stringify({ version }) });
    say("fwMsg", r.rebooting ? `${version} armed, and the robot is rebooting to take it. Give it a couple of minutes.`
                             : `${version} armed for the robot's next check-in (it didn't take the reboot: restart it by hand).`);
    loadFirmware();
  } catch (e) { say("fwMsg", e.message, true); }
}

document.addEventListener("DOMContentLoaded", () => {
  $("fwFetch").onclick = fetchFirmware;
  $("fwUpload").onclick = uploadFirmware;
  loadFirmware(); setInterval(loadFirmware, 15000);
  $("suSend").onclick = setupSend;
  $("suCard").onclick = setupCard;
  loadSoma(); loadTouch();
  setInterval(loadSoma, 5000);
  setInterval(loadTouch, 1000);
});
