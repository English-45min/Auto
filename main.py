import os
import requests
import feedparser

# 从环境变量中读取基础配置
RSSHUB_URL = os.getenv('RSSHUB_URL', 'http://104.207.154.160:1200')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

def fetch_twitter_tech_ai_posts():
    print("🐦 正在从自建服务器拉取 X (Twitter) 科技 AI 中文圈爆文...")
    
    # 专属锁定科技、AI、Agent、LLM 等中文圈高质量高赞关键词
    keywords = "AI OR 智能体 OR Claude OR GPT OR 独立开发者 min_faves:50 lang:zh"
    url = f"{RSSHUB_URL}/twitter/keyword/{keywords}"
    print(f"请求路由: {url}")
    
    feed = feedparser.parse(url)
    posts = []
    
    if not feed.entries:
        print("⚠️ 提示: 暂时未抓取到符合条件的推文。")
        return posts

    for entry in feed.entries[:8]: # 取前 8 条精选
        title = entry.title
        link = entry.link
        
        posts.append({
            "title": title,
            "link": link
        })
        
    print(f"✅ 成功解析出 {len(posts)} 条科技 AI 中文圈爆文！")
    return posts

def format_pure_tech_board(posts):
    board = "🔥 <b>【X 科技 & AI 中文圈精选爆文看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新爆文</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            # 智能高亮阅读量高或极具价值的爆帖
            board += f"🔹 <b>#{i}</b> <a href='{post['link']}'>{post['title']}</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>Vultr 节点自建 RSSHub | 纯粹科技 AI 垂直高赞推送</i>"
    return board

def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误: 未检测到 Telegram 密钥。")
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
        print("✅ Telegram 爆文看板推送成功！")
    else:
        print(f"❌ 推送失败: {response.text}")

if __name__ == "__main__":
    posts = fetch_twitter_tech_ai_posts()
    board_content = format_pure_tech_board(posts)
    send_telegram(board_content)
