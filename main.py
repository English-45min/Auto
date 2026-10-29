import os
import requests
import feedparser
from urllib.parse import quote

# 从环境变量中读取基础配置
RSSHUB_URL = os.getenv('RSSHUB_URL', 'http://104.207.154.160:1200')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

def fetch_twitter_tech_ai_posts():
    print("🐦 正在从自建服务器拉取 X (Twitter) 科技 AI 中文圈爆文...")
    
    # 关键词定义
    raw_keywords = "AI OR 智能体 OR Claude OR GPT OR 独立开发者 min_faves:50 lang:zh"
    # 💡 核心修复：对关键词中的空格及特殊字符进行标准 URL 编码
    encoded_keywords = quote(raw_keywords)
    
    url = f"{RSSHUB_URL}/twitter/keyword/{encoded_keywords}"
    print(f"请求路由: {url}")
    
    posts = []
    try:
        # 使用 requests 先获取 XML 文本，设置 15 秒超时防止卡死
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            print(f"⚠️ RSSHub 状态异常: HTTP {resp.status_code}")
            return posts

        feed = feedparser.parse(resp.content)
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
    except Exception as e:
        print(f"❌ 抓取推文时发生异常: {e}")

    return posts

def format_pure_tech_board(posts):
    board = "🔥 <b>【X 科技 & AI 中文圈精选爆文看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新爆文</i>\n"
    else:
        for i, post in enumerate(posts, 1):
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
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ Telegram 爆文看板推送成功！")
        else:
            print(f"❌ 推送失败: {response.text}")
    except Exception as e:
        print(f"❌ 发送 Telegram 消息失败: {e}")

if __name__ == "__main__":
    posts = fetch_twitter_tech_ai_posts()
    board_content = format_pure_tech_board(posts)
    send_telegram(board_content)
