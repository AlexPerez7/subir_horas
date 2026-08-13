// Service worker minimo: no cachea nada a proposito (los datos vienen
// en vivo de Odoo via el backend, cachearlos generaria datos viejos
// mostrados como si fueran actuales). Solo existe para que el sitio
// cumpla el criterio de instalabilidad de Chrome/Android ("Agregar a
// pantalla de inicio" / "Instalar app") y funcione como PWA liviana.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
