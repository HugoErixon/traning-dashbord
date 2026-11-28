// Service worker för mobilappen (scope /app/).
//
// Appen har en egen worker i stället för webbsidans /sw.js. En hemskärmsapp på
// iPhone öppnar bara länkar inom sitt eget scope; med webbsidans worker
// öppnades notiserna i Safari på webbsidan i stället för i appen.
//
// Precis som webbsidans worker cachar den ingenting: appen ska visa färsk data.
// Notiser kommer även när appen är stängd. Det enda kravet är att appen lagts
// till på hemskärmen och att notiser slagits på inifrån den.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

const APP_ROOT = '/app/';

// Servern skickar redan app-länkar till app-prenumerationer, men en äldre
// länk till webbsidan ('/…') pekas ändå om hit så att den aldrig lämnar appen.
function appUrl(url) {
  if (!url || url === '/') return APP_ROOT;
  if (url.startsWith(APP_ROOT)) return url;
  if (url.startsWith('/')) return '/app' + url;
  return APP_ROOT;
}

self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = { body: event.data ? event.data.text() : '' };
  }
  event.waitUntil(self.registration.showNotification(data.title || 'Trainyze', {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || 'trainyze',
    // Morgonnotisen öppnar alltid dagssammanfattningen, även när den skickas
    // av en äldre server som bara länkar till '/'.
    data: { url: data.tag === 'morning-report' && !String(data.url || '').includes('vy=') ? '/app/?vy=morgon' : appUrl(data.url) },
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = new URL(appUrl(event.notification.data && event.notification.data.url), self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const open = windows.find(client => new URL(client.url).pathname.startsWith(APP_ROOT));
    if (open) {
      // Appen är redan igång: be den byta vy utan omladdning, och navigera
      // som reserv om sidan inte lyssnar.
      open.postMessage({ type: 'open-url', url: target });
      try { await open.focus(); } catch (err) { /* iOS tillåter inte alltid fokus */ }
      return;
    }
    if (self.clients.openWindow) await self.clients.openWindow(target);
  })());
});
