// The plan page while editing (plan.html): its Save button drives the builder frame, and
// leaving with unsaved changes asks first.
// The frame posts {type: "aiab-plan", dirty, canSave, note, bad} whenever that changes; the
// page posts {type: "aiab-plan-save"} back when Save is clicked. A link or form on this page
// opens the site's confirm dialog while changes are unsaved; closing the tab or typing another
// address gets the browser's own prompt, which a page cannot replace.
(function () {
  var frame = document.getElementById("builder");
  var button = document.getElementById("plan-save");
  var note = document.getElementById("plan-save-note");
  var dialog = document.getElementById("confirm");
  if (!frame || !button) return;
  var TEXT = "This plan has unsaved changes. Leaving loses them.";
  var dirty = false, leaving = false;

  window.addEventListener("message", function (event) {
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    var m = event.data;
    if (!m || m.type !== "aiab-plan") return;
    dirty = Boolean(m.dirty);
    button.disabled = !m.canSave;
    if (note) {
      note.textContent = m.note || "";
      note.classList.toggle("bad", Boolean(m.bad));
    }
  });
  button.addEventListener("click", function () {
    frame.contentWindow.postMessage({ type: "aiab-plan-save" }, location.origin);
  });

  window.addEventListener("beforeunload", function (event) {
    if (dirty && !leaving) event.preventDefault();
  });
  function ask(go) {
    if (!dialog || !dialog.showModal) { leaving = true; go(); return; }
    document.getElementById("confirm-text").textContent = TEXT;
    dialog.returnValue = "";
    dialog.addEventListener("close", function () {
      if (dialog.returnValue === "ok") { leaving = true; go(); }
    }, { once: true });
    dialog.showModal();
  }
  document.addEventListener("click", function (event) {
    if (!dirty || leaving) return;
    var link = event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
    if ((link.getAttribute("href") || "").charAt(0) === "#") return;
    event.preventDefault();
    ask(function () { location.href = link.href; });
  }, true);
  document.addEventListener("submit", function (event) {
    if (!dirty || leaving) return;
    var form = event.target;
    if (form.closest("dialog") || form.dataset.leaveOk) return;
    event.preventDefault();
    var submitter = event.submitter;
    ask(function () { form.dataset.leaveOk = "1"; form.requestSubmit(submitter); });
  }, true);
})();
