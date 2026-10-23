const CACHE_NAME = 'terminal-news-v3';
const ASSETS_TO_CACHE = [
  './',
  './index.html'
];

// 安装时预缓存基础骨架
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

// 激活时清理旧缓存
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clientsClaim();
});

// 核心：网络优先策略 (Network-First)
self.addEventListener('fetch', (event) => {
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        // 有网络时：请求成功，克隆一份存入缓存，并返回最新内容
        const responseClone = networkResponse.clone();
        caches.open(CACHE_NAME).then((cache) => {
          cache.put(event.request, responseClone);
        });
        return networkResponse;
      })
      .catch(() => {
        // 没网络（断网/弱网失败）时：降级从本地缓存读取
        return caches.match(event.request);
      })
  );
});
