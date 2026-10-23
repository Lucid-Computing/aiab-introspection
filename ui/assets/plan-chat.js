/* Only an expired-session message is backed up in sessionStorage; chat stays in memory. */
(() => {
  const panel = document.getElementById("plan-chat");
  if (!panel) return;
  const byId = id => document.getElementById(id);
  const form = byId("chat-form"), input = byId("chat-input");
  const log = byId("chat-messages"), status = byId("chat-status");
  const preview = byId("chat-proposal"), ready = panel.dataset.ready === "yes";
  const storageKey = "aiab-chat-message:" + panel.dataset.endpoint;
  let history = [], events = [], candidate = null, proposal = null;
  let busy = false, controller = null, retryEntry = null;

  function backup(value) {
    try {
      if (value) sessionStorage.setItem(storageKey, value);
      else sessionStorage.removeItem(storageKey);
    } catch (_) { /* In-memory message still survives signing in in a new tab. */ }
  }
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
    return entry;
  }
  function action(text) {
    events.push(`After assistant turn ${history.length / 2}: ${text}`);
    const entry = document.createElement("p");
    entry.className = "chat-event";
    entry.textContent = text;
    log.append(entry);
  }
  function pending(mode) {
    busy = Boolean(mode);
    ["chat-send", "chat-discard", "chat-reset", "chat-revise"].forEach(id => {
      byId(id).disabled = busy || !ready;
    });
    byId("chat-apply").disabled = busy || !ready || !proposal;
    panel.querySelectorAll("[data-chat-starter]").forEach(button => {
      button.disabled = busy || !ready;
    });
    input.disabled = busy || !ready;
    form.setAttribute("aria-busy", String(busy));
    byId("chat-stop").hidden = mode !== "chat";
    byId("chat-send").textContent = mode === "chat" ? "Thinking…" : "Send";
    byId("chat-thinking").hidden = mode !== "chat";
  }
  function clearProposal() {
    candidate = proposal = null;
    preview.hidden = true;
    byId("chat-files").replaceChildren();
  }
  function renderProposal(data) {
    if (data.candidate) candidate = data.candidate;
    else if (data.validation_error) candidate = null;
    if (!data.candidate && !data.validation_error) return; // A conversational reply keeps the proposal.
    proposal = data.proposal || null;
    preview.hidden = false;
    byId("chat-summary").textContent = data.message;
    const validation = byId("chat-validation");
    validation.textContent = proposal
      ? "Structure checked: schema, bundle and code restrictions pass. Behavioral evaluation has not run; classifier correctness is not established. Applying clears prior evaluation results."
      : "Structure check failed: " + data.validation_error + ". Revise this proposal before applying. Behavioral evaluation has not run.";
    validation.className = proposal ? "form-message" : "form-message error";
    const files = byId("chat-files");
    files.replaceChildren();
    const changes = proposal ? proposal.changes : (candidate ? candidate.changes : []);
    byId("chat-file-count").textContent = `${changes.length} changed file${changes.length === 1 ? "" : "s"}`;
    byId("chat-file-list").textContent = changes.map(c => c.path).join(" · ");
    for (const [index, change] of changes.entries()) {
      const details = document.createElement("details"), summary = document.createElement("summary");
      details.className = "chat-file";
      details.open = index === 0;
      summary.textContent = change.path;
      if (change.diff) summary.textContent += `  (+${change.added} / −${change.removed})`;
      details.append(summary);
      const pre = document.createElement("pre");
      pre.className = "cmd chat-diff";
      pre.tabIndex = 0;
      pre.setAttribute("aria-label", change.diff ? `Changes to ${change.path}` : `Invalid candidate: ${change.path}`);
      if (change.diff) {
        for (const [lineIndex, line] of change.diff.split("\n").entries()) {
          const span = document.createElement("span");
          span.textContent = line + "\n";
          span.className = lineIndex > 1 && line.startsWith("+") ? "diff-added"
            : lineIndex > 1 && line.startsWith("-") ? "diff-removed" : "diff-context";
          pre.append(span);
        }
      } else {
        const file = candidate.files.find(f => f.path === change.path);
        pre.textContent = file.content;
      }
      details.append(pre);
      files.append(details);
    }
    byId("chat-apply").disabled = !proposal;
  }
  async function request(path, body, signal) {
    const response = await fetch(panel.dataset.endpoint + path, {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin", body: JSON.stringify(body), signal
    });
    if (response.status === 401) {
      backup(input.value);
      byId("chat-session").hidden = false;
      throw new Error("Your session expired. Sign in in a new tab, then retry here. Your message is preserved.");
    }
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error("The server did not return a response. Try again."); }
    if (!response.ok) throw new Error(data.error || "The request failed. Try again.");
    byId("chat-session").hidden = true;
    return data;
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !ready || !input.value.trim()) return;
    const text = input.value.trim();
    const next = [...history, {role: "user", content: text}];
    // Commit the pair to history only on success; retries reuse this visible user entry.
    if (!retryEntry) retryEntry = message("user", text);
    else retryEntry.querySelector("p").textContent = text;
    retryEntry.dataset.state = "sending";
    controller = new AbortController();
    pending("chat");
    tell("");
    try {
      const context = candidate ? {state: candidate.state, files: candidate.files, base_digest: candidate.base_digest} : null;
      const data = await request("", {messages: next, candidate: context, events}, controller.signal);
      history = [...next, {role: "assistant", content: data.message}];
      retryEntry.dataset.state = "sent";
      retryEntry = null;
      message("assistant", data.message);
      input.value = "";
      backup("");
      renderProposal(data);
      tell(data.validation_error ? "Ask Opus to repair the proposal, or use Revise below." : "", Boolean(data.validation_error));
    } catch (error) {
      retryEntry.dataset.state = "not-sent";
      tell(error.name === "AbortError"
        ? "Stopped waiting. Your message is ready to retry. The server or Anthropic may still finish the request and bill for it."
        : error.message, true);
    } finally {
      controller = null;
      pending(false);
      input.focus({preventScroll: true});
    }
  });
  byId("chat-stop").addEventListener("click", () => controller?.abort());
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  panel.querySelectorAll("[data-chat-starter]").forEach(button => {
    button.addEventListener("click", () => {
      input.value = button.dataset.chatStarter;
      input.focus();
    });
  });
  byId("chat-revise").addEventListener("click", () => {
    if (!input.value.trim()) input.value = proposal
      ? "Make this proposal more conservative and explain the tradeoffs."
      : "Repair the structural validation errors in this proposal, preserving the intended behavior.";
    input.focus();
    input.scrollIntoView({block: "nearest"});
  });
  byId("chat-discard").addEventListener("click", () => {
    action("Proposal discarded. Its edits were not applied; use the saved draft for future requests.");
    clearProposal();
    tell("Proposal discarded. The draft has not changed.");
    input.focus({preventScroll: true});
  });
  byId("chat-apply").addEventListener("click", async () => {
    if (!proposal || busy) return;
    pending("apply");
    tell("Rechecking structure and saving your draft…");
    try {
      const data = await request("/apply", {files: proposal.files, base_digest: proposal.base_digest});
      action(`Proposal applied to the draft (${data.digest}). It has not been deployed.`);
      clearProposal();
      backup("");
      tell(data.warning || "Changes saved as a draft. Continue chatting, or review and sign the plan below.", Boolean(data.warning));
      // Refresh the saved plan while preserving the conversation and composer.
      try {
        const response = await fetch(panel.dataset.endpoint.replace(/\/chat$/, "?edit=1"));
        if (!response.ok || response.redirected) throw new Error("Refresh unavailable");
        const documentCopy = new DOMParser().parseFromString(await response.text(), "text/html");
        const details = documentCopy.getElementById("plan-details");
        if (!details) throw new Error("Plan details unavailable");
        byId("plan-details").replaceWith(details);
      } catch (_) { tell("Changes were saved. Reload the page to refresh the plan details below."); }
    } catch (error) { tell(error.message, true); }
    finally { pending(false); input.focus({preventScroll: true}); }
  });
  function dirty() { return Boolean(history.length || candidate || retryEntry || input.value.trim() || busy); }
  window.addEventListener("beforeunload", event => {
    if (dirty()) { event.preventDefault(); event.returnValue = ""; }
  });
  byId("chat-reset").addEventListener("click", () => {
    if (busy || (dirty() && !window.confirm("Start a new chat? This clears the conversation, typed message and unapplied proposal. Saved draft changes are kept."))) return;
    history = []; events = []; retryEntry = null;
    log.replaceChildren();
    clearProposal(); tell(""); backup("");
    byId("chat-session").hidden = true;
    input.value = "";
    input.focus();
  });
  const initial = JSON.parse(byId("chat-initial").textContent);
  let recovered = "";
  try { recovered = sessionStorage.getItem(storageKey) || ""; } catch (_) { /* Optional backup. */ }
  if ((recovered || initial) && ready) {
    input.value = recovered || initial;
    if (recovered) tell("Recovered your message after sign-in. You can send it again.");
    input.focus({preventScroll: true});
  }
  pending(false);
})();
