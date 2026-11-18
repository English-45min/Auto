
/*
 * BILLBOARD Finance Dashboard
 * Dedicated Service Worker
 *
 * 只缓存财经看板自己的文件。
 * 不删除其他应用的缓存。
 * 不拦截其他应用的网络请求。
 */

const CACHE_NAME = "billboard-finance-v3";

const FINANCE_ASSETS = [
  "./finance.html",
  "./manifest.json",
  "./icons/finance-192.png",
  "./icons/finance-512.png",
  "./icons/apple-touch-icon.png"
];

// 判断请求是否属于财经看板
function isFinanceAsset(url) {
  if (url.origin !== self.location.origin) {
    return false;
  }

  const path = url.pathname;

  return (
    path.endsWith("/finance.html") ||
    path.endsWith("/manifest.json") ||
    path.endsWith("/icons/finance-192.png") ||
    path.endsWith("/icons/finance-512.png") ||
    path.endsWith("/icons/apple-touch-icon.png")
  );
}

// 安装：只缓存财经看板文件
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(FINANCE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// 激活：只清理财经看板自己的旧版本缓存
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => {
        const oldFinanceCaches = keys.filter(key =>
          key.startsWith("billboard-finance-") &&
          key !== CACHE_NAME
        );

        return Promise.all(
          oldFinanceCaches.map(key => caches.delete(key))
        );
      })
      .then(() => self.clients.claim())
  );
});

// 网络优先，网络失败时使用财经看板缓存
self.addEventListener("fetch", event => {
  const request = event.request;

  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);

  if (!isFinanceAsset(url)) {
    return;
  }

  event.respondWith(
    fetch(request)
      .then(response => {
        if (response && response.ok) {
          const copy = response.clone();

          caches.open(CACHE_NAME)
            .then(cache => cache.put(request, copy));
        }

        return response;
      })
      .catch(() => {
        return caches.match(request).then(cached => {
          if (cached) {
            return cached;
          }

          return new Response(
            "当前网络不可用，财经看板资源暂时无法加载。",
            {
              status: 503,
              headers: {
                "Content-Type": "text/plain; charset=utf-8"
              }
            }
          );
        });
      })
  );
});
