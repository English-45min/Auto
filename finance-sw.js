
const CACHE_NAME = "billboard-finance-v4";

const REQUIRED_ASSETS = [
  "./finance.html",
  "./manifest.json"
];

const OPTIONAL_ASSETS = [
  "./icons/finance-192.png",
  "./icons/finance-512.png",
  "./icons/apple-touch-icon.png"
];

// 安装：核心文件必须缓存，图标缺失时不阻止安装
self.addEventListener("install", event => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);

      await cache.addAll(REQUIRED_ASSETS);

      await Promise.allSettled(
        OPTIONAL_ASSETS.map(asset => cache.add(asset))
      );

      await self.skipWaiting();
    })()
  );
});

// 激活：只清理 BILLBOARD 财经看板自己的旧缓存
self.addEventListener("activate", event => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();

      await Promise.all(
        keys
          .filter(key =>
            key.startsWith("billboard-finance-") &&
            key !== CACHE_NAME
          )
          .map(key => caches.delete(key))
      );

      await self.clients.claim();
    })()
  );
});

// 请求：只处理财经看板相关资源
self.addEventListener("fetch", event => {
  const request = event.request;

  if (request.method !== "GET") return;

  const url = new URL(request.url);

  if (url.origin !== self.location.origin) return;

  const isFinanceAsset =
    url.pathname.endsWith("/finance.html") ||
    url.pathname.endsWith("/manifest.json") ||
    url.pathname.includes("/icons/finance-") ||
    url.pathname.endsWith("/icons/apple-touch-icon.png");

  if (!isFinanceAsset) return;

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE_NAME);

      try {
        const response = await fetch(request);

        if (response && response.ok) {
          await cache.put(request, response.clone());
        }

        return response;
      } catch (error) {
        const cached = await cache.match(request);

        if (cached) return cached;

        return new Response("当前资源暂时无法加载，请检查网络连接。", {
          status: 503,
          headers: {
            "Content-Type": "text/plain; charset=utf-8"
          }
        });
      }
    })()
  );
});
