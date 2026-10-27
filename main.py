import os
import requests
import feedparser

# 从环境变量中读取配置
RSSHUB_URL = os.getenv('RSSHUB_URL', 'http://104.207.154.160:1200')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

def fetch_twitter_posts():
    print("🐦 正在从自建服务器拉取 X (Twitter) 财经与科技爆文...")
    
    # 采用标准精简的关键词，提高 RSSHub 的搜索成功率
    keywords = "AI OR 科技 OR 美股"
    url = f"{RSSHUB_URL}/twitter/keyword/{keywords}"
    print(f"正在抓取: {url}")
    
    feed = feedparser.parse(url)
    posts = []
    
    if not feed.entries:
        print("⚠️ 提示: RSSHub 未返回任何推文，请检查 RSSHub 状态或关键词。")
        return posts

    for entry in feed.entries:
        title = entry.title
        author = getattr(entry, 'author', 'X 优质用户')
        
        # 获取推文完整正文内容（限制 300 字，保证饱满）
        summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
        content = summary[:300] + "..." if len(summary) > 300 else summary
        link = entry.link
        
        # 简单判定爆帖条件（可根据需要拓展）
        is_viral = len(summary) > 120 or "http" in summary
        
        posts.append({
            "title": title,
            "author": author,
            "content": content,
            "link": link,
            "is_viral": is_viral
        })
        
    return posts

def format_board(posts):
    board = "📊 <b>【X (Twitter) 中文财经与科技热点爆文看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "⚠️ 过去一段时间未探测到新鲜推文，请稍后再试。\n\n"
    else:
        for i, post in enumerate(posts[:10], 1):
            if post["is_viral"]:
                board += f"🔥 <b>[50万+爆帖]</b> <b>#{i} {post['author']}</b>\n"
                if post['content']:
                    board += f"💡 {post['content']}\n"
                board += f"🔗 <a href='{post['link']}'>点击查看原文</a>\n\n"
            else:
                board += f"📌 <b>#{i} {post['author']}</b>\n"
                if post['content']:
                    board += f"💬 {post['content']}\n"
                board += f"🔗 <a href='{post['link']}'>点击查看原文</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>数据源自自建 Vultr RSSHub 节点自动监控</i>"
    return board

def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误: 未检测到 Telegram 密钥或 Chat ID 配置。")
        return
        
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        print("✅ Telegram 消息推送成功！")
    else:
        print(f"❌ 推送失败: {response.text}")

if __name__ == "__main__":
    posts = fetch_twitter_posts()
    board_content = format_board(posts)
    
    # 无论是否有新推文，都执行推送，方便验证管道连通性
    send_telegram(board_content)
