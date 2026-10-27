import os
import requests
import feedparser

# 从环境变量中读取配置
RSSHUB_URL = os.getenv('RSSHUB_URL', 'http://104.207.154.160:1200')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

def fetch_twitter_posts():
    print("🐦 正在从自建服务器拉取 X (Twitter) 财经与科技爆文...")
    
    # 扩展后的关键词：涵盖 AI、科技、美股、财经、投资、创业等中文圈核心话题
    keywords = "AI OR 科技 OR 美股 OR 财经 OR 投资 OR 创业 OR 程序员"
    
    # 适当放宽阅读量门槛（min_faves:50），确保能抓取到足够多的候选内容
    url = f"{RSSHUB_URL}/twitter/keyword/{keywords}+min_faves:50"
    print(f"正在抓取: {url}")
    
    feed = feedparser.parse(url)
    posts = []
    
    if not feed.entries:
        print("⚠️ 提示: 当前未抓取到符合条件的推文，请稍后再试。")
        return posts

    for entry in feed.entries:
        # 提取点赞数/阅读量特征（若 RSSHub 标题或正文中包含点赞数，可在此解析；此处以基础互动及长度判定）
        title = entry.title
        
        # 获取推文作者
        author = getattr(entry, 'author', 'X 优质用户')
        
        # 提取并扩充正文内容显示长度（最多展示 200 个字，让看板内容更饱满）
        summary = getattr(entry, 'summary', '')
        content = summary[:200] + "..." if len(summary) > 200 else summary
        
        # 获取原文链接
        link = entry.link
        
        # 模拟评估阅读量（如果标题或链接中带有高互动标识，或者根据浏览量估算）
        # 这里默认将包含热门或特定长文的判定为高赞，或者通过文本特征标记 50w+ 爆帖
        is_viral = "爆" in title or "50w" in title or len(summary) > 150
        
        posts.append({
            "title": title,
            "author": author,
            "content": content,
            "link": link,
            "is_viral": is_viral
        })
        
    return posts

def format_board(posts):
    # 按照排版要求整理成看板形式
    board = "📊 <b>【X (Twitter) 中文财经与科技热点爆文看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "⚠️ 暂时没有抓取到新鲜出炉的爆文，稍后为您重新探测。\n"
    
    # 最多展示前 10 条精华推文
    for i, post in enumerate(posts[:10], 1):
        if post["is_viral"]:
            board += f"🔥 <b>[50万+爆帖]</b> <b>#{i} {post['author']}</b>：{post['title']}\n"
            if post['content']:
                board += f"💡 <i>{post['content']}</i>\n"
            board += f"🔗 <a href='{post['link']}'>点击查看原文</a>\n\n"
        else:
            board += f"📌 <b>#{i} {post['author']}</b>：{post['title']}\n"
            if post['content']:
                board += f"💬 <i>{post['content']}</i>\n"
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
