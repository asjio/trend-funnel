// 趋势漏斗 Service Worker: 网络优先, 失败回退缓存(数据页需新鲜, 不做纯离线)
const CACHE = 'funnel-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).then(r => {
      const clone = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, clone)).catch(() => {});
      return r;
    }).catch(() => caches.match(e.request))
  );
});
