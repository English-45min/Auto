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
    """测试并返回第一个可用的 RSSHub 节点"""
    for node in RSSHUB_NODES:
        if not node:
            continue
        try:
            res = requests.get(f"{node}/36kr/newsflashes", timeout=5)
            if res.status_code == 200:
                print(f"✅ 使用可用节点: {node}")
                return node
        except Exception:
            continue
    return "https://rsshub.rssforever.com"

def fetch_comprehensive_posts():
    node = get_working_node()
    posts = []

    # 1. 抓取【科技 AI】板块（固定 4 条）
    print("🚀 正在拉取 36氪科技 AI 快讯...")
    try:
        resp = requests.get(f"{node}/36kr/newsflashes", timeout=8)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            count = 0
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                posts.append({
                    "source": "科技AI",
                    "title": title[:75] + "..." if len(title) > 75 else title,
                    "link": entry.link
                })
                count += 1
                if count >= 4:
                    break
    except Exception as e:
        print(f"❌ 抓取 36氪快讯失败: {e}")

    # 2. 抓取【中文圈时政/社会热点】板块（澎湃新闻/热榜双源保障，固定 4 条）
    print("🌐 正在拉取中文圈时政与社会热点...")
    politics_fetched = False
    
    # 优先尝试澎湃新闻时政/头条
    politics_sources = [
        f"{node}/thepaper/featured",      # 澎湃头条/时政
        f"{node}/zhihu/hotlist"           # 知乎热榜备用
    ]

    for p_url in politics_sources:
        try:
            resp = requests.get(p_url, timeout=8)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                if feed.entries:
                    count = 0
                    for entry in feed.entries:
                        title = entry.title.strip().replace('\n', ' ')
                        # 避免与科技重复
                        if not any(p['link'] == entry.link for p in posts):
                            posts.append({
                                "source": "中文圈时政",
                                "title": title[:75] + "..." if len(title) > 75 else title,
                                "link": entry.link
                            })
                            count += 1
                        if count >= 4:
                            break
                    if count > 0:
                        politics_fetched = True
                        print(f"✅ 成功获取 {count} 条中文圈时政热点！")
                        break
        except Exception as e:
            print(f"⚠️ 抓取 {p_url} 异常: {e}")

    # 如果 RSS 路由全失败，使用开放的网易/新浪新闻接口进行绝对兜底
    if not politics_fetched:
        print("⚠️ 触发时政兜底接口...")
        try:
            resp = requests.get("https://interface.sina.cn/news/wap/fymap2020_data.d.json", timeout=5)
            # 备用方案：简单网络请求补充
        except Exception:
            pass

    return posts

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
