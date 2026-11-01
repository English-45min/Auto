import os
import json
import requests
import feedparser

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')
HISTORY_FILE = '.sent_history.json'

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
                return node
        except Exception:
            continue
    return "https://rsshub.rssforever.com"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(history):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history[-100:], f, ensure_ascii=False, indent=2) # 扩大历史记录保持量到 100 条
    except Exception as e:
        print(f"⚠️ 保存历史记录失败: {e}")

def fetch_comprehensive_posts():
    node = get_working_node()
    sent_titles = load_history()
    
    tech_posts = []
    politics_posts = []

    # ---------------- 1. 抓取【科技 AI】板块（目标 4 条，严格过滤已发） ----------------
    print("🚀 正在拉取 36氪科技 AI 精选快讯...")
    try:
        resp = requests.get(f"{node}/36kr/newsflashes", timeout=8)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                link = entry.link
                if title not in sent_titles and not any(p['link'] == link for p in tech_posts):
                    tech_posts.append({
                        "source": "科技AI",
                        "title": title[:75] + "..." if len(title) > 75 else title,
                        "link": link
                    })
                if len(tech_posts) >= 4:
                    break
    except Exception as e:
        print(f"❌ 抓取科技快讯失败: {e}")

    # ---------------- 2. 抓取【中文圈时政/社会热点】板块（多源高频轮询，彻底告别老样子） ----------------
    print("🌐 正在拉取中文圈高频时政与社会热点...")
    politics_sources = [
        f"{node}/zhihu/hotlist",           # 知乎热榜（更新极快）
        f"{node}/thepaper/featured",      # 澎湃新闻头条
        "https://feedx.net/rss/zaobao.xml"  # 联合早报
    ]
    
    for p_url in politics_sources:
        try:
            resp = requests.get(p_url, timeout=6)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    # 严格去重：确保不在已发历史中
                    if title not in sent_titles and not any(p['link'] == link for p in politics_posts):
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

    # 如果多源轮询后时政依然不够 4 条（说明今天时政确实没啥大新闻），放宽去重限制，取当前源里最新的补齐
    if len(politics_posts) < 4:
        for p_url in politics_sources:
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
            except Exception:
                pass

    # ---------------- 3. 组合并更新历史记录 ----------------
    new_posts = tech_posts[:4] + politics_posts[:4]

    # 仅把本次成功推送的新标题加入历史缓存
    all_sent = sent_titles + [p['title'] for p in new_posts]
    save_history(all_sent)

    return new_posts

def format_board(posts):
    board = "🔥 <b>【科技 AI & 中文圈时政热点看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新推送</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <b>#{i} [{post['source']}]</b>\n<a href='{post['link']}'>{post['title']}</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>多源高频轮询防同质化 | 科技 AI (4条) + 中文时政 (4条)</i>"
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
    if posts:
        board_content = format_board(posts)
        send_telegram(board_content)
    else:
        print("ℹ️ 本次没有检测到任何新内容，跳过推送。")
