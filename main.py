import os
import requests
import feedparser
from urllib.parse import quote

# 优先读取 GitHub Secrets 中的 RSSHUB_URL，如果失效或未设置，自动兜底使用公共 RSS 节点
RSSHUB_URL = os.getenv('RSSHUB_URL', 'https://rsshub.rssforever.com').rstrip('/')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

def fetch_tech_ai_posts():
    print("🚀 正在抓取中文科技 AI 圈精选爆文...")
    
    # 组合多节点策略，防止单一节点失效
    nodes = [
        RSSHUB_URL,
        "https://rsshub.rssforever.com",
        "https://rsshub.app"
    ]
    
    posts = []
    
    for base_url in nodes:
        print(f"尝试从节点抓取: {base_url}")
        target_url = f"{base_url}/36kr/newsflashes"
        try:
            resp = requests.get(target_url, timeout=10)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                keywords = ["AI", "Claude", "GPT", "大模型", "智能体", "Prompt", "独立开发", "开源", "科技", "英伟达"]
                
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    
                    # 优先筛选科技/AI相关主题
                    if any(kw.lower() in title.lower() for kw in keywords) or len(posts) < 3:
                        if not any(p['link'] == link for p in posts):
                            posts.append({
                                "title": title[:90] + "..." if len(title) > 90 else title,
                                "link": link
                            })
                    if len(posts) >= 8:
                        break
                        
            if posts:
                print(f"✅ 成功从 {base_url} 解析出 {len(posts)} 条爆文！")
                break # 抓取成功直接退出循环
        except Exception as e:
            print(f"⚠️ 节点 {base_url} 连接失败/超时: {e}")

    return posts

def format_tech_board(posts):
    board = "🔥 <b>【中文科技 & AI 圈每日爆文看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新推送（网络节点连接异常）</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <b>#{i}</b> <a href='{post['link']}'>{post['title']}</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>全网科技 AI 高品质干货推送 | 多节点自动容灾</i>"
    return board

def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误: 未检测到 Telegram 密钥 (TG_BOT_TOKEN 或 TG_CHAT_ID)。")
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
    posts = fetch_tech_ai_posts()
    board_content = format_tech_board(posts)
    send_telegram(board_content)
