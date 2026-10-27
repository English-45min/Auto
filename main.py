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
    print(f"🚗 正在检索二手车（时效性优先）：品牌[{CAR_BRANDS}] 车型[{CAR_TYPES}] 预算[{CAR_BUDGET}]...")
    
    brands_query = " OR ".join(CAR_BRANDS.split())
    types_query = " OR ".join(CAR_TYPES.split())
    car_keywords = f"({brands_query}) ({types_query})"
    
    url = f"{RSSHUB_URL}/xianyu/search/{car_keywords}"
    feed = feedparser.parse(url)
    cars = []
    
    if not feed.entries:
        print("⚠️ 提示: 暂时未检索到匹配的最新车源。")
        return cars

    # 定义时效性窗口：只保留最近 48 小时内发布的车源（可根据需要缩短为 24 小时）
    time_threshold = datetime.now() - timedelta(hours=48)

    for entry in feed.entries:
        title = entry.title
        summary = getattr(entry, 'summary', getattr(entry, 'description', ''))
        link = entry.link
        
        # 尝试解析发布时间（兼容多种 RSS 时间格式）
        published_parsed = getattr(entry, 'published_parsed', None)
        is_fresh = True
        
        if published_parsed:
            pub_date = datetime(*published_parsed[:6])
            if pub_date < time_threshold:
                # 如果发布时间超过 48 小时，判定为过时信息，跳过
                is_fresh = False
                
        if is_fresh:
            content = summary[:150] + "..." if len(summary) > 150 else summary
            cars.append({
                "title": title,
                "content": content,
                "link": link
            })
            
    print(f"✅ 过滤后剩余高时效性新鲜车源: {len(cars)} 条")
    return cars

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
    
    # 板块一：二手车高时效雷达
    board += f"🚗 <b>【二手车新鲜捡漏雷达】</b> (<code>{CAR_BRANDS} | {CAR_TYPES} | {CAR_BUDGET}</code>)\n"
    if not cars:
        board += "<i>当前时间窗口内暂无新上架车源，系统持续高频监控中...</i>\n\n"
    else:
        for i, car in enumerate(cars[:5], 1):
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
        for i, post in enumerate(posts[:5], 1):
            board += f"🔹 <a href='{post['link']}'>{post['title']}</a>\n"
            
    board += "\n───────────────────────────\n"
    board += "🤖 <i>Vultr 节点自动监控 | 已开启时效性过滤与防过时机制</i>"
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
