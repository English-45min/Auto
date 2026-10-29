import os
import re
import json
import time
import base64
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from collections import OrderedDict
from dotenv import load_dotenv

# ================= Configuration =================
load_dotenv()

# DeepSeek API
API_BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY")
AI_MODEL = "deepseek-chat"

# Timezone (UTC+8 for China)
TZ = timezone(timedelta(hours=8))

# Output file
OUTPUT_FILE = "index.html"
CACHE_FILE = "ai_cache.json"

# ************************************************
# [Important] 全球万亿市值俱乐部及超级巨头全覆盖关键词
# 涵盖美股、港股、A股核心巨头，确保财报与突发自动高权重置顶
# ************************************************
MEGA_CAP_KEYWORDS = {
    # 科技与 AI 巨头
    "microsoft": "Microsoft (微软)",
    "msft": "Microsoft (微软)",
    "apple": "Apple (苹果)",
    "aapl": "Apple (苹果)",
    "google": "Alphabet (谷歌)",
    "alphabet": "Alphabet (谷歌)",
    "googl": "Alphabet (谷歌)",
    "amazon": "Amazon (亚马逊)",
    "amzn": "Amazon (亚马逊)",
    "nvidia": "Nvidia (英伟达)",
    "nvda": "Nvidia (英伟达)",
    "meta": "Meta Platforms",
    "facebook": "Meta Platforms",
    
    # 半导体与硬件核心
    "tsmc": "台积电",
    "taiwan semiconductor": "台积电",
    "broadcom": "博通 (Broadcom)",
    "avgo": "博通 (Broadcom)",
    "amd": "AMD",
    
    # 新能源与汽车
    "tesla": "Tesla (特斯拉)",
    "tsla": "Tesla (特斯拉)",
    
    # 传统金融、医药与消费巨头
    "berkshire": "伯克希尔·哈撒韦",
    "eli lilly": "礼来制药",
    "jpmorgan": "摩根大通",
    "walmart": "沃尔玛",
    "visa": "Visa",
    
    # 中概股巨头 (美股/港股)
    "tencent": "腾讯控股",
    "0700.hk": "腾讯控股",
    "alibaba": "阿里巴巴",
    "baba": "阿里巴巴",
    "9988.hk": "阿里巴巴",
    
    # 中文关键词对照
    "微软": "微软",
    "苹果": "苹果",
    "谷歌": "谷歌",
    "亚马逊": "亚马逊",
    "英伟达": "英伟达",
    "台积电": "台积电",
    "博通": "博通",
    "特斯拉": "特斯拉",
    "伯克希尔": "伯克希尔",
    "礼来": "礼来",
    "摩根大通": "摩根大通",
    "腾讯": "腾讯",
    "阿里巴巴": "阿里巴巴"
}

# 财报栏目专属关键词
EARNINGS_CHANNEL_KEYWORDS = {"earning", "profit", "quarter", "results", "guidance", "fiscal", "财报", "业绩", "季度", "指引"}

# RSS Feeds configuration
FEEDS = [
    {"url": "https://www.reutersagency.com/feed/?best-topics=business-finance&format=rss", "source": "Reuters", "channels": ["global", "earnings"]},
    {"url": "https://feeds.a.bloomberg.com/bpol/rss", "source": "Bloomberg", "channels": ["global", "earnings"]},
    {"url": "https://www.wsj.com/xml/rss/3_7014.xml", "source": "WSJ", "channels": ["global"]},
    {"url": "https://www.investors.com/feed/investing/", "source": "IBD", "channels": ["earnings"]},
    {"url": "https://sc.hkexnews.hk/rss", "source": "HKEx", "channels": ["china", "earnings"]},
    {"url": "https://rss.caixin.com/finance/all", "source": "Caixin", "channels": ["china"]},
    {"url": "http://www.nbd.com.cn/rss", "source": "NBD", "channels": ["china"]}
]

# Time limit: 24 hours
TIME_LIMIT = timedelta(hours=24)

