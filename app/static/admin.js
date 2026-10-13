/* Admin pages: keyword chips, the Bin's selection bar and filter, and the "review" toggle. No build step, no libraries. */
(function () {
  // Keywords as chips. The plain text box stays in the page and is what gets saved, so nothing breaks without this script.
  document.querySelectorAll("[data-chips]").forEach(function (box) {
    var source = box.querySelector("[data-chips-source]"), list = box.querySelector("[data-chip-list]");
    var adder = box.querySelector("[data-chip-add]"), input = box.querySelector("[data-chip-input]");
    var button = box.querySelector("[data-chip-button]"), hint = box.querySelector("[data-chips-hint]");
    var items = [];
    function parse(text) { return text.split(/[,\n]/).map(function (t) { return t.trim(); }).filter(Boolean); }
    function sync() { source.value = items.join(", "); }
    function draw() {
      list.textContent = "";
      items.forEach(function (term, i) {
        var chip = document.createElement("span"); chip.className = "chip-item"; chip.appendChild(document.createTextNode(term));
        var x = document.createElement("button"); x.type = "button"; x.setAttribute("aria-label", "Remove " + term);
        x.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
        x.addEventListener("click", function () { items.splice(i, 1); sync(); draw(); });
        chip.appendChild(x); list.appendChild(chip);
      });
    }
    function add() {
      parse(input.value).forEach(function (t) { if (items.map(function (i) { return i.toLowerCase(); }).indexOf(t.toLowerCase()) < 0) items.push(t); });
      input.value = ""; sync(); draw(); input.focus();
    }
    items = parse(source.value);
    source.hidden = true; list.hidden = false; adder.hidden = false; if (hint) hint.hidden = true;
    draw();
    button.addEventListener("click", add);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); add(); } });
    box.closest("form").addEventListener("submit", function () { if (input.value.trim()) add(); sync(); });
  });

  // The Bin: a bar appears once something is ticked.
  document.querySelectorAll("form[data-selectbar]").forEach(function (form) {
    var bar = form.querySelector(".selectbar"), count = form.querySelector("[data-count]"), all = form.querySelector("[data-all]");
    function boxes() { return form.querySelectorAll('input[name="job"]'); }
    function update() {
      var n = 0; boxes().forEach(function (c) { if (c.checked) n++; });
      count.textContent = n; bar.hidden = n === 0;
      if (all) all.checked = n > 0 && n === boxes().length;
    }
    form.addEventListener("change", function (e) { if (e.target === all) boxes().forEach(function (c) { c.checked = all.checked; }); update(); });
    form.querySelector("[data-clear]").addEventListener("click", function () { boxes().forEach(function (c) { c.checked = false; }); update(); });
  });

  // The Bin's search box filters the rows already on the page.
  document.querySelectorAll("[data-filter]").forEach(function (input) {
    var body = document.getElementById(input.getAttribute("data-filter"));
    if (!body) return;
    input.addEventListener("input", function () {
      var needle = input.value.trim().toLowerCase();
      body.querySelectorAll("tr").forEach(function (row) { row.hidden = needle && row.textContent.toLowerCase().indexOf(needle) < 0; });
    });
  });

  // "Review the 18" opens the list.
  document.querySelectorAll("[data-open]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      var target = document.getElementById(a.getAttribute("data-open"));
      if (!target) return;
      e.preventDefault(); target.classList.toggle("show");
      target.querySelectorAll("details").forEach(function (d) { d.open = target.classList.contains("show"); });
    });
  });
})();
