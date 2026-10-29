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
# [Important] 超万亿公司财报特殊播报关键词
# 这些关键词出现，重要性自动提升到最高级，并强制置顶
# ************************************************
MEGA_CAP_KEYWORDS = {
    # 英文源
    "google": "Alphabet (Google)",
    "alphabet": "Alphabet (Google)",
    "microsoft": "Microsoft (微软)",
    "msft": "Microsoft (微软)",
    "apple": "Apple (苹果)",
    "amazon": "Amazon (亚马逊)",
    "amzn": "Amazon (亚马逊)",
    "nvidia": "Nvidia (英伟达)",
    "nvda": "Nvidia (英伟达)",
    # 中文源
    "谷歌": "谷歌",
    "微软": "微软",
    "苹果公司": "苹果",
    "亚马逊": "亚马逊",
    "英伟达": "英伟达"
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

# ************************************************
# [Important] 修改重要性评分逻辑
# 加入针对超万亿公司的专属权重提升
# ************************************************
def get_importance_weight(title, channels):
    title_lower = title.lower()
    weight = 50 # Base weight

    # 1. 栏目影响 (财报栏目基础分较高)
    if "earnings" in channels:
        weight += 15

    # 2. 财报相关关键词 (在标题中检测到财报字眼)
    if any(k in title_lower for k in EARNINGS_CHANNEL_KEYWORDS):
        weight += 10
        
    # ==========================================================
    # 🌟 [Key Change] 特殊公司播报核心逻辑
    # 如果是超万亿公司（MEGA-CAP），自动获得最高额外权重
    # ==========================================================
    mega_cap_match = None
    for kw, name in MEGA_CAP_KEYWORDS.items():
        if kw in title_lower:
            mega_cap_match = name
            # 检测到万亿俱乐部公司名称
            weight += 40  # 自动获得极大权重提升
            
            # 如果同时也在检测到财报关键词，则是“双重重磅”
            if any(k in title_lower for k in EARNINGS_CHANNEL_KEYWORDS):
                weight += 15  # 财报发布，额外再加！
            
            break # 匹配一个即可

    # 限制评分区间在 0 - 100 之间
    return min(100, weight)

# Fetch news and filter by date
def fetch_all_news():
    now = datetime.now(timezone.utc)
    seen_urls = set()
    news_pool = []
    
    print(f"[Fetch] Fetching news from {len(FEEDS)} feeds...")
    for f in FEEDS:
        try:
            feed = feedparser.parse(f["url"])
            if feed.bobo: continue
            
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url or url in seen_urls: continue
                
                # Filter by date (last 24h)
                pub_time = entry.get("published_parsed", None)
                if pub_time:
                    dt = datetime(*pub_time[:6], tzinfo=timezone.utc)
                    if now - dt > TIME_LIMIT: continue
                    published_at = dt.astimezone(TZ).strftime("%m-%d %H:%M")
                else:
                    # Fallback to current time if pubDate missing
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
                    "_weight": weight # Sorting only, not shown on webpage
                })
                seen_urls.add(url)
                
        except Exception as e:
            print(f"Error fetching feed {f['source']}: {e}")
    
    # Sort news by importance (highest weight first)
    news_pool.sort(key=lambda x: x["_weight"], reverse=True)
    return news_pool

# AI Cache
def load_cache():
    if Path(CACHE_FILE).exists():
        try:
            return json.loads(Path(CACHE_FILE).read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)
        except:
            return OrderedDict()
    return OrderedDict()

def save_cache(cache_data):
    # Keep only 200 items to save space
    items = list(cache_data.items())[-200:]
    Path(CACHE_FILE).write_text(json.dumps(OrderedDict(items), ensure_ascii=False, indent=2), encoding="utf-8")

