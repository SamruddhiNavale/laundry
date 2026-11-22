self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  if (!event.data) return;
  const {room, machine, end, body} = event.data.json();
  event.waitUntil(self.registration.showNotification('Laundry pickup reminder', {
    body,
    icon: '/icon-192.png',
    tag: `laundry-pickup-${room}-${machine}`,
    data: {room, machine, end},
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const {room, machine, end} = event.notification.data;
  const url = new URL('/', self.location.origin);
  url.searchParams.set('room', room);
  if (machine) {
    url.searchParams.set('pickup', machine);
    url.searchParams.set('end', end);
  }
  event.waitUntil(self.clients.openWindow(url.href));
});
