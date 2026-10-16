import os
import re
import feedparser
import requests

# 1. 自动从系统读取安全配置
RSSHUB_URL = os.getenv("RSSHUB_URL", "").rstrip("/")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 2. 预设防踩坑的备份订阅源列表（默认优先从不设防的公开大V/热门 List 开始抓，确保百分百能推出来）
# 换成不需要 Twitter 复杂验证的开源/第三方 X 镜像或科技爆文源
FEEDS = [
    f"{RSSHUB_URL}/twitter/keyword/AI min_faves:100"
]

def send_tg_msg(title, link):
    """一键发送 Telegram 消息"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误：GitHub Secrets 里的 TG_BOT_TOKEN 或 TG_CHAT_ID 没有配置！")
        return False
    
    clean_title = re.sub('<[^<]+?>', '', title)[:200]  # 清理网页标签并截取前200字
    msg = f"🔥 <b>发现 X 热门动态</b>\n\n{clean_title}...\n\n🔗 <a href='{link}'>点击查看原文</a>"
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    
    res = requests.post(url, json=payload, timeout=10)
    print(f"👉 Telegram 推送结果: Status {res.status_code}")
    return res.status_code == 200

def run():
    if not RSSHUB_URL:
        print("❌ 错误：GitHub Secrets 里的 RSSHUB_URL 还没填！")
        return

    print(f"📡 准备敲响服务器大门: {RSSHUB_URL}")
    
    for feed_url in FEEDS:
        print(f"正在尝试抓取: {feed_url}")
        feed = feedparser.parse(feed_url)
        
        # 如果抓到了有效数据，立刻推送并退出
        if feed.entries:
            latest = feed.entries[0]
            print(f"✅ 成功抓取到新推文: {latest.title}")
            send_tg_msg(latest.title, latest.link)
            return
            
    print("⚠️ 提示：未抓取到内容。可能是 RSSHub 节点未开放或链接被拦截。")

if __name__ == "__main__":
    run()
