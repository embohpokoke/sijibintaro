const CACHE_NAME = 'siji-cache-v1';

const PRECACHE_URLS = [
  '/dashboard/',
  '/manifest.json',
  '/images/icon-192.png',
  '/images/icon-512.png'
];

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SIJI Bintaro - Offline</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    background:#0A0A0A;color:#F0EDE8;display:flex;align-items:center;
    justify-content:center;min-height:100vh;text-align:center;padding:2rem}
  .container{max-width:400px}
  .icon{font-size:4rem;margin-bottom:1.5rem;opacity:.6}
  h1{font-size:1.5rem;font-weight:600;margin-bottom:.75rem;color:#D4A017}
  p{font-size:1rem;color:#A0A0A0;line-height:1.6;margin-bottom:2rem}
  button{background:#D4A017;color:#0A0A0A;border:none;padding:.75rem 2rem;
    border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;
    transition:background .2s}
  button:hover{background:#B8860B}
</style>
</head>
<body>
<div class="container">
  <div class="icon">&#x1F4F6;</div>
  <h1>Anda Sedang Offline</h1>
  <p>Periksa koneksi internet Anda dan coba lagi.</p>
  <button onclick="location.reload()">Coba Lagi</button>
</div>
</body>
</html>`;

// Install: precache core assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch strategy
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API calls: network only, never cache
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ error: 'offline' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' }
        })
      )
    );
    return;
  }

  // Navigation requests: network first, offline fallback
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        })
        .catch(() =>
          caches.match(event.request).then(cached =>
            cached || new Response(OFFLINE_HTML, {
              status: 200,
              headers: { 'Content-Type': 'text/html; charset=utf-8' }
            })
          )
        )
    );
    return;
  }

  // Static assets: cache first, fallback to network
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        // Cache successful responses for static assets
        if (response.ok && (
          url.pathname.match(/\.(js|css|png|jpg|jpeg|svg|ico|woff2?)$/) ||
          url.hostname === 'fonts.googleapis.com' ||
          url.hostname === 'fonts.gstatic.com' ||
          url.hostname === 'cdn.jsdelivr.net'
        )) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      });
    })
  );
});
