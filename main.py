import os
import requests
import feedparser

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

RSSHUB_NODES = [
    os.getenv('RSSHUB_URL', '').rstrip('/'),
    "https://rsshub.rssforever.com",
    "https://rsshub.app"
]

def get_working_node():
    for node in RSSHUB_NODES:
        if not node:
            continue
        try:
            res = requests.get(f"{node}/36kr/newsflashes", timeout=4)
            if res.status_code == 200:
                print(f"✅ 使用可用节点: {node}")
                return node
        except Exception:
            continue
    return "https://rsshub.rssforever.com"

def fetch_comprehensive_posts():
    node = get_working_node()
    tech_posts = []
    politics_posts = []

    # ---------------- 1. 独立抓取【科技 AI】板块（目标 4 条） ----------------
    print("🚀 正在拉取 36氪科技 AI 精选快讯...")
    try:
        resp = requests.get(f"{node}/36kr/newsflashes", timeout=8)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            tech_keywords = ["ai", "大模型", "智能体", "芯片", "科技", "开源", "claude", "gpt", "算力", "机器人", "nvidia", "微软", "谷歌", "anthropic", "openai", "美股", "英伟达", "英特尔", "发布", "融资", "融资"]
            
            # 优先匹配科技核心词
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                if any(kw in title.lower() for kw in tech_keywords):
                    if not any(p['link'] == entry.link for p in tech_posts):
                        tech_posts.append({
                            "source": "科技AI",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": entry.link
                        })
                if len(tech_posts) >= 4:
                    break
            
            # 兜底：如果精准匹配不够，直接取 36氪最新条目补齐 4 条
            if len(tech_posts) < 4:
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    if not any(p['link'] == entry.link for p in tech_posts):
                        tech_posts.append({
                            "source": "科技AI",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": entry.link
                        })
                    if len(tech_posts) >= 4:
                        break
    except Exception as e:
        print(f"❌ 抓取科技快讯失败: {e}")

    # ---------------- 2. 独立抓取【中文圈时政】板块（目标 4 条） ----------------
    print("🌐 正在拉取中文圈真实时政与社会新闻...")
    politics_urls = [
        "https://feedx.net/rss/zaobao.xml", # 联合早报直连 RSS
        f"{node}/zhihu/hotlist"
    ]
    
    for p_url in politics_urls:
        try:
            resp = requests.get(p_url, timeout=6)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    if not any(p['link'] == link for p in politics_posts):
                        politics_posts.append({
                            "source": "中文圈时政",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": link
                        })
                    if len(politics_posts) >= 4:
                        break
            if len(politics_posts) >= 4:
                break
        except Exception as e:
            print(f"⚠️ 时政抓取 {p_url} 异常: {e}")

    # ---------------- 3. 硬性合并两个独立板块 ----------------
    final_posts = tech_posts[:4] + politics_posts[:4]
    return final_posts

def format_board(posts):
    board = "🔥 <b>【科技 AI & 中文圈时政热点看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新推送</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <b>#{i} [{post['source']}]</b>\n<a href='{post['link']}'>{post['title']}</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>多节点自动容灾 | 科技 AI (4条) + 中文时政 (4条)</i>"
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
    posts = fetch_comprehensive_posts()
    board_content = format_board(posts)
    send_telegram(board_content)
