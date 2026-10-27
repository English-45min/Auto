import os
import requests
import feedparser

# 从环境变量中读取基础配置
RSSHUB_URL = os.getenv('RSSHUB_URL', 'http://104.207.154.160:1200')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

# 💡 外部配置：品牌、车型、预算
CAR_BRANDS = os.getenv('CAR_BRANDS', '福特 丰田 沃尔沃 马自达 本田 奥迪')
CAR_TYPES = os.getenv('CAR_TYPES', '旅行车 混动 SUV')
CAR_BUDGET = os.getenv('CAR_BUDGET', '15万内')

def fetch_car_news_and_deals():
    print(f"🚗 正在通过高稳定性知乎/全网热榜监控汽车与二手车话题...")
    
    # 使用知乎热搜/综合热榜，其包含大量车主真实讨论、二手车避坑和买车建议
    url = f"{RSSHUB_URL}/zhihu/hotlist"
    feed = feedparser.parse(url)
    
    items = []
    for entry in feed.entries:
        title = entry.title
        summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
        link = entry.link
        
        # 智能筛选：如果标题或摘要中包含汽车相关词汇，优先挑出来展示
        keywords_filter = ["车", "SUV", "混动", "二手", "丰田", "福特", "奥迪", "本田", "马自达", "沃尔沃", "买车", "落地"]
        is_car_related = any(kw in title for kw in keywords_filter)
        
        if is_car_related or len(items) < 3: # 确保至少有内容输出
            content = summary[:120] + "..." if len(summary) > 120 else summary
            items.append({
                "title": title,
                "content": content,
                "link": link
            })
            if len(items) >= 5:
                break

    print(f"✅ 成功抓取汽车/车源行情: {len(items)} 条")
    return items

def fetch_twitter_posts():
    print("🐦 正在拉取 X (Twitter) 中文财经与科技热点...")
    keywords = "AI OR 科技 OR 美股 OR 财经 lang:zh"
    url = f"{RSSHUB_URL}/twitter/keyword/{keywords}"
    
    feed = feedparser.parse(url)
    posts = []
    
    for entry in feed.entries[:5]:
        posts.append({
            "title": entry.title,
            "link": entry.link
        })
    return posts

def format_combined_board(car_items, posts):
    board = "📊 <b>【个人专属多功能智能雷达看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    # 板块一：汽车/二手车行情监控
    board += f"🚗 <b>【汽车与二手车全网讨论雷达】</b> (<code>{CAR_BRANDS} | {CAR_TYPES} | {CAR_BUDGET}</code>)\n"
    if not car_items:
        board += "<i>暂-无最新行情资讯</i>\n\n"
    else:
        for i, item in enumerate(car_items, 1):
            board += f"🔥 <b>#{i} {item['title']}</b>\n"
            if item['content']:
                board += f"💬 {item['content']}\n"
            board += f"🔗 <a href='{item['link']}'>点击查看详情</a>\n\n"
            
    board += "───────────────────────────\n\n"
    
    # 板块二：X 科技财经热点
    board += "🔥 <b>【X 科技与财经热点精选】</b>\n"
    if not posts:
        board += "<i>暂无热点资讯</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <a href='{post['link']}'>{post['title']}</a>\n"
            
    board += "\n───────────────────────────\n"
    board += "🤖 <i>Vultr 节点自动监控 | 已切换至全网高稳定性数据源</i>"
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
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        print("✅ Telegram 看板推送成功！")
    else:
        print(f"❌ 推送失败: {response.text}")

if __name__ == "__main__":
    car_items = fetch_car_news_and_deals()
    posts = fetch_twitter_posts()
    board_content = format_combined_board(car_items, posts)
    send_telegram(board_content)
