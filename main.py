import os
import json
import time
import random
import requests
import feedparser
from datetime import datetime, timezone, timedelta

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')
HISTORY_FILE = '.sent_history.json'

# 🌐 保留 RSSHub 节点仅用于 36氪科技 AI 快讯
RSSHUB_NODES = [
    os.getenv('RSSHUB_URL', '').rstrip('/'),
    "https://rsshub.rssforever.com",
    "https://hub.slarker.me",
    "https://rsshub.app"
]

FORBIDDEN_KEYWORDS = [
    "贝森特", "许家印", "王兴兴", "山东社区爆炸", "全剧终", "伊朗经济",
    "支联会", "张雪机车", "湖南省领导动作频频", "算力狂飙", "韩咏红", "长和", "巴拿马运河"
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
        history_data = [{"title": t, "time": current_time} for t in titles[-300:]]
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存历史记录失败: {e}")

def is_recent_entry(entry):
    """检查文章是否为最近 36 小时内发出的新鲜新闻"""
    try:
        published_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
        if published_parsed:
            entry_time = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now - entry_time > timedelta(hours=36):
                return False
    except Exception:
        pass
    return True

def fetch_comprehensive_posts():
    node = get_working_node()
    sent_titles = load_history()
    
    tech_posts = []
    hkmotw_posts = []

    # ---------------- 1. 抓取【科技 AI】板块（4 条） ----------------
    print("🚀 正在拉取 36氪科技 AI 精选快讯...")
    try:
        resp = requests.get(f"{node}/36kr/newsflashes", timeout=8)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                link = entry.link
                if any(kw in title for kw in FORBIDDEN_KEYWORDS) or not is_recent_entry(entry):
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

    # ---------------- 2. 抓取【港澳台地区】板块（直连官方原生 RSS） ----------------
    print("🌐 正在拉取港澳台地区直连原生热点新闻...")
    hkmotw_sources = [
        "https://news.yahoo.com.tw/rss/",                 # 台湾 Yahoo 奇摩新闻焦点
        "https://www me.cna.com.tw/cna2013/market/rss.aspx",# 台湾中央社 (CNA) 官方源
        "https://rthk.hk/rthk/news/rss/c_expressnews.xml", # 香港电台 (RTHK) 现场即时新闻
        "https://hk.news.yahoo.com/rss/"                  # 香港 Yahoo 新闻
    ]
    
    # 兼容修正部分常见 CNA RSS 地址
    cna_fallback = "https://www.cna.com.tw/cna2013/market/rss.aspx"
    
    for p_url in hkmotw_sources:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            resp = requests.get(p_url, headers=headers, timeout=6)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link
                    
                    ignore_keywords = ["综艺", "明星", "八卦", "CP", "恋情", "运势"]
                    if any(kw in title for kw in ignore_keywords) or any(kw in title for kw in FORBIDDEN_KEYWORDS):
                        continue
                    
                    if not is_recent_entry(entry):
                        continue

                    if title not in sent_titles and not any(p['link'] == link for p in hkmotw_posts):
                        hkmotw_posts.append({
                            "source": "港澳台新闻",
                            "title": title[:75] + "..." if len(title) > 75 else title,
                            "link": link
                        })
                    if len(hkmotw_posts) >= 4:
                        break
            if len(hkmotw_posts) >= 4:
                break
        except Exception as e:
            print(f"⚠️ 港澳台新闻源 {p_url} 抓取异常: {e}")

    # ---------------- 3. 兜底逻辑：若不足 4 条，自动打乱补充 ----------------
    if len(hkmotw_posts) < 4:
        print("🔄 执行港澳台板块实时兜底...")
        for p_url in hkmotw_sources:
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                resp = requests.get(p_url, headers=headers, timeout=6)
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.content)
                    entries = [e for e in feed.entries if is_recent_entry(e)]
                    random.shuffle(entries)
                    for entry in entries:
                        title = entry.title.strip().replace('\n', ' ')
                        link = entry.link
                        if any(kw in title for kw in FORBIDDEN_KEYWORDS):
                            continue
                        if not any(p['link'] == link for p in hkmotw_posts):
                            hkmotw_posts.append({
                                "source": "港澳台新闻",
                                "title": title[:75] + "..." if len(title) > 75 else title,
                                "link": link
                            })
                        if len(hkmotw_posts) >= 4:
                            break
                if len(hkmotw_posts) >= 4:
                    break
            except Exception:
                pass

    final_posts = tech_posts[:4] + hkmotw_posts[:4]

    all_current_titles = [p['title'] for p in final_posts]
    updated_history = list(dict.fromkeys(sent_titles + all_current_titles))
    save_history(updated_history)

    return final_posts

def format_board(posts):
    board = "🔥 <b>【科技 AI & 港澳台热点看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    if not posts:
        board += "<i>当前时间窗口内暂无新推送</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <b>#{i} [{post['source']}]</b>\n<a href='{post['link']}'>{post['title']}</a>\n\n"
            
    board += "───────────────────────────\n"
    board += "🤖 <i>官方原生直连 | 科技 AI (4条) + 港澳台 (4条)</i>"
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
