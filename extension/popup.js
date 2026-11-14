// The toolbar popup: is the MAICA desktop app running, and is this tab a
// chat site MAICA watches?
const api = globalThis.browser || globalThis.chrome;

async function main() {
  const app = document.getElementById("app"), appText = document.getElementById("appText");
  const siteDot = document.getElementById("site"), siteText = document.getElementById("siteText");
  const hint = document.getElementById("hint");
  let status = null;
  try {
    const r = await fetch("http://127.0.0.1:47823/status", { cache: "no-store" });
    status = r.ok ? await r.json() : null;
  } catch (e) { status = null; }
  if (status) {
    app.className = "dot ok";
    appText.textContent = status.light ? "Connected to MAICA" : "Desktop app running (MAICA not connected)";
  } else {
    app.className = "dot bad";
    appText.textContent = "MAICA desktop app isn't running";
    hint.textContent = "Start it from the Start menu / Applications. Chat tabs are picked up automatically once it runs.";
  }
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  const rules = await api.runtime.sendMessage({ type: "rules" });
  let host = "";
  try { host = new URL(tab.url).hostname; } catch (e) { /* no URL access for this tab */ }
  const site = (rules.sites || []).find(s => (s.hosts || []).some(h => host === h || host.endsWith("." + h)));
  if (site) {
    const name = (rules.apps[site.app] || {}).name || site.id;
    siteDot.className = "dot ok";
    const live = status && (status.apps || []).find(a => a.name === "web_" + site.id);
    siteText.textContent = `${name}: ` + (live && live.connected ? live.state : "watching");
  } else {
    siteDot.className = "dot";
    siteText.textContent = "This tab isn't a chat site MAICA watches";
  }
}
main();
