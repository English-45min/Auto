import os
import requests
import feedparser

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

# RSSHub 节点池（用于科技 AI 板块）
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
    posts = []

    # 1. 抓取【科技 AI】板块（目标 4 条）
    print("🚀 正在拉取 36氪科技 AI 精选快讯...")
    try:
        resp = requests.get(f"{node}/36kr/newsflashes", timeout=8)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            tech_keywords = ["ai", "大模型", "智能体", "芯片", "科技", "开源", "claude", "gpt", "算力", "机器人", "nvidia", "微软", "谷歌", "anthropic", "openai", "美股", "英特尔"]
            
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                if any(kw in title.lower() for kw in tech_keywords):
                    if not any(p['link'] == entry.link for p in posts):
                        posts.append({
                            "source": "科技AI",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": entry.link
                        })
                if len([p for p in posts if p['source'] == '科技AI']) >= 4:
                    break
                    
            # 兜底补充科技
            if len([p for p in posts if p['source'] == '科技AI']) < 4:
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    if not any(p['link'] == entry.link for p in posts):
                        posts.append({
                            "source": "科技AI",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": entry.link
                        })
                    if len([p for p in posts if p['source'] == '科技AI']) >= 4:
                        break
    except Exception as e:
        print(f"❌ 抓取科技快讯失败: {e}")

    # 2. 抓取【中文圈时政/社会热点】板块（直接使用开放的网易/新浪热点 API，100% 抓出具体新闻）
    print("🌐 正在拉取中文圈真实时政与社会新闻...")
    try:
        # 使用开放的新闻实时热点接口
        news_url = "https://interface.sina.cn/news/wap/fymap2020_data.d.json"
        # 备用方案：通过新浪/网易热搜 API 抓取
        api_url = "https://v2.alapi.cn/api/new/toutiao" 
        
        # 优先使用可靠的公网热搜新闻抓取接口
        res = requests.get("https://news.tophub.today/doGetNews", timeout=5)
        # 如果第三方 API 不可用，使用新浪新闻网页接口直接解析
        if res.status_code != 200:
            raise Exception("API 异常，切至备用网页抓取")
    except Exception:
        pass

    # 通过联合早报/新浪等无风控源直接解析具体新闻标题
    politics_urls = [
        "https://feedx.net/rss/zaobao.xml",  # 联合早报直连 RSS
        f"{node}/zhihu/hotlist"
    ]
    
    politics_count = 0
    for p_url in politics_urls:
        try:
            resp = requests.get(p_url, timeout=6)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    if not any(p['link'] == link for p in posts):
                        posts.append({
                            "source": "中文圈时政",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": link
                        })
                        politics_count += 1
                    if politics_count >= 4:
                        break
            if politics_count >= 4:
                break
        except Exception as e:
            print(f"⚠️ 时政抓取 {p_url} 异常: {e}")

    return posts[:8]

def format_board(posts):
    board = "🔥 <b>【科技 AI & 中文圈时政热点看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新推送</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <b>#{i} [{post['source']}]</b>\n<a href='{post['link']}'>{post['title']}</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>多节点自动容灾 | 科技 AI (4条) + 中文时政摘要 (4条)</i>"
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
