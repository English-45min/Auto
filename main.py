import os
import json
import time
import requests
import feedparser
from datetime import datetime, timezone

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
                data = json.load(f)
                if data and isinstance(data[0], dict):
                    return [item['title'] for item in data]
                return data if isinstance(data, list) else []
        except Exception:
            pass
    return []

def save_history(titles):
    try:
        current_time = datetime.now(timezone.utc).isoformat()
        # 保持最近 120 条缓存
        history_data = [{"title": t, "time": current_time} for t in titles[-120:]]
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存历史记录失败: {e}")

def fetch_comprehensive_posts():
    node = get_working_node()
    sent_titles = load_history()
    
    tech_posts = []
    politics_posts = []

    # ---------------- 1. 抓取【科技 AI】板块（严格 4 条） ----------------
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

    # 科技兜底：若不够 4 条直接补齐
    if len(tech_posts) < 4:
        try:
            resp = requests.get(f"{node}/36kr/newsflashes", timeout=6)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    if not any(p['link'] == link for p in tech_posts):
                        tech_posts.append({
                            "source": "科技AI",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": link
                        })
                    if len(tech_posts) >= 4:
                        break
        except Exception:
            pass

    # ---------------- 2. 抓取【中文圈时政】板块（严格 4 条，多源轮询防止死水） ----------------
    print("🌐 正在拉取中文圈时政与宏观热点...")
    
    # 结合高频热榜与宏观资讯源，彻底告别陈旧老新闻
    politics_sources = [
        f"{node}/thepaper/featured",      # 澎湃新闻头条
        f"{node}/zhihu/hotlist",           # 知乎热榜
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
                    
                    # 过滤日常娱乐无用话题
                    ignore_keywords = ["电视剧", "明星", "演员", "演唱会", "八卦"]
                    if any(kw in title for kw in ignore_keywords):
                        continue

                    if title not in sent_titles and not any(p['link'] == link for p in politics_posts):
                        politics_posts.append({
                            "source": "中文时政",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": link
                        })
                    if len(politics_posts) >= 4:
                        break
            if len(politics_posts) >= 4:
                break
        except Exception as e:
            print(f"⚠️ 时政源 {p_url} 抓取异常: {e}")

    # 时政强制兜底：如果还差几条，直接从源里拉取最新的前 4 条补齐
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
                                "source": "中文时政",
                                "title": title[:75] + "..." if len(title) > 75 else title,
                                "link": link
                            })
                        if len(politics_posts) >= 4:
                            break
                if len(politics_posts) >= 4:
                    break
            except Exception:
                pass

    # ---------------- 3. 严格合并为 4 + 4 ----------------
    final_posts = tech_posts[:4] + politics_posts[:4]

    # 归档入库
    all_current_titles = [p['title'] for p in final_posts]
    save_history(sent_titles + all_current_titles)

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
    board += "🤖 <i>多源高频防重 | 科技 AI (4条) + 中文时政 (4条)</i>"
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
