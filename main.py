import os
import requests
import feedparser

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

# 可用 RSSHub 节点自动容灾池
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

    # 1. 抓取 36氪快讯（科技与 AI 核心硬核资讯）
    print("🚀 正在拉取 36氪科技 AI 快讯...")
    try:
        resp = requests.get(f"{node}/36kr/newsflashes", timeout=6)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            keywords = ["AI", "大模型", "智能体", "芯片", "科技", "开源", "Claude", "GPT", "算力"]
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                # 优先匹配科技AI，若数量不够则按顺序补充
                if any(kw.lower() in title.lower() for kw in keywords) or len(posts) < 2:
                    if not any(p['link'] == entry.link for p in posts):
                        posts.append({
                            "source": "科技AI",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": entry.link
                        })
                if len(posts) >= 4:
                    break
    except Exception as e:
        print(f"抓取 36氪快讯失败: {e}")

    # 2. 抓取知乎热榜（涵盖中文圈时政、社会议题、经济与综合热点）
    print("🌐 正在拉取中文圈时政与社会热点...")
    try:
        resp = requests.get(f"{node}/zhihu/hotlist", timeout=6)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                if not any(p['link'] == entry.link for p in posts):
                    posts.append({
                        "source": "时政热点",
                        "title": title[:75] + "..." if len(title) > 75 else title,
                        "link": entry.link
                    })
                if len(posts) >= 8: # 总计控制在 8 条以内
                    break
    except Exception as e:
        print(f"抓取知乎热榜失败: {e}")

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
    board += "🤖 <i>多节点自动容灾 | 科技 AI 与中文圈时政综合推送</i>"
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
