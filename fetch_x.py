import os
import requests
import feedparser
import urllib.parse

# 从环境变量中读取密钥
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# 订阅的 X (Twitter) 热门中文账号列表或关键词 (使用公共/自建 RSSHub 路由)
# 格式: https://rsshub.app/twitter/user/账号名
RSS_URLS = [
    "https://rsshub.app/twitter/user/getodkn",      # 示例账号
    "https://rsshub.app/twitter/keyword/AI%20中文",  # 关键词搜索
]

def send_telegram_message(text):
    """发送消息到 Telegram"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    res = requests.post(url, json=payload)
    return res.status_code == 200

def fetch_and_push():
    posts = []
    
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]: # 每个源取最新的 3 条
                title = entry.get("title", "")
                link = entry.get("link", "")
                author = feed.feed.get("title", "X 博主")
                
                # 组装格式化消息
                posts.append(f"🔥 <b>【{author}】</b>\n{title[:150]}...\n🔗 <a href='{link}'>查看原推文</a>\n")
        except Exception as e:
            print(f"抓取 {url} 失败: {e}")

    if not posts:
        print("未抓取到最新推文")
        return

    # 汇总形成爆文看板格式
    message_header = "🌟 <b>X (Twitter) 中文爆文实时定时推送</b> 🌟\n--------------------------------\n\n"
    full_message = message_header + "\n\n".join(posts)
    
    # 发送到 Telegram
    if send_telegram_message(full_message):
        print("✅ 成功推送至 Telegram!")
    else:
        print("❌ 推送失败，请检查 Token 与 Chat ID")

if __name__ == "__main__":
    fetch_and_push()