def get_importance_weight(title, channels):
    title_lower = title.lower()
    weight = 50 # Base weight

    if "earnings" in channels:
        weight += 15

    if any(k in title_lower for k in EARNINGS_CHANNEL_KEYWORDS):
        weight += 10
        
    # 检测全球万亿巨头
    for kw, name in MEGA_CAP_KEYWORDS.items():
        if kw in title_lower:
            weight += 40  # 自动获得极高权重
            if any(k in title_lower for k in EARNINGS_CHANNEL_KEYWORDS):
                weight += 15  # 财报发布，双重重磅加分
            break

    return min(100, weight)

def fetch_all_news():
    now = datetime.now(timezone.utc)
    seen_urls = set()
    news_pool = []
    
    print(f"[Fetch] Fetching news from {len(FEEDS)} feeds...")
    for f in FEEDS:
        try:
            feed = feedparser.parse(f["url"])
            if feed.bozo: continue
            
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url or url in seen_urls: continue
                
                pub_time = entry.get("published_parsed", None)
                if pub_time:
                    dt = datetime(*pub_time[:6], tzinfo=timezone.utc)
                    if now - dt > TIME_LIMIT: continue
                    published_at = dt.astimezone(TZ).strftime("%m-%d %H:%M")
                else:
                    published_at = now.astimezone(TZ).strftime("%m-%d %H:%M")
                
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                
                weight = get_importance_weight(title, f["channels"])
                
                news_pool.append({
                    "title": title,
                    "summary": summary,
                    "url": url,
                    "source": f["source"],
                    "publishedAt": published_at,
                    "channels": f["channels"],
                    "_weight": weight
                })
                seen_urls.add(url)
                
        except Exception as e:
            print(f"Error fetching feed {f['source']}: {e}")
    
    news_pool.sort(key=lambda x: x["_weight"], reverse=True)
    return news_pool

def load_cache():
    if Path(CACHE_FILE).exists():
        try:
            return json.loads(Path(CACHE_FILE).read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)
        except:
            return OrderedDict()
    return OrderedDict()

def save_cache(cache_data):
    items = list(cache_data.items())[-200:]
    Path(CACHE_FILE).write_text(json.dumps(OrderedDict(items), ensure_ascii=False, indent=2), encoding="utf-8")

