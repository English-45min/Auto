import os
import json
import time
import random
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

# 🛡️ 终极硬编码黑名单：彻底封杀已连续推送多天的高频旧闻关键词
FORBIDDEN_KEYWORDS = [
    "贝森特", "许家印", "王兴兴", "山东社区爆炸", "全剧终", "伊朗经济",
    "支联会", "张雪机车", "湖南省领导动作频频", "算力狂飙"
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
        # 保存最近 250 条缓存，防止历史膨胀同时保证去重窗口足够大
        history_data = [{"title": t, "time": current_time} for t in titles[-250:]]
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存历史记录失败: {e}")

def fetch_comprehensive_posts():
    node = get_working_node()
    sent_titles = load_history()
    
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
                if any(kw in title for kw in FORBIDDEN_KEYWORDS):
                    continue
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

    # ---------------- 2. 抓取【中文圈时政】板块（目标 4 条，多源优先） ----------------
    print("🌐 正在拉取中文圈时政与宏观热点...")
    politics_sources = [
        f"{node}/zhihu/hotlist",           # 知乎热榜（实时滚动）
        f"{node}/thepaper/featured",      # 澎湃新闻
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
                    
                    ignore_keywords = ["电视剧", "明星", "演员", "演唱会", "八卦", "CP", "恋情"]
                    if any(kw in title for kw in ignore_keywords) or any(kw in title for kw in FORBIDDEN_KEYWORDS):
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

    # ---------------- 3. 兜底逻辑：若不够 4 条，打乱抓取顺序避免重复旧条目 ----------------
    if len(politics_posts) < 4:
        print("🔄 正在执行时政板块随机打乱兜底抓取...")
        for p_url in politics_sources:
            try:
                resp = requests.get(p_url, timeout=6)
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.content)
                    entries = list(feed.entries)
                    random.shuffle(entries)  # 随机打乱兜底列表，防止死扣固定前几条
                    for entry in entries:
                        title = entry.title.strip().replace('\n', ' ')
                        link = entry.link
                        if any(kw in title for kw in FORBIDDEN_KEYWORDS):
                            continue
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

    final_posts = tech_posts[:4] + politics_posts[:4]

    # 无论来自常规去重还是兜底，本次推送的所有标题一律写回历史档案
    all_current_titles = [p['title'] for p in final_posts]
    updated_history = list(dict.fromkeys(sent_titles + all_current_titles))
    save_history(updated_history)

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
    board += "🤖 <i>全量持久去重 | 科技 AI (4条) + 中文时政 (4条)</i>"
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
