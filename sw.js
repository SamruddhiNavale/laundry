self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  if (!event.data) return;
  const {room, machine, end, body, kind, turn, deadline} = event.data.json();
  if (kind === 'turn' && deadline * 1000 <= Date.now()) return;
  event.waitUntil(self.registration.showNotification(kind === 'turn' ? 'Your laundry turn' : 'Laundry pickup reminder', {
    body,
    icon: '/icon-192.png',
    tag: kind === 'turn' ? `laundry-turn-${room}-${turn}` : `laundry-pickup-${room}-${machine}`,
    data: {room, machine, end, kind},
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const {room, machine, end, kind} = event.notification.data;
  const url = new URL('/', self.location.origin);
  url.searchParams.set('room', room);
  if (machine && kind !== 'turn') {
    url.searchParams.set('pickup', machine);
    url.searchParams.set('end', end);
  }
  event.waitUntil(self.clients.openWindow(url.href));
});
