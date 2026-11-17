/* BILLBOARD Finance PWA service worker
 * 独立于仓库原有 sw.js，不接管 Telegram Bot 或瓜子雷达。
 */
"use strict";

const CACHE_PREFIX = "billboard-finance-pwa-";
const CACHE_VERSION = "v1";
const CACHE_NAME = CACHE_PREFIX + CACHE_VERSION;

const PAGE_URL = new URL("./finance.html", self.registration.scope).href;
const ICON_URLS = [
  new URL("./icons/finance-192.png", self.registration.scope).href,
  new URL("./icons/finance-512.png", self.registration.scope).href
];

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    // 页面预缓存失败不应阻止安装；运行时仍会尝试联网加载。
    try {
      const response = await fetch(PAGE_URL, { cache: "reload" });
      if (response.ok) await cache.put(PAGE_URL, response);
    } catch (_) {}
    await Promise.all(ICON_URLS.map(async url => {
      try {
        const response = await fetch(url, { cache: "reload" });
        if (response.ok) await cache.put(url, response);
      } catch (_) {}
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys
      .filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
      .map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  const scopeUrl = new URL(self.registration.scope);

  // 只处理当前 GitHub Pages 项目路径下的请求。
  if (url.origin !== scopeUrl.origin || !url.pathname.startsWith(scopeUrl.pathname)) return;

  // 页面使用 network-first，确保 update.py 发布的新新闻数据能及时显示。
  if (request.mode === "navigate" || url.href === PAGE_URL) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      try {
        const fresh = await fetch(request);
        if (fresh && fresh.ok) await cache.put(PAGE_URL, fresh.clone());
        return fresh;
      } catch (_) {
        const cached = await cache.match(PAGE_URL);
        if (cached) return cached;
        return new Response(
          "<!doctype html><meta charset=utf-8><title>离线</title><p>当前无法联网，财经看板暂时不可用。请恢复网络后重试。</p>",
          { headers: { "Content-Type": "text/html; charset=utf-8" }, status: 503 }
        );
      }
    })());
    return;
  }

  // 图标采用 cache-first，减少重复下载。
  if (ICON_URLS.includes(url.href)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(request);
      if (cached) return cached;
      const response = await fetch(request);
      if (response && response.ok) await cache.put(request, response.clone());
      return response;
    })());
  }
});
