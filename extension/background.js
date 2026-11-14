// MAICA browser extension -- background worker.
//
// Relays each chat tab's state (from content.js) to the MAICA desktop app on
// this PC (http://127.0.0.1:47823, see Windows/event_server.py) and carries
// out what it sends back -- "focus" brings a tab forward when you double-tap
// MAICA. Also hands content scripts their rules: the desktop app's copy when
// it's running (so a rules fix reaches the extension without an update),
// else the copy packaged with the extension.

const api = globalThis.browser || globalThis.chrome;
const APP = "http://127.0.0.1:47823";
const tabSites = new Map();   // tab id -> site, to say goodbye when a tab closes
let rulesCache = null, rulesAt = 0, appUp = false;

async function packagedRules() {
  const r = await fetch(api.runtime.getURL("maica_rules.json"));
  return r.json();
}

async function getRules() {
  if (rulesCache && Date.now() - rulesAt < 10 * 60 * 1000) return rulesCache;
  try {
    const r = await fetch(APP + "/rules", { cache: "no-store" });
    if (r.ok) { rulesCache = await r.json(); rulesAt = Date.now(); return rulesCache; }
  } catch (e) { /* desktop app not running */ }
  if (!rulesCache) { rulesCache = await packagedRules(); rulesAt = Date.now() - 9 * 60 * 1000; }
  return rulesCache;
}

async function send(payload) {
  try {
    const r = await fetch(APP + "/event", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-MAICA": "1" },
      body: JSON.stringify(payload),
    });
    appUp = r.ok;
    if (!r.ok) return;
    const reply = await r.json();
    for (const cmd of reply.commands || []) await run(cmd);
  } catch (e) {
    appUp = false;
  }
}

async function run(cmd) {
  if (cmd.cmd === "focus" && cmd.tab) {
    const id = Number(cmd.tab);
    try {
      const tab = await api.tabs.update(id, { active: true });
      if (tab && tab.windowId !== undefined) await api.windows.update(tab.windowId, { focused: true });
    } catch (e) { /* tab gone */ }
  }
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "rules") {
    getRules().then(sendResponse);
    return true;  // answering asynchronously
  }
  if (msg.type === "state" && sender.tab) {
    tabSites.set(sender.tab.id, msg.site);
    send({ source: "ext", key: String(sender.tab.id), site: msg.site, level: msg.level,
           limited: !!msg.limited, focused: !!msg.focused, t: msg.t });
  }
  if (msg.type === "status") {
    sendResponse({ appUp });
  }
  return false;
});

api.tabs.onRemoved.addListener((tabId) => {
  const site = tabSites.get(tabId);
  if (!site) return;
  tabSites.delete(tabId);
  send({ source: "ext", key: String(tabId), site, level: "idle", closed: true });
});
