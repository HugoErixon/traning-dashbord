// Service worker för Trainyze.
//
// Enda uppgiften är notiser. Den cachar medvetet ingenting: dashboarden visar
// färsk tränings- och hälsodata, och en offline-cache skulle riskera att visa
// gårdagens siffror som om de vore dagens.
//
// På iPhone körs den här filen bara när sajten lagts till på hemskärmen.
// Safari tillåter inte push från en vanlig flik.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  // En push utan payload är fortfarande värd att visa — bättre med en generisk
  // notis än en tyst som användaren aldrig får se.
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = { body: event.data ? event.data.text() : '' };
  }

  const title = data.title || 'Trainyze';
  const options = {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || 'trainyze',
    data: { url: data.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';

  // Fokusera ett fönster som redan är öppet i stället för att starta ett till.
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
      for (const client of clients) {
        if ('focus' in client) {
          if ('navigate' in client && target !== '/') client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
    })
  );
});
