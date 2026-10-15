import os
import requests
import feedparser

# 正确的写法应该使用 os.environ.get 来读取 GitHub Secrets 变量
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")


# 你的 RSS 订阅源地址（你可以随时修改或添加更多）
RSS_URLS = [
    "https://rsshub.app/twitter/user/getodkn",
]

def send_telegram_message(text):
    """调用 Telegram API 发送消息"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
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
            for entry in feed.entries[:3]: # 默认取前 3 条
                title = entry.get("title", "")
                link = entry.get("link", "")
                posts.append(f"🔥 {title[:150]}\n🔗 <a href='{link}'>查看原推文</a>\n")
        except Exception as e:
            print(f"抓取失败: {e}")

    if not posts:
        print("❌ 未抓取到任何推文，发送一条测试通知。")
        send_telegram_message("⚠️ 测试：工作流运行成功，但未抓取到新推文。")
        return

    full_message = "🌟 <b>X 爆文定时推送</b> 🌟\n\n" + "\n".join(posts)
    if send_telegram_message(full_message):
        print("✅ 成功推送至 Telegram!")
    else:
        print("❌ 推送失败")

if __name__ == "__main__":
    fetch_and_push()
