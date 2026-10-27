import os
import requests
import feedparser
from datetime import datetime, timedelta

# 从环境变量中读取基础配置
RSSHUB_URL = os.getenv('RSSHUB_URL', 'http://104.207.154.160:1200')
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

# 💡 零代码动态配置：从 GitHub 变量读取喜好（支持随时在网页修改）
CAR_BRANDS = os.getenv('CAR_BRANDS', '福特 丰田 沃尔沃 马自达 本田 奥迪')
CAR_TYPES = os.getenv('CAR_TYPES', '旅行车 混动 SUV')
CAR_BUDGET = os.getenv('CAR_BUDGET', '15万内')

def fetch_used_cars():
    print(f"🚗 正在检索二手车（已启用防空窗兜底）：品牌[{CAR_BRANDS}] 车型[{CAR_TYPES}] 预算[{CAR_BUDGET}]...")
    
    brands_query = " OR ".join(CAR_BRANDS.split())
    types_query = " OR ".join(CAR_TYPES.split())
    car_keywords = f"({brands_query}) ({types_query})"
    
    url = f"{RSSHUB_URL}/xianyu/search/{car_keywords}"
    feed = feedparser.parse(url)
    
    if not feed.entries:
        print("⚠️ 提示: RSSHub 当前未返回任何车源数据。")
        return []

    # 放宽时间窗口：初筛 72 小时内
    time_threshold = datetime.now() - timedelta(hours=72)
    fresh_cars = []
    fallback_cars = []

    for entry in feed.entries:
        title = entry.title
        summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
        content = summary[:150] + "..." if len(summary) > 150 else summary
        link = entry.link
        
        car_item = {
            "title": title,
            "content": content,
            "link": link
        }
        
        # 收集所有抓取到的车源作为兜底池
        fallback_cars.append(car_item)
        
        # 尝试检查时间戳
        published_parsed = getattr(entry, 'published_parsed', None)
        if published_parsed:
            try:
                pub_date = datetime(*published_parsed[:6])
                if pub_date >= time_threshold:
                    fresh_cars.append(car_item)
            except Exception:
                pass

    # 🛠️ 核心兜底条款：如果 72 小时内筛选结果为空，但抓取到了原始内容，则自动启用兜底，返回最新车源
    if not fresh_cars and fallback_cars:
        print("ℹ️ 提示: 未命中 72 小时严格时间窗，已自动触发兜底条款，为您推送最新可用车源。")
        fresh_cars = fallback_cars[:5]
    else:
        fresh_cars = fresh_cars[:5]

    print(f"✅ 最终确认推送二手车数量: {len(fresh_cars)} 条")
    return fresh_cars

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

def format_combined_board(cars, posts):
    board = "📊 <b>【个人专属多功能智能雷达看板】</b>\n"
    board += "───────────────────────────\n\n"
    
    # 板块一：二手车雷达（含兜底保障）
    board += f"🚗 <b>【二手车智能检索雷达】</b> (<code>{CAR_BRANDS} | {CAR_TYPES} | {CAR_BUDGET}</code>)\n"
    if not cars:
        board += "<i>当前各大平台暂无匹配车源，系统持续监控中...</i>\n\n"
    else:
        for i, car in enumerate(cars, 1):
            board += f"🔥 <b>#{i} {car['title']}</b>\n"
            if car['content']:
                board += f"💬 {car['content']}\n"
            board += f"🔗 <a href='{car['link']}'>点击直达平台查看详情</a>\n\n"
            
    board += "───────────────────────────\n\n"
    
    # 板块二：X 科技财经热点
    board += "🔥 <b>【X 科技与财经热点精选】</b>\n"
    if not posts:
        board += "<i>暂无热点资讯</i>\n"
    else:
        for i, post in enumerate(posts, 1):
            board += f"🔹 <a href='{post['link']}'>{post['title']}</a>\n"
            
    board += "\n───────────────────────────\n"
    board += "🤖 <i>Vultr 节点自动监控 | 已启用防空窗兜底条款</i>"
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
    cars = fetch_used_cars()
    posts = fetch_twitter_posts()
    board_content = format_combined_board(cars, posts)
    send_telegram(board_content)
