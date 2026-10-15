import os
import feedparser
import requests

# 从 GitHub Secrets 环境变量中读取配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# 配置多个稳定的 RSSHub 备用节点（自动按顺序轮询）
RSS_URLS = [
    "https://rsshub.rssforever.com/twitter/user/getodkn",
    "https://rsshub.pseudoyu.com/twitter/user/getodkn",
    "https://rsshub.ktachibana.party/twitter/user/getodkn",
]

def send_telegram_message(text):
    """向 Telegram 发送消息"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误：未检测到 TG_BOT_TOKEN 或 TG_CHAT_ID 环境变量")
        return
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            print("✅ Telegram 消息推送成功！")
        else:
            print(f"❌ Telegram 推送失败，状态码: {response.status_code}, 响应: {response.text}")
    except Exception as e:
        print(f"❌ 发送 Telegram 请求时发生异常: {e}")

def main():
    feed = None
    
    # 依次尝试各个备用 RSSHub 节点
    for url in RSS_URLS:
        print(f"正在尝试抓取节点: {url}")
        try:
            parsed = feedparser.parse(url)
            # 检查是否成功获取到文章列表
            if parsed and hasattr(parsed, 'entries') and len(parsed.entries) > 0:
                feed = parsed
                print(f"✅ 成功从节点获取到内容！")
                break
            else:
                print(f"⚠️ 该节点返回内容为空或格式无效，尝试下一个...")
        except Exception as e:
            print(f"❌ 节点请求异常: {e}")

    # 判断抓取结果并发送通知
    if not feed or not feed.entries:
        print("⚠️ 所有 RSSHub 节点均未成功抓取到新推文。")
        send_telegram_message("⚠️ 测试：工作流运行成功，但所有 RSSHub 节点均未抓取到新推文。")
    else:
        # 获取最新的一条推文
        latest_entry = feed.entries[0]
        title = getattr(latest_entry, 'title', '无标题')
        link = getattr(latest_entry, 'link', '')
        
        # 组装推送文案
        message_text = f"🔥 **检测到 X (Twitter) 最新更新**\n\n{title}\n\n🔗 [点击查看原文]({link})"
        print(f"准备推送内容: {title}")
        send_telegram_message(message_text)

if __name__ == "__main__":
    main()