def call_deepseek_ai(entry):
    if not API_KEY:
        return None, "🟠 DeepSeek API 密钥未检测到，AI 宏观拆解不可用。"

    text_en = f"Title: {entry['title']}\nSummary: {entry['summary']}\nSource: {entry['source']}"
    
    prompt = f"""
Input text:
'''
{text_en}
'''

Task:
Perform financial/geopolitical macro analysis on this input text and generate a structured JSON object in simplified Chinese (zh-CN).

Constraints:
1. Do not use Markdown formatting in the JSON keys or values.
2. Provide precise financial and logic translations.

JSON Structure:
{{
  "zh_title": "精准的中文标题",
  "zh_summary": "精准简练的中文摘要",
  "importance": 70,
  "analysis": "100字以内的宏观时政深度拆解与逻辑链条。指出最关键的影响因素。",
  "coreLogic": "核心逻辑/传导链条 (2句话内)",
  "marketImpact": "潜在市场影响 (资产/板块/宏观指标)",
  "risk": "风险提示/信息不确定性"
}}
"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    print(f"[{entry['source']}] AI Processing (Global Mega-Cap detector enabled)...")
    try:
        response = requests.post(f"{API_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=40)
        response.raise_for_status()
        data = response.json()
        
        content = data["choices"][0]["message"]["content"]
        clean_content = re.sub(r"^```json\n|\n```$", "", content.strip(), flags=re.MULTILINE)
        ai_data = json.loads(clean_content)
        
        title_zh_lower = ai_data["zh_title"].lower()
        mega_found_in_ai = False
        for kw, name in MEGA_CAP_KEYWORDS.items():
            if kw in title_zh_lower or name.lower() in title_zh_lower:
                if any(k in title_zh_lower for k in EARNINGS_CHANNEL_KEYWORDS):
                    ai_data["importance"] += 15
                    mega_found_in_ai = True
                    break
        
        ai_data["importance"] = min(100, ai_data["importance"])
        
        if mega_found_in_ai:
             ai_data["zh_title"] = f"🔥 {ai_data['zh_title']}"

        return ai_data, None

    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
        return None, f"🟠 AI 分析不可用: API 调用出错 ({str(e)})."

def main():
    if not API_KEY:
        print("Warning: DEEPSEEK_API_KEY is not set.")

    all_news = fetch_all_news()
    cache = load_cache()
    
    result = OrderedDict()
    result["updatedAt"] = datetime.now(TZ).strftime("%m-%d %H:%M:%S")
    result["aiEnabled"] = bool(API_KEY)
    result["aiModel"] = AI_MODEL
    result["aiStats"] = {"attempts": 0, "successes": 0}
    
    for channel in ["global", "asia", "china", "earnings"]:
        result[channel] = []

    limit = {"global": 20, "asia": 10, "china": 10, "earnings": 10}
    count_limit = OrderedDict(limit)

    print("[Process] Processing AI analyses and sorting with global mega-cap priority...")
    for entry in all_news:
        if all(c <= 0 for c in count_limit.values()):
            break

        item_channels = entry["channels"]
        can_add_to = [ch for ch in item_channels if ch in count_limit and count_limit[ch] > 0]
        
        if not can_add_to: continue

        cache_key = base64.b64encode(f"{entry['url']}_{entry['source']}".encode("utf-8")).decode("utf-8")
        ai_data = cache.get(cache_key)
        
        news_item = {
            "source": entry["source"],
            "url": entry["url"],
            "publishedAt": entry["publishedAt"],
            "title": entry["title"],
            "summary": entry["summary"]
        }
        
        ai_msg = None
        if not ai_data:
            result["aiStats"]["attempts"] += 1
            ai_data, ai_msg = call_deepseek_ai(entry)
            if ai_data:
                result["aiStats"]["successes"] += 1
                cache[cache_key] = ai_data
            elif ai_msg:
                cache[cache_key] = {"ai_msg": ai_msg, "importance": 40}

        if ai_data:
            if "ai_msg" in ai_data:
                news_item["ai_msg"] = ai_data["ai_msg"]
                news_item["_importance"] = ai_data["importance"]
            else:
                news_item["title"] = ai_data["zh_title"]
                news_item["summary"] = ai_data["zh_summary"]
                news_item["analysis"] = ai_data["analysis"]
                news_item["impact"] = ai_data["marketImpact"]
                news_item["coreLogic"] = ai_data["coreLogic"]
                news_item["transmission"] = ai_data["transmission"]
                news_item["evidence"] = ai_data["evidence"]
                news_item["scersonal"] = ai_data.get("scenarios", [])
                news_item["risk"] = ai_data["risk"]
                news_item["confidence"] = ai_data.get("confidence", "高")
                news_item["_importance"] = ai_data["importance"]
        else:
            news_item["ai_msg"] = ai_msg or "AI分析不可用"
            news_item["_importance"] = 40

        for ch in can_add_to:
            result[ch].append(news_item)
            count_limit[ch] -= 1

    for ch in ["global", "asia", "china", "earnings"]:
        result[ch].sort(key=lambda x: x["_importance"], reverse=True)

    print(f"[Save] Saving data to {OUTPUT_FILE} and updating cache...")
    save_cache(cache)
    
    page = Path(OUTPUT_FILE).read_text(encoding="utf-8")
    json_data_json = json.dumps(result, ensure_ascii=False, indent=2)
    new_page = re.sub(
        r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->",
        f"<!-- NEWS_DATA_START -->\n<script>window.newsData={json_data_json};</script>\n<!-- NEWS_DATA_END -->",
        page,
        flags=re.S
    )
    Path(OUTPUT_FILE).write_text(new_page, encoding="utf-8")
    print("✅ 全球万亿巨头全覆盖策略已生效！")

from pathlib import Path

if __name__ == "__main__":
    main()
