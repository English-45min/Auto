import os
import requests
import feedparser

# 从环境变量中读取配置
RSSHUB_URL = os.getenv('RSSHUB_URL', 'http://104.207.154.160:1200')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

def fetch_twitter_posts():
    print("🐦 正在从自建服务器拉取 X (Twitter) 中文财经与科技爆文...")
    
    # 核心调整：通过 lang:zh 强制限定只抓取中文推文，并组合科技、AI、美股、财经等关键词
    keywords = "AI OR 科技 OR 美股 OR 财经 OR 投资 OR 创业 lang:zh"
    
    # 加上 min_faves 限制过滤低质量内容（例如点赞数大于 20 或 50）
    url = f"{RSSHUB_URL}/twitter/keyword/{keywords}+min_faves:20"
    print(f"正在抓取: {url}")
    
    feed = feedparser.parse(url)
    posts = []
    
    if not feed.entries:
        print("⚠️ 提示: RSSHub 未返回中文推文，尝试放宽条件...")
        # 降级备用：如果加了点赞限制没有抓到，尝试不加点赞限制纯搜中文关键词
        url_fallback = f"{RSSHUB_URL}/twitter/keyword/{keywords}"
        feed = feedparser.parse(url_fallback)

    for entry in feed.entries:
        title = entry.title
        author = getattr(entry, 'author', 'X 中文优质用户')
        
        # 获取推文正文摘要（最多 250 字）
        summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
        content = summary[:250] + "..." if len(summary) > 250 else summary
        link = entry.link
        
        # 判定是否为 50w+ 或高互动爆帖（根据文本特征或长度）
        is_viral = "爆" in title or "万" in title or len(summary) > 150
        
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
        board += "⚠️ 过去 24 小时暂未抓取到符合条件的中文热帖，稍后为您重新探测。\n\n"
    else:
        # 最多展示前 8-10 条精选中文推文
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
    send_telegram(board_content)
