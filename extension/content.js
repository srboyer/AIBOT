// MAICA browser extension -- content script.
//
// Runs in each AI chat tab (the sites in maica_rules.json) and works out what
// the page is doing right now:
//   running   a visible Stop button (nearly every chat UI swaps Send for Stop
//             while it writes) -- the site's own selectors, plus a generic
//             check of every button's label against the rules' stop_label
//   thinking  running, and a "Thinking..." indicator is showing
//   question  the site's "asking you" widget is showing
//   limited   a usage-limit banner near the message box ("You've hit your
//             limit...", "You're out of free messages...")
// and whether you're looking at it (focused). It reports through the
// background worker to the MAICA desktop app on this PC, at once when
// anything changes and every couple of seconds as a heartbeat.
//
// It only reads labels, banner text and page structure -- never your
// messages or the replies -- and sends nothing but the four states above.

(() => {
  "use strict";
  const api = globalThis.browser || globalThis.chrome;
  const TEST = !!globalThis.__MAICA_TEST__;
  let rules = null, site = null, stopRe = null, limitRe = null, thinkRe = null;
  let last = null, lastSent = 0, timer = null, limitCache = { at: 0, value: false };

  function visible(el) {
    if (!el || el.hidden || el.closest("[hidden],[aria-hidden='true']")) return false;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return false;
    return el.getClientRects().length > 0 || TEST;
  }

  function anyVisible(selectors) {
    for (const sel of selectors || []) {
      let list;
      try { list = document.querySelectorAll(sel); } catch (e) { continue; }  // a bad selector in the rules
      for (const el of list) if (visible(el)) return true;
    }
    return false;
  }

  function label(el) {
    return (el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || "").trim();
  }

  function stopVisible() {
    if (anyVisible(site.stop)) return true;
    for (const el of document.querySelectorAll("button, [role='button']")) {
      const l = label(el);
      if (l.length < 40 && stopRe.test(l) && visible(el)) return true;
    }
    return false;
  }

  function thinkingVisible() {
    const t = site.thinking || {};
    if (anyVisible(t.selectors)) return true;
    if (!thinkRe) return false;
    const cands = document.querySelectorAll(
      "summary, button, [aria-live], [role='status'], [class*='think' i], [class*='reason' i], [data-testid*='think' i]");
    for (const el of cands) {
      const text = (el.textContent || "").trim();
      if (text.length <= 60 && thinkRe.test(text) && visible(el)) return true;
    }
    return false;
  }

  // Short texts near the message box only; checked every few seconds at most.
  function limitShowing() {
    const now = Date.now();
    if (now - limitCache.at < (TEST ? 0 : 3000)) return limitCache.value;
    let found = false;
    const scopes = site.limit_scope || "form, [role='alert'], [role='status']";
    let roots;
    try { roots = document.querySelectorAll(scopes); } catch (e) { roots = []; }
    outer: for (const root of roots) {
      let n = 0;
      for (const el of root.querySelectorAll("*")) {
        if (++n > 400) break;
        if (el.childElementCount > 3) continue;
        const text = (el.innerText !== undefined ? el.innerText : el.textContent || "").trim();
        if (text && text.length <= 200 && limitRe.test(text) && visible(el)) { found = true; break outer; }
      }
    }
    limitCache = { at: now, value: found };
    return found;
  }

  function evaluate() {
    const running = stopVisible();
    let level = "idle";
    if (anyVisible(site.question)) level = "question";
    else if (running) level = thinkingVisible() ? "thinking" : "running";
    const limited = level === "idle" ? limitShowing() : (limitCache.value && false);
    const focused = document.visibilityState === "visible" && document.hasFocus();
    return { level, limited, focused };
  }

  function report(force) {
    if (!site) return;
    let state;
    try { state = evaluate(); } catch (e) { return; }
    const changed = !last || state.level !== last.level || state.limited !== last.limited || state.focused !== last.focused;
    const beat = document.visibilityState === "visible" ? 2000 : 5000;
    if (!changed && !force && Date.now() - lastSent < beat) return;
    last = state;
    lastSent = Date.now();
    try {
      api.runtime.sendMessage({ type: "state", site: site.id, ...state, t: Date.now() });
    } catch (e) { /* extension reloaded: this page's script is orphaned */ }
  }

  function schedule() {
    if (timer) return;
    timer = setTimeout(() => { timer = null; report(false); }, 120);
  }

  function matchSite(r) {
    const host = location.hostname;
    return (r.sites || []).find(s =>
      (s.hosts || []).some(h => host === h || host.endsWith("." + h)) &&
      (!s.path || new RegExp(s.path).test(location.pathname))) || null;
  }

  function start(r) {
    rules = r;
    site = matchSite(r);
    if (!site) return;
    stopRe = new RegExp(r.stop_label || "^\\s*stop\\b", "i");
    limitRe = new RegExp(r.limit_banner, "i");
    thinkRe = site.thinking && site.thinking.text ? new RegExp(site.thinking.text, "i") : null;
    new MutationObserver(schedule).observe(document.documentElement, {
      subtree: true, childList: true, attributes: true,
      attributeFilter: ["aria-label", "title", "disabled", "hidden", "class", "data-state", "style", "aria-hidden"],
    });
    for (const ev of ["visibilitychange", "focus", "blur"]) addEventListener(ev, () => report(false), true);
    setInterval(() => report(false), 1000);
    report(true);
  }

  if (TEST) { globalThis.__maica = { start, evaluate, report }; return; }
  api.runtime.sendMessage({ type: "rules" }).then(r => r && start(r)).catch(() => {});
})();
