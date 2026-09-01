const CACHE_NAME = "nova-web-v13";
const APP_SHELL = [
  "./",
  "./index.html",
  "./styles.css?v=13",
  "./app.js?v=13",
  "./manifest.webmanifest",
  "./icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request).then((response) => {
      if (response.ok && new URL(event.request.url).origin === self.location.origin) {
        event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone())));
      }
      return response;
    }).catch(() => caches.open(CACHE_NAME).then((cache) => (
      cache.match(event.request).then((cached) => cached || new Response("Нет подключения", { status: 503 }))
    ))),
  );
});