# DeepSeek API Translation and Analysis
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
  "importance": 70, // 评分 (0-100)，基于宏观影响、突发性、证据可信度
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
        "response_format": {"type": "json_object"} # Force JSON output
    }

    print(f"[{entry['source']}] AI Processing (MEGA-CAP detector enabled)...")
    try:
        response = requests.post(f"{API_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=40)
        response.raise_for_status()
        data = response.json()
        
        content = data["choices"][0]["message"]["content"]
        # Strip markdown if present
        clean_content = re.sub(r"^```json\n|\n```$", "", content.strip(), flags=re.MULTILINE)
        
        ai_data = json.loads(clean_content)
        
        print(f"AI: Importance {ai_data['importance']} - {ai_data['zh_title']}")
        
        # ==========================================================
        # 🌟 [Key Change] AI 层面再次确保万亿巨头财报置顶
        # 如果公司名称在 AI 拆解的标题中出现，则在原评分上强制再加分
        # ==========================================================
        title_zh_lower = ai_data["zh_title"].lower()
        mega_found_in_ai = False
        for kw, name in MEGA_CAP_KEYWORDS.items():
            if kw in title_zh_lower or name in title_zh_lower:
                if any(k in title_zh_lower for k in EARNINGS_CHANNEL_KEYWORDS):
                    ai_data["importance"] += 15 # 双重确认：万亿巨头+财报关键词
                    mega_found_in_ai = True
                    break # 匹配一个即可
        
        # 再次确保评分最高为 100
        ai_data["importance"] = min(100, ai_data["importance"])
        
        # 如果检测到万亿俱乐部的财报，在中文标题前加上专属火种图标 🔥
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
    
    # Output structure
    result = OrderedDict()
    result["updatedAt"] = datetime.now(TZ).strftime("%m-%d %H:%M:%S")
    result["aiEnabled"] = bool(API_KEY)
    result["aiModel"] = AI_MODEL
    result["aiStats"] = {"attempts": 0, "successes": 0}
    
    for channel in ["global", "asia", "china", "earnings"]:
        result[channel] = []

    limit = {"global": 20, "asia": 10, "china": 10, "earnings": 10}
    
    count_limit = OrderedDict()
    for ch in limit:
        count_limit[ch] = limit[ch]

    print("[Process] Processing AI analyses and sorting with mega-cap priority...")
    for entry in all_news:
        if all(c <= 0 for c in count_limit.values()):
            break

        item_channels = entry["channels"]
        can_add_to = [ch for ch in item_channels if ch in count_limit and count_limit[ch] > 0]
        
        if not can_add_to: continue

        # DeepSeek AI Analysis
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
                # Store message in cache as object with empty analysis
                cache[cache_key] = {"ai_msg": ai_msg, "importance": 40}

        if ai_data:
            if "ai_msg" in ai_data:
                # Handle cached API error
                news_item["ai_msg"] = ai_data["ai_msg"]
                news_item["_importance"] = ai_data["importance"]
            else:
                # Merge AI results into news_item
                news_item["title"] = ai_data["zh_title"]
                news_item["summary"] = ai_data["zh_summary"]
                news_item["analysis"] = ai_data["analysis"]
                news_item["impact"] = ai_data["marketImpact"]
                news_item["coreLogic"] = ai_data["coreLogic"]
                news_item["transmission"] = ai_data["transmission"]
                news_item["evidence"] = ai_data["evidence"]
                news_item["scenarios"] = ai_data["scenarios"]
                news_item["risk"] = ai_data["risk"]
                news_item["confidence"] = ai_data["confidence"]
                
                # Internal sorting weight, prioritize AI-calculated importance
                news_item["_importance"] = ai_data["importance"]
        else:
            news_item["ai_msg"] = ai_msg or "AI分析不可用"
            news_item["_importance"] = 40

        # Add to matching channels and count down
        for ch in can_add_to:
            result[ch].append(news_item)
            count_limit[ch] -= 1

    # Sorting again after AI processing to ensure priorities within the top-10 are correct
    print("[Final Sort] Final channel-based sorting with AI importance.")
    for ch in ["global", "asia", "china", "earnings"]:
        # Sorted by AI importance, mega-cap earnings get 90-100 score
        result[ch].sort(key=lambda x: x["_importance"], reverse=True)

    # Save data to index.html
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
    print("✅ 大功告成！Python 脚本文件修复完成。")

from pathlib import Path

if __name__ == "__main__":
    main()
