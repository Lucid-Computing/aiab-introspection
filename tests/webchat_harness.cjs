// Run the actual inline script against a small DOM/fetch boundary, without a browser dependency.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor() { this.children = []; this.listeners = {}; this.value = ""; this.textContent = ""; this.hidden = false; this.ariaBusy = "false"; }
  append(...items) { this.children.push(...items); }
  replaceChildren() { this.children = []; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() {}
}
const ids = Object.fromEntries(["status", "messages", "benign", "ml_training", "clear", "chat", "prompt", "send", "retry", "loading", "loading-text", "stat-requests", "stat-average", "stat-last", "stat-allowed", "stat-blocked", "stat-errors"].map(id => [id, new Element()]));
const requests = [];
let clock = 0;
const configuredUrl = process.env.AIAB_TEST_URL || "https://localhost:8443";
const baseUrl = configuredUrl.replace(/\/$/, "");
let nextStatus = 200;
let nextBody = { choices: [{ message: { content: "<script>unsafe HTML</script>" } }] };
const context = {
  document: { getElementById: id => ids[id], createElement: () => new Element() },
  window: { AIAB_URL: configuredUrl },
  URL, AbortSignal, performance: { now: () => { clock += 500; return clock; } },
  fetch: async (url, options) => {
    assert.equal(options.redirect, "error");
    assert.equal(options.credentials, "same-origin");
    let status = 200;
    let body;
    if (url === "demo_prompts.json") body = { benign: "dinner", ml_training: "pretraining" };
    else if (url === `${baseUrl}/aiab/health`) body = { status: "ready", enforcement_mode: "block", aiab_mode: "local" };
    else if (url === `${baseUrl}/v1/models`) body = { data: [{ id: "from-model-endpoint" }] };
    else {
      assert.equal(url, `${baseUrl}/v1/chat/completions`);
      assert.equal(ids.loading.hidden, false);
      assert.equal(ids.loading.textContent, "");
      assert.equal(ids["loading-text"].textContent, "Checking policy and generating a response…");
      assert.equal(ids.chat.ariaBusy, "true");
      requests.push(JSON.parse(options.body));
      status = nextStatus; body = nextBody;
    }
    return { status, ok: status === 200, json: async () => body };
  }
};
vm.runInNewContext(fs.readFileSync(0, "utf8"), context);
const settle = () => new Promise(resolve => setImmediate(resolve));
const submit = async (value) => {
  ids.prompt.value = value;
  await ids.chat.listeners.submit({ preventDefault() {} });
};
(async () => {
  await settle();
  assert.equal(ids.status.textContent, `AIAB: ${baseUrl} · transport: local · mode: block · local`);
  assert.equal(ids.send.disabled, false);
  assert.equal(ids.loading.hidden, true);
  assert.equal(ids.chat.ariaBusy, "false");
  ids.benign.listeners.click();
  assert.equal(ids.prompt.value, "dinner");
  await submit("Hello");
  assert.equal(ids.loading.hidden, true);
  assert.equal(ids.chat.ariaBusy, "false");
  assert.equal(requests[0].model, "from-model-endpoint");
  assert.equal(requests[0].stream, false);
  assert.equal(ids["stat-requests"].textContent, "1");
  assert.equal(ids["stat-average"].textContent, "500 ms");
  assert.equal(ids["stat-last"].textContent, "500 ms");
  assert.equal(ids["stat-allowed"].textContent, "1");
  assert.equal(ids["stat-blocked"].textContent, "0");
  assert.equal(ids["stat-errors"].textContent, "0");
  assert.equal(ids.messages.children[1].children[1].textContent, "<script>unsafe HTML</script>");
  nextStatus = 403;
  nextBody = { error: { code: "aiab_blocked", message: "Policy decision", classifier_id: "ai_capabilities/v1", confidence: 0.95 } };
  await submit("pretraining");
  assert.equal(ids["stat-requests"].textContent, "2");
  assert.equal(ids["stat-blocked"].textContent, "1");
  assert.equal(requests[1].messages.length, 3);
  const blocked = ids.messages.children.at(-1);
  assert.match(blocked.className, /blocked/);
  assert.equal(blocked.children[0].textContent, "Blocked by AIAB");
  assert.match(blocked.children[1].textContent, /Policy decision\nClassifier: ai_capabilities\/v1\nConfidence: 0.95/);
  nextStatus = 503;
  nextBody = { error: { message: "Unavailable" } };
  await submit("Hello again");
  assert.equal(ids["stat-errors"].textContent, "1");
  assert.equal(requests[2].messages.length, 3);
  assert.equal(requests[2].messages.some(message => message.content === "pretraining"), false);
  assert.match(ids.messages.children.at(-1).className, /error/);
  assert.match(ids.messages.children.at(-1).children[0].textContent, /HTTP 503/);
  nextStatus = 403;
  nextBody = { error: { code: "different_error", message: "Denied" } };
  await submit("Other error");
  assert.match(ids.messages.children.at(-1).className, /error/);
  ids.clear.listeners.click();
  assert.equal(ids.messages.children.length, 0);
  nextStatus = 200; nextBody = { choices: [{ message: { content: "Fresh" } }] };
  await submit("New conversation");
  assert.equal(requests.at(-1).messages.length, 1);
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
