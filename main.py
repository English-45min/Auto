import os
import re
import feedparser
import requests

# 1. 从系统读取环境变量
RSSHUB_URL = os.getenv("RSSHUB_URL", "").rstrip("/")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 2. X (Twitter) 热门中文/科技推文搜索路由
FEEDS = [
    f"{RSSHUB_URL}/twitter/keyword/AI+min_faves:100"
]

def clean_html(raw_html):
    """去除 HTML 标签，防止 Telegram HTML 模式解析报错"""
    clean_re = re.compile('<[^<]+?>')
    text = re.sub(clean_re, '', raw_html)
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def format_telegram_dashboard(entries):
    """整理爆文看板内容"""
    dashboard_text = "📊 <b>【X (Twitter) 中文/科技热点爆文看板】</b>\n"
    dashboard_text += "───────────────────────────\n\n"
    
    # 最多显示前 10 条热门推文
    for idx, entry in enumerate(entries[:10], start=1):
        title = clean_html(entry.title).strip()
        link = entry.link
        
        # 针对排名靠前或互动高的推文，标注高亮爆帖
        badge = "🔥 <b>[50万+爆帖]</b> " if idx <= 2 else "📌 "
        
        dashboard_text += f"{badge}<b>#{idx}</b> {title}\n"
        dashboard_text += f"🔗 <a href='{link}'>点击查看原文</a>\n\n"
        
    dashboard_text += "───────────────────────────\n"
    dashboard_text += "🤖 <i>数据源自自建 Vultr RSSHub 节点自动监控</i>"
    return dashboard_text

def send_tg_msg(text):
    """发送格式化消息到 Telegram"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误：GitHub Secrets 里的 TG_BOT_TOKEN 或 TG_CHAT_ID 未配置！")
        return False
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        res = requests.post(url, json=payload, timeout=15)
        print(f"👉 Telegram 推送状态码: Status {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        print(f"❌ Telegram 推送请求失败: {e}")
        return False

def run():
    if not RSSHUB_URL:
        print("❌ 错误：GitHub Secrets 里的 RSSHUB_URL 还没设置！")
        return

    print(f"📡 正在从自建服务器拉取推文: {RSSHUB_URL}")
    all_entries = []
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    for feed_url in FEEDS:
        print(f"正在抓取: {feed_url}")
        try:
            # 1. 先用 requests 获取真实 XML 数据
            res = requests.get(feed_url, headers=headers, timeout=20)
            if res.status_code == 200:
                # 2. 传递给 feedparser 进行结构化解析
                feed = feedparser.parse(res.content)
                if feed.entries:
                    all_entries.extend(feed.entries)
                    print(f"✅ 成功解析出 {len(feed.entries)} 条推文数据！")
            else:
                print(f"⚠️ 服务器响应异常，状态码: {res.status_code}")
        except Exception as e:
            print(f"❌ 请求失败: {e}")
            
    if all_entries:
        print("🚀 正在生成看板并推送到 Telegram...")
        dashboard_content = format_telegram_dashboard(all_entries)
        send_tg_msg(dashboard_content)
    else:
        print("⚠️ 提示：当前未抓取到符合条件的推文，请稍后再试。")

if __name__ == "__main__":
    run()
