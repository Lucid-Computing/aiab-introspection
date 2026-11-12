// The site's own dropdown. Every <select> on a page is kept in its form (it still carries
// the value and its change handlers) but hidden; a button and a list stand in for it.
(function () {
  function enhance(select) {
    if (select.dataset.sel) return;
    select.dataset.sel = "1";
    var box = document.createElement("span");
    box.className = "sel";
    var button = document.createElement("button");
    button.type = "button";
    button.className = "sel-btn";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    if (select.id) { button.id = select.id + "-button"; }
    var value = document.createElement("span");
    value.className = "sel-value";
    var caret = document.createElement("span");
    caret.className = "sel-caret";
    caret.setAttribute("aria-hidden", "true");
    button.appendChild(value);
    button.appendChild(caret);
    var list = document.createElement("ul");
    list.className = "sel-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;

    function show() {
      var chosen = select.options[select.selectedIndex];
      value.textContent = chosen ? chosen.textContent : "";
      Array.prototype.forEach.call(list.children, function (li, k) {
        li.firstChild.classList.toggle("on", k === select.selectedIndex);
        li.setAttribute("aria-selected", k === select.selectedIndex ? "true" : "false");
      });
    }
    function close() {
      list.hidden = true;
      box.classList.remove("is-open");
      button.setAttribute("aria-expanded", "false");
    }
    Array.prototype.forEach.call(select.options, function (option, k) {
      var li = document.createElement("li");
      li.setAttribute("role", "option");
      var item = document.createElement("button");
      item.type = "button";
      item.textContent = option.textContent;
      item.addEventListener("click", function () {
        close();
        if (select.selectedIndex === k) return;
        select.selectedIndex = k;
        show();
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      li.appendChild(item);
      list.appendChild(li);
    });
    button.addEventListener("click", function () {
      var open = list.hidden;
      list.hidden = !open;
      box.classList.toggle("is-open", open);
      button.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("mousedown", function (e) { if (!box.contains(e.target)) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    select.addEventListener("change", show);
    box.appendChild(button);
    box.appendChild(list);
    select.hidden = true;
    select.parentNode.insertBefore(box, select.nextSibling);
    show();
  }
  function all() { Array.prototype.forEach.call(document.querySelectorAll("select"), enhance); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", all); else all();
})();
