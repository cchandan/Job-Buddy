// Shows Job Buddy notifications and opens the right page when one is clicked.
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data.json(); } catch (e) { data = {}; }
  event.waitUntil(self.registration.showNotification(data.title || "Job Buddy", {
    body: data.body || "",
    tag: data.tag || undefined,
    data: { url: data.url || "/" }
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (open) {
    for (var i = 0; i < open.length; i++) {
      if ("focus" in open[i]) { open[i].navigate(url); return open[i].focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
