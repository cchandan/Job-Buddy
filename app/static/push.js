// The "Browser notifications" card: turn on, send a test, turn off.
(function () {
  var card = document.getElementById("notify");
  if (!card) return;
  var key = card.dataset.key, csrf = card.dataset.csrf;
  var status = document.getElementById("notify-status");
  var on = document.getElementById("notify-on"), off = document.getElementById("notify-off"), test = document.getElementById("notify-test");
  if (!key) return;

  function say(text) { status.textContent = text; }
  function show(subscribed) { on.hidden = subscribed; off.hidden = !subscribed; test.hidden = !subscribed; }
  function post(path, fields) {
    var body = new FormData();
    body.append("csrf", csrf);
    Object.keys(fields || {}).forEach(function (k) { body.append(k, fields[k]); });
    return fetch(path, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) { return r.json(); });
  }
  function keyBytes(b64) {
    var pad = "=".repeat((4 - b64.length % 4) % 4), raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }

  var ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var installed = window.navigator.standalone === true || (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
  if (ios && !installed) {  // an iPhone only allows notifications from a website that was added to the Home Screen
    say("On an iPhone, notifications only work from the Home Screen app. In Safari tap Share, then Add to Home Screen. Then open Job Buddy from its new icon and turn notifications on here.");
    on.hidden = true;
    return;
  }
  if (!("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)) {
    say(ios ? "This iPhone can't show notifications. It needs iOS 16.4 or newer." : "This browser can't show notifications. Chrome on a computer or Android phone can.");
    on.hidden = true;
    return;
  }

  function refresh() {
    if (Notification.permission === "denied") { say("Notifications are blocked for this site. Allow them in Chrome's site settings (the padlock by the address), then reload."); show(false); on.hidden = true; return Promise.resolve(); }
    return navigator.serviceWorker.getRegistration("/").then(function (reg) {
      return reg ? reg.pushManager.getSubscription() : null;
    }).then(function (sub) {
      show(!!sub);
      say(sub ? "On for this browser." : "Off for this browser.");
    });
  }

  on.addEventListener("click", function () {
    var note = "";  // shown after the status line refreshes, so it isn't wiped straight away
    on.disabled = true;
    Notification.requestPermission().then(function (perm) {
      if (perm !== "granted") { note = "You didn't allow notifications, so none will be sent."; return; }
      return navigator.serviceWorker.register("/sw.js").then(function () { return navigator.serviceWorker.ready; }).then(function (reg) {
        return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
      }).then(function (sub) {
        var j = sub.toJSON();
        return post("/push/subscribe", { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth });
      });
    }).catch(function () { note = "Couldn't turn notifications on. Please try again in a normal (not Incognito) Chrome window."; })
      .then(function () { on.disabled = false; return refresh(); })
      .then(function () { if (note) say(note); });
  });

  off.addEventListener("click", function () {
    navigator.serviceWorker.getRegistration("/").then(function (reg) {
      return reg ? reg.pushManager.getSubscription() : null;
    }).then(function (sub) {
      if (!sub) return;
      var endpoint = sub.endpoint;
      return sub.unsubscribe().then(function () { return post("/push/unsubscribe", { endpoint: endpoint }); });
    }).then(refresh);
  });

  test.addEventListener("click", function () {
    say("Sending…");
    post("/push/test").then(function (r) { say(r.ok ? "Sent. It should appear in a moment." : "It didn't send. Try turning notifications off and on again."); })
      .catch(function () { say("It didn't send. Please try again."); });
  });

  refresh();
})();
