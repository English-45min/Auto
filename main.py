import os
import feedparser
import requests

# 从 GitHub 的配置里安全读取你的 Vultr 服务器地址和 Telegram 密钥
RSSHUB_BASE_URL = os.getenv("RSSHUB_URL", "http://你的服务器IP:1200")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 你的 RSSHub 爆文订阅链接（这里以搜索 AI 且点赞大于 500 的推文为例）
# 你可以改成任何你想要的关键词
RSS_URL = f"{RSSHUB_BASE_URL}/twitter/keyword/AI min_faves:500"

def send_telegram_message(text):
    """通过你的 Telegram Bot 发送消息"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("未检测到 Telegram 密钥，跳过发送")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    response = requests.post(url, json=payload)
    print("Telegram 发送结果:", response.status_code)

def main():
    print(f"正在抓取 RSS: {RSS_URL}")
    feed = feedparser.parse(RSS_URL)
    
    if not feed.entries:
        print("没有抓到内容或链接失效。")
        return

    # 默认只取最新的一条爆文推送（防止一次性发太多刷屏）
    entry = feed.entries[0]
    title = entry.get('title', '无标题')
    link = entry.get('link', '')
    
    message = f"🔥 <b>发现 X 爆文</b>\n\n{title}\n\n<a href='{link}'>点击查看原推</a>"
    
    print("准备发送消息...")
    send_telegram_message(message)

if __name__ == "__main__":
    main()
