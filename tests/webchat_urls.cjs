// Exercise the actual browser script and assert invalid configuration cannot
// trigger API requests, even where WHATWG URL would silently normalize it.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { script, valid, invalid } = JSON.parse(fs.readFileSync(0, "utf8"));
class Element {
  append() {}
  replaceChildren() {}
  addEventListener() {}
}
async function run(url, accepted) {
  const requests = [];
  const elements = {};
  const context = {
    window: { AIAB_URL: url },
    URL, AbortSignal,
    document: {
      getElementById: id => elements[id] ??= new Element(),
      createElement: () => new Element()
    },
    fetch: async target => {
      requests.push(target);
      const body = target.endsWith("/aiab/health")
        ? { status: "ready", enforcement_mode: "block", aiab_mode: "local" }
        : target.endsWith("/v1/models") ? { data: [{ id: "demo" }] }
        : { benign: "dinner", ml_training: "pretraining" };
      return { ok: true, status: 200, json: async () => body };
    }
  };
  vm.runInNewContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  const expected = ["demo_prompts.json"];
  if (accepted) {
    const base = url.replace(/\/$/, "");
    expected.push(`${base}/aiab/health`, `${base}/v1/models`);
  }
  assert.deepEqual(requests, expected, JSON.stringify(url));
  assert.equal(elements.send.disabled, !accepted, JSON.stringify(url));
}
(async () => {
  for (const url of valid) await run(url, true);
  for (const url of invalid) await run(url, false);
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
