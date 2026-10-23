/* Conversation and proposals live only in this page's memory. */
(() => {
  const panel = document.getElementById("plan-chat");
  if (!panel) return;
  const byId = id => document.getElementById(id);
  const form = byId("chat-form"), input = byId("chat-input");
  const log = byId("chat-messages"), status = byId("chat-status");
  const preview = byId("chat-proposal");
  const ready = panel.dataset.ready === "yes";
  let history = [], proposal = null, busy = false;

  function tell(text, error = false) {
    status.hidden = !text;
    status.textContent = text;
    status.className = error ? "form-message error" : "text-muted";
  }
  function message(role, text) {
    const entry = document.createElement("div");
    entry.className = "chat-message";
    const label = document.createElement("strong"), content = document.createElement("p");
    label.textContent = role === "user" ? "You" : "Opus";
    content.textContent = text;
    entry.append(label, content);
    log.append(entry);
    entry.scrollIntoView({block: "nearest"});
  }
  function pending(value) {
    busy = value;
    ["chat-send", "chat-apply", "chat-discard", "chat-reset"].forEach(id => {
      byId(id).disabled = value || !ready;
    });
    input.disabled = value || !ready;
    panel.setAttribute("aria-busy", String(value));
    byId("chat-send").textContent = value ? "Working…" : "Send";
  }
  function clearProposal() {
    proposal = null;
    preview.hidden = true;
    byId("chat-diff").textContent = "";
  }
  async function request(path, body) {
    const response = await fetch(panel.dataset.endpoint + path, {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin", body: JSON.stringify(body)
    });
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error("The server did not return a response. Try again."); }
    if (!response.ok) throw new Error(data.error || "The request failed. Try again.");
    return data;
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !ready || !input.value.trim()) return;
    if (proposal) { tell("Apply or discard the current proposal before sending another message.", true); return; }
    const text = input.value.trim();
    const next = [...history, {role: "user", content: text}];
    pending(true);
    tell("Opus is reviewing your plan. This may take a couple of minutes.");
    try {
      const data = await request("", {messages: next});
      history = [...next, {role: "assistant", content: data.message}];
      message("user", text);
      message("assistant", data.message);
      input.value = "";
      if (data.proposal) {
        proposal = data.proposal;
        byId("chat-diff").textContent = proposal.diff;
        preview.hidden = false;
      }
      tell(data.validation_error ? "The proposal did not pass validation. Ask Opus to fix it." : "", Boolean(data.validation_error));
    } catch (error) { tell(error.message, true); }
    finally { pending(false); }
  });
  byId("chat-discard").addEventListener("click", () => {
    clearProposal();
    tell("Proposal discarded. The draft has not changed.");
  });
  byId("chat-apply").addEventListener("click", async () => {
    if (!proposal || busy) return;
    pending(true);
    tell("Validating and saving your draft…");
    try {
      await request("/apply", {files: proposal.files, base_digest: proposal.base_digest});
      const url = new URL(window.location.href);
      url.searchParams.delete("chat");
      url.searchParams.set("msg", "Chat changes saved as a draft. Review and sign when ready.");
      window.location.assign(url);
    } catch (error) { tell(error.message, true); pending(false); }
  });
  byId("chat-reset").addEventListener("click", () => {
    if (busy) return;
    history = [];
    log.replaceChildren();
    clearProposal();
    tell("");
    input.value = "";
    input.focus();
  });
  const initial = JSON.parse(byId("chat-initial").textContent);
  if (initial && ready) {
    input.value = initial;
    // Seed the first message for review; sending is an explicit user action.
    panel.scrollIntoView({block: "start"});
    input.focus();
  }
})();
