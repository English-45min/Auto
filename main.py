import os
import json
import requests
import feedparser
from datetime import datetime, timezone, timedelta

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
            json.dump(history[-150:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存历史记录失败: {e}")

def fetch_comprehensive_posts():
    node = get_working_node()
    sent_history = load_history()
    
    tech_posts = []
    politics_posts = []

    # ---------------- 1. 抓取【科技 AI】板块（目标 4 条） ----------------
    print("🚀 正在拉取 36氪科技 AI 精选快讯...")
    try:
        resp = requests.get(f"{node}/36kr/newsflashes", timeout=8)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                link = entry.link
                # 使用标题作为唯一指纹去重
                if title not in sent_history and not any(p['link'] == link for p in tech_posts):
                    tech_posts.append({
                        "source": "科技AI",
                        "title": title[:75] + "..." if len(title) > 75 else title,
                        "link": link
                    })
                if len(tech_posts) >= 4:
                    break
    except Exception as e:
        print(f"❌ 抓取科技快讯失败: {e}")

    # ---------------- 2. 抓取【外媒时政】板块（严格 24 小时内新鲜内容过滤） ----------------
    print("🌐 正在拉取外媒中文时政与宏观报道...")
    politics_sources = [
        "https://feedx.net/rss/zaobao.xml",
        f"{node}/zaobao/realtime/china"
    ]
    
    now_utc = datetime.now(timezone.utc)
    one_day_ago = now_utc - timedelta(hours=36) # 放宽至 36 小时内

    for p_url in politics_sources:
        try:
            resp = requests.get(p_url, timeout=6)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    
                    # 检查发布时间（若 feed 包含 published_parsed）
                    is_recent = True
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        try:
                            pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                            if pub_dt < one_day_ago:
                                is_recent = False # 太旧了，跳过
                        except Exception:
                            pass

                    # 必须是历史没发过、且属于近期更新的文章
                    if is_recent and title not in sent_history and not any(p['link'] == link for p in politics_posts):
                        politics_posts.append({
                            "source": "外媒时政",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": link
                        })
                    if len(politics_posts) >= 2: # 最多取 2 条真正新鲜的时政
                        break
            if len(politics_posts) >= 2:
                break
        except Exception as e:
            print(f"⚠️ 时政抓取 {p_url} 异常: {e}")

    # ---------------- 3. 合并与更新历史记录 ----------------
    final_posts = tech_posts[:4] + politics_posts[:2]

    # 将新出现的标题追加进历史档案
    new_titles = [p['title'] for p in final_posts]
    save_history(list(set(sent_history + new_titles)))

    return final_posts

def format_board(posts):
    board = "🔥 <b>【科技 AI & 外媒中文时政看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新推送</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <b>#{i} [{post['source']}]</b>\n<a href='{post['link']}'>{post['title']}</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>严格时间戳去重 | 拒绝隔夜旧闻</i>"
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
