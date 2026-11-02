import os
import json
import time
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

    # ---------------- 2. 抓取【中文圈时政/社会财经】板块（多源轮询，确保每次都有新鲜增量） ----------------
    print("🌐 正在拉取中文圈时政与宏观财经动态...")
    politics_sources = [
        f"{node}/zhihu/hotlist",           # 知乎热榜（全天候实时滚动社会与时政讨论）
        f"{node}/thepaper/featured",      # 澎湃新闻头条
        "https://feedx.net/rss/zaobao.xml"  # 联合早报
    ]
    
    now_utc = datetime.now(timezone.utc)
    one_day_ago = now_utc - timedelta(hours=36)

    for p_url in politics_sources:
        try:
            resp = requests.get(p_url, timeout=6)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    
                    is_recent = True
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        try:
                            pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                            if pub_dt < one_day_ago:
                                is_recent = False
                        except Exception:
                            pass

                    # 只要没发过，且在时间窗口内
                    if is_recent and title not in sent_history and not any(p['link'] == link for p in politics_posts):
                        politics_posts.append({
                            "source": "中文时政",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": link
                        })
                    if len(politics_posts) >= 2:
                        break
            if len(politics_posts) >= 2:
                break
        except Exception as e:
            print(f"⚠️ 时政抓取 {p_url} 异常: {e}")

    # 兜底：如果高频源也没凑够 2 条，放宽时间限制，直接取当前源最新的前 2 条
    if len(politics_posts) < 2:
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
                                "source": "中文时政",
                                "title": title[:75] + "..." if len(title) > 75 else title,
                                "link": link
                            })
                        if len(politics_posts) >= 2:
                            break
                if len(politics_posts) >= 2:
                    break
            except Exception:
                pass

    # ---------------- 3. 合并与更新历史记录 ----------------
    final_posts = tech_posts[:4] + politics_posts[:2]

    new_titles = [p['title'] for p in final_posts]
    save_history(list(set(sent_history + new_titles)))

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
    board += "🤖 <i>多源高频轮询 | 智能去重防断流</i>"
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
    
    for attempt in range(1, 4):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("✅ Telegram 爆文看板推送成功！")
                return
            else:
                print(f"⚠️ 第 {attempt} 次推送遇到状态码 {response.status_code}: {response.text}")
        except Exception as e:
            print(f"⚠️ 第 {attempt} 次推送网络异常: {e}")
        
        if attempt < 3:
            time.sleep(3)
            
    print("❌ 多次重试后推送依然失败。")

if __name__ == "__main__":
    posts = fetch_comprehensive_posts()
    if posts:
        board_content = format_board(posts)
        send_telegram(board_content)
    else:
        print("ℹ️ 本次没有检测到任何新内容，跳过推送。")
