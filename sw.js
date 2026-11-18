
/*
 * BILLBOARD / Auto Repository
 * Shared Service Worker - Safe Compatibility Version
 *
 * 作用：
 * 1. 保留旧 Service Worker 的更新入口。
 * 2. 不删除其他网页应用的缓存。
 * 3. 不拦截其他网页的网络请求。
 * 4. 不负责财经看板的缓存。
 *
 * 财经看板使用独立的 finance-sw.js。
 */

const SHARED_SW_VERSION = "auto-shared-sw-v2";

self.addEventListener("install", event => {
  event.waitUntil(
    self.skipWaiting()
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    self.clients.claim()
  );
});

// 不设置 fetch 事件。
// 让浏览器正常处理网络请求，避免影响其他网页。
