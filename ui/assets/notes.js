// The Feedback button (bottom right, base.html) and post-it notes on the page, for everyone
// logged in (server/feedback.py keeps them).
//
// The button opens a menu: "Write feedback" opens the Feedback dialog; "Stick a note on this
// page" starts note mode (crosshair cursor, a banner at the top, Esc cancels), and the next
// click inside the page's main column opens a small form at that spot; Save sends it. A note's spot is kept relative to the nearest element with an id around the
// click (a fraction of its width, pixels below its top), and relative to <main> for when that
// element is gone or hidden, so notes stay put when the window changes width. Every text a
// person typed is set with textContent, never as HTML. Resolved notes are hidden; the lab can
// resolve and reopen. A link ending in #note=<id> (from the lab's Feedback list) shows that
// note highlighted even when it is resolved or notes are hidden. The builder iframe is a
// separate page: clicks inside it do not place notes.
(function () {
  "use strict";
  var main = document.querySelector("main");
  var fb = document.getElementById("fb");
  var banner = document.getElementById("fb-banner");
  if (!main || !fb || !banner || !window.fetch) return;
  var lab = fb.dataset.lab === "1";
  var trigger = fb.querySelector(".fb-button");
  var menu = document.getElementById("fb-menu");
  var writeButton = fb.querySelector("[data-fb-write]");
  var addButton = fb.querySelector("[data-fb-note]");
  var toggle = fb.querySelector("[data-fb-toggle]");
  var more = fb.querySelector(".fb-more");
  var dialog = document.getElementById("feedback");
  var bannerText = banner.querySelector("span");
  var bannerCancel = banner.querySelector("button");
  var bannerTimer = null;
  var page = location.pathname + location.search;
  var focus = (location.hash.match(/^#note=([0-9a-f]{12})$/) || [])[1] || null;
  var layer = document.createElement("div");
  layer.className = "notes-layer";
  main.appendChild(layer);
  var notes = [], hidden = false, placing = false, draft = null;
  try { hidden = localStorage.getItem("aiab-notes-hidden") === "1"; } catch (e) { /* none */ }

  function anchorOf(target) {
    for (var el = target; el && el !== main; el = el.parentElement) {
      if (el.id && /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(el.id)) return el;
    }
    return null;
  }

  // Where a note goes, in pixels from the top left of <main>.
  function spot(note) {
    var m = main.getBoundingClientRect();
    var a = note.anchor ? document.getElementById(note.anchor) : null;
    var left = note.x * m.width, top = note.y;
    if (a && main.contains(a)) {
      var r = a.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { left = r.left - m.left + note.ax * r.width; top = r.top - m.top + note.ay; }
    }
    return { left: Math.max(0, Math.min(left, m.width - 200)), top: Math.max(0, top) };
  }

  function place(el, note) {
    var s = spot(note);
    el.style.left = s.left + "px";
    el.style.top = s.top + "px";
  }

  function when(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " +
      d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  function text(tag, cls, value) {
    var el = document.createElement(tag);
    el.className = cls;
    el.textContent = value;
    return el;
  }

  function card(note) {
    var el = document.createElement("div");
    el.className = "postit" + (note.resolved ? " postit--resolved" : "") + (note.id === focus ? " postit--focus" : "");
    el.dataset.note = note.id;
    el.appendChild(text("p", "postit-text", note.text));
    var who = note.name ? note.name + " · " + note.user : note.user;
    el.appendChild(text("p", "postit-meta", who + " · " + when(note.time)));
    if (lab) {
      var b = text("button", "postit-resolve", note.resolved ? "Reopen" : "Resolve");
      b.type = "button";
      b.addEventListener("click", function () { resolve(note); });
      el.appendChild(b);
    }
    place(el, note);
    return el;
  }

  function open(note) { return !note.resolved; }

  function draw() {
    Array.prototype.slice.call(layer.querySelectorAll(".postit:not(.postit--draft)")).forEach(function (el) { el.remove(); });
    notes.forEach(function (note) {
      if (note.id === focus || (!hidden && open(note))) layer.appendChild(card(note));
    });
    var count = notes.filter(open).length;
    toggle.hidden = count === 0;
    toggle.textContent = (hidden ? "Show notes on this page" : "Hide notes on this page") + " (" + count + ")";
    more.hidden = toggle.hidden && !more.querySelector("a");
  }

  function reposition() {
    layer.querySelectorAll(".postit[data-note]").forEach(function (el) {
      var note = notes.find(function (n) { return n.id === el.dataset.note; });
      if (note) place(el, note);
    });
    if (draft) place(draft.el, draft.pos);
  }

  function send(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  function resolve(note) {
    send("/notes/" + note.id + "/resolve", { resolved: !note.resolved }).then(function (d) {
      note.resolved = d.note.resolved;
      draw();
    }).catch(function () { /* left as it was */ });
  }

  // The banner at the top of the window: the note-mode hint with Cancel, or a short message
  // that goes away by itself.
  function showBanner(message, cancel) {
    clearTimeout(bannerTimer);
    bannerText.textContent = message;
    bannerCancel.hidden = !cancel;
    banner.classList.toggle("fb-banner--done", !cancel);
    banner.hidden = false;
    if (!cancel) bannerTimer = setTimeout(function () { banner.hidden = true; }, 3500);
  }

  function setPlacing(on) {
    placing = on;
    document.body.classList.toggle("notes-placing", on);
    if (on) showBanner("Click where you want the note · Esc to cancel", true);
    else if (!bannerCancel.hidden) banner.hidden = true;
  }

  function closeDraft() {
    if (draft) draft.el.remove();
    draft = null;
    addButton.disabled = false;
  }

  function setMenu(on) {
    menu.hidden = !on;
    trigger.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) writeButton.focus();
  }

  function openDraft(event) {
    var m = main.getBoundingClientRect();
    var pos = { anchor: "", ax: 0, ay: 0, x: (event.clientX - m.left) / m.width, y: event.clientY - m.top };
    var a = anchorOf(event.target);
    if (a) {
      var r = a.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        pos.anchor = a.id; pos.ax = (event.clientX - r.left) / r.width; pos.ay = event.clientY - r.top;
      }
    }
    var form = document.createElement("form");
    form.className = "postit postit--draft";
    var area = document.createElement("textarea");
    area.required = true; area.maxLength = 1000; area.setAttribute("aria-label", "Note");
    var name = document.createElement("input");
    name.maxLength = 80; name.placeholder = "Name (optional)"; name.setAttribute("aria-label", "Name");
    name.autocomplete = "name";
    var row = document.createElement("div");
    row.className = "postit-actions";
    var cancel = text("button", "btn btn--ghost btn--sm", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", closeDraft);
    var save = text("button", "btn btn--primary btn--sm", "Save");
    row.appendChild(cancel); row.appendChild(save);
    form.appendChild(area); form.appendChild(name); form.appendChild(row);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      save.disabled = true;
      send("/notes", { page: page, text: area.value, name: name.value, anchor: pos.anchor, ax: pos.ax, ay: pos.ay, x: pos.x, y: pos.y })
        .then(function (d) {
          notes.unshift(d.note); closeDraft(); hidden = false; draw();
          showBanner("Note saved · the team will see it", false);
        })
        .catch(function () { save.disabled = false; save.textContent = "Not saved · retry"; });
    });
    closeDraft();
    draft = { el: form, pos: pos };
    place(form, pos);
    layer.appendChild(form);
    addButton.disabled = true;
    area.focus();
  }

  // The button opens and closes the menu; while placing a note it cancels note mode.
  trigger.addEventListener("click", function () {
    if (placing) { setPlacing(false); return; }
    setMenu(menu.hidden);
  });
  writeButton.addEventListener("click", function () {
    setMenu(false);
    trigger.focus(); // the dialog gives focus back here when it closes
    if (dialog && dialog.showModal) {
      dialog.showModal();
      var area = dialog.querySelector("textarea");
      if (area) area.focus();
    }
  });
  addButton.addEventListener("click", function () {
    setMenu(false);
    setPlacing(true);
  });
  bannerCancel.addEventListener("click", function () { setPlacing(false); });
  toggle.addEventListener("click", function () {
    hidden = !hidden;
    try { localStorage.setItem("aiab-notes-hidden", hidden ? "1" : ""); } catch (e) { /* none */ }
    draw();
  });
  // A click outside the button and its menu closes the menu. A click inside the builder
  // iframe never reaches this document, but it takes the focus from this window.
  document.addEventListener("click", function (event) {
    if (!menu.hidden && !fb.contains(event.target)) setMenu(false);
  });
  window.addEventListener("blur", function () { if (!menu.hidden) setMenu(false); });
  // While placing, a click in the page drops the note there and does nothing else (no link
  // is followed, no button pressed).
  main.addEventListener("click", function (event) {
    if (!placing || layer.contains(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    setPlacing(false);
    openDraft(event);
  }, true);
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (!menu.hidden) { setMenu(false); trigger.focus(); }
    else if (placing) setPlacing(false);
    else if (draft) closeDraft();
  });
  if (window.ResizeObserver) new ResizeObserver(reposition).observe(main);
  window.addEventListener("resize", reposition);

  fetch("/notes?page=" + encodeURIComponent(page), { credentials: "same-origin", cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : { notes: [] }; })
    .then(function (d) {
      notes = d.notes || [];
      draw();
      var target = focus && layer.querySelector('.postit[data-note="' + focus + '"]');
      if (target) target.scrollIntoView({ block: "center" });
    })
    .catch(function () { /* no notes shown */ });
})();
