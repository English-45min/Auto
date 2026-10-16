import os
import re
import feedparser
import requests
from urllib.parse import quote

# 1. 自动从系统读取安全配置
RSSHUB_URL = os.getenv("RSSHUB_URL", "").rstrip("/")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 2. 定义你想搜索的推特关键词（这里可以直接写带空格的正常文字，代码会自动处理）
keyword_query = "AI min_faves:100"
encoded_query = quote(keyword_query)

FEEDS = [
    # 自动把空格转成合法的 URL 编码，杜绝报错
    f"{RSSHUB_URL}/twitter/keyword/{encoded_query}"
]

def send_tg_msg(title, link):
    """一键发送 Telegram 消息"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误：GitHub Secrets 里的 TG_BOT_TOKEN 或 TG_CHAT_ID 没有配置！")
        return False
    
    clean_title = re.sub('<[^<]+?>', '', title)[:200]
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
        
        if feed.entries:
            latest = feed.entries[0]
            print(f"✅ 成功抓取到新推文: {latest.title}")
            send_tg_msg(latest.title, latest.link)
            return
            
    print("⚠️ 提示：未抓取到内容。可能是 RSSHub 节点未开放或链接被拦截。")

if __name__ == "__main__":
    run()
