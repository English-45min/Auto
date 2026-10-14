import os
import requests
import feedparser

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

RSS_URLS = [
    "https://rsshub.app/twitter/user/getodkn",
]

def send_telegram_message(text):
    """发送消息到 Telegram 并打印详细调试日志"""
    # 打印前 5 位字符检查 Token 是否为空（保护隐私不打印全串）
    token_preview = TG_BOT_TOKEN[:5] if TG_BOT_TOKEN else "None"
    print(f"DEBUG: Token 前缀: {token_preview}, Chat ID: {TG_CHAT_ID}")
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        res = requests.post(url, json=payload, timeout=10)
        print(f"Telegram API 响应状态码: {res.status_code}")
        print(f"Telegram API 响应内容: {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"发送请求异常: {e}")
        return False

def fetch_and_push():
    posts = []
    
    for url in RSS_URLS:
        try:
            print(f"正在抓取 RSS: {url}")
            feed = feedparser.parse(url)
            print(f"RSS 解析成功，条目数: {len(feed.entries)}")
            for entry in feed.entries[:3]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                author = feed.feed.get("title", "X 博主")
                posts.append(f"🔥 <b>【{author}】</b>\n{title[:150]}...\n🔗 <a href='{link}'>查看原推文</a>\n")
        except Exception as e:
            print(f"抓取 {url} 失败: {e}")

    if not posts:
        print("❌ 未抓取到任何推文，导致没有发送消息。")
        # 即使没抓到推文，我们发一条测试文本看能不能通
        send_telegram_message("⚠️ 测试：虽然没有抓到推文，但脚本成功触发了！")
        return

    message_header = "🌟 <b>X (Twitter) 中文爆文实时定时推送</b> 🌟\n--------------------------------\n\n"
    full_message = message_header + "\n\n".join(posts)
    
    if send_telegram_message(full_message):
        print("✅ 成功推送至 Telegram!")
    else:
        print("❌ 推送失败，请检查上面的 API 响应内容")

if __name__ == "__main__":
    fetch_and_push()
