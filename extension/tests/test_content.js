// Runs the extension's content.js inside jsdom pages that mimic chat UIs and
// checks what it reports. node test_content.js <path to extension>
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const ext = process.argv[2] || path.join(__dirname, "..");
const src = fs.readFileSync(path.join(ext, "content.js"), "utf8");
const rules = JSON.parse(fs.readFileSync(path.join(ext, "maica_rules.json"), "utf8"));
let failures = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures++;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${label}: ${JSON.stringify(got)}${ok ? "" : " (wanted " + JSON.stringify(want) + ")"}`);
}

function page(url, body) {
  const dom = new JSDOM(`<!doctype html><html><body>${body}</body></html>`, { url, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  w.__MAICA_TEST__ = true;
  w.chrome = { runtime: { sendMessage: () => Promise.resolve(null) } };
  w.eval(src);
  w.__maica.start(rules);
  return w;
}

// ChatGPT-like page
let w = page("https://chatgpt.com/c/123", `
  <main><div class="msg">You've hit your limit of patience? Let's talk about usage limits.</div></main>
  <form><textarea></textarea><button data-testid="send-button" aria-label="Send prompt">Send</button></form>`);
let d = w.document;
check("chatgpt idle", w.__maica.evaluate().level, "idle");
check("a message mentioning limits isn't a banner", w.__maica.evaluate().limited, false);
const stop = d.createElement("button");
stop.setAttribute("data-testid", "stop-button"); stop.setAttribute("aria-label", "Stop streaming");
d.querySelector("form").appendChild(stop);
check("stop button -> running", w.__maica.evaluate().level, "running");
const think = d.createElement("span"); think.className = "thinking-indicator"; think.textContent = "Thinking";
d.querySelector("main").appendChild(think);
check("thinking indicator -> thinking", w.__maica.evaluate().level, "thinking");
stop.remove(); think.remove();
const q = d.createElement("input"); q.setAttribute("aria-label", "Custom answer for Where are you going?");
d.querySelector("main").appendChild(q);
check("question widget -> question", w.__maica.evaluate().level, "question");
q.remove();
const banner = d.createElement("div"); banner.textContent = "You've hit the Free plan limit for GPT-5. Responses will use another model until your limit resets after 4:12 PM.";
d.querySelector("form").prepend(banner);
check("limit banner by the composer -> limited", w.__maica.evaluate().limited, true);

// A site with only generic rules (Le Chat): a plain "Stop" button counts...
w = page("https://chat.mistral.ai/chat/1", `<form><textarea></textarea><button>Stop</button></form>`);
check("generic stop label -> running", w.__maica.evaluate().level, "running");
// ...but "Stop sharing" and hidden stop buttons don't.
w = page("https://chat.mistral.ai/chat/1", `<form><button>Stop sharing</button><button style="display:none">Stop</button><button hidden>Stop</button></form>`);
check("'Stop sharing' / hidden Stop -> idle", w.__maica.evaluate().level, "idle");

// claude.ai: its question placeholder
w = page("https://claude.ai/chat/abc", `<fieldset><div contenteditable data-placeholder="Reply directly to Claude..."></div></fieldset>`);
check("claude 'Reply directly' -> question", w.__maica.evaluate().level, "question");

// Pages that aren't chat sites don't report at all.
w = page("https://example.com/", `<button>Stop</button>`);
check("non-chat site: no site matched", w.__maica.evaluate === undefined ? "n/a" : (() => { try { return w.__maica.evaluate().level; } catch (e) { return "not watching"; } })(), "not watching");

console.log(failures ? `\n${failures} FAILED` : "\nAll extension checks passed.");
process.exit(failures ? 1 : 0);
