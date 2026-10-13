/* Triage: the Skip reason sheet, the keyboard (left skips, right shortlists, Z undoes) and swiping the card on a phone.
   Without this script the buttons still work: Skip simply skips without a reason. */
(function () {
  var card = document.getElementById("tcard");
  if (!card) return;
  var dialog = document.getElementById("reasons"), skipBtn = document.getElementById("skipbtn"), yesForm = document.getElementById("yesform");
  var undoBtn = document.querySelector(".tbtn--undo");

  function openSheet() { if (dialog && !dialog.open && dialog.showModal) dialog.showModal(); else document.getElementById("skipform").submit(); }
  function yes() { yesForm.submit(); }
  if (dialog && dialog.showModal) {
    skipBtn.addEventListener("click", function (e) { e.preventDefault(); openSheet(); });
    document.getElementById("reasons-cancel").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
  }

  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (dialog && dialog.open) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); openSheet(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); yes(); }
    else if ((e.key === "z" || e.key === "Z") && undoBtn && !undoBtn.disabled) { undoBtn.click(); }
  });

  // Swiping: drag the card; past the line it decides, otherwise it springs back.
  var startX = null, dx = 0, LINE = 110;
  card.addEventListener("pointerdown", function (e) {
    if (e.target.closest("a, button")) return;
    startX = e.clientX; dx = 0; card.setPointerCapture(e.pointerId); card.classList.add("dragging");
  });
  card.addEventListener("pointermove", function (e) {
    if (startX === null) return;
    dx = e.clientX - startX;
    card.style.transform = "translateX(" + dx + "px) rotate(" + (dx / 18) + "deg)";
    card.classList.toggle("going-yes", dx > 40); card.classList.toggle("going-skip", dx < -40);
  });
  function release() {
    if (startX === null) return;
    startX = null; card.classList.remove("dragging");
    if (dx > LINE) { yes(); return; }
    if (dx < -LINE) { card.style.transform = ""; card.classList.remove("going-skip"); openSheet(); return; }
    card.style.transform = ""; card.classList.remove("going-yes", "going-skip");
  }
  card.addEventListener("pointerup", release);
  card.addEventListener("pointercancel", release);
})();
