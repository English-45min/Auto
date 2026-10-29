import os
import re
import json
import time
import base64
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from dotenv import load_dotenv
from pathlib import Path

# ================= Configuration =================
load_dotenv()

API_BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY")
AI_MODEL = "deepseek-chat"

TZ = timezone(timedelta(hours=8))
OUTPUT_FILE = "index.html"
CACHE_FILE = "ai_cache.json"

# 万亿巨头与超级大厂词库
MEGA_CAP_KEYWORDS = {
    "microsoft": "Microsoft (微软)", "msft": "Microsoft (微软)",
    "apple": "Apple (苹果)", "aapl": "Apple (苹果)",
    "google": "Alphabet (谷歌)", "alphabet": "Alphabet (谷歌)", "googl": "Alphabet (谷歌)",
    "amazon": "Amazon (亚马逊)", "amzn": "Amazon (亚马逊)",
    "nvidia": "Nvidia (英伟达)", "nvda": "Nvidia (英伟达)",
    "meta": "Meta Platforms", "facebook": "Meta Platforms",
    "tsmc": "台积电", "broadcom": "博通", "tesla": "Tesla (特斯拉)", "tsla": "Tesla (特斯拉)",
    "tencent": "腾讯", "alibaba": "阿里巴巴",
    "微软": "微软", "苹果": "苹果", "谷歌": "谷歌", "亚马逊": "亚马逊", "英伟达": "英伟达",
    "台积电": "台积电", "博通": "博通", "特斯拉": "特斯拉", "腾讯": "腾讯", "阿里巴巴": "阿里巴巴"
}

# 沃什时代美联储及宏观重磅词库
MACRO_EVENT_KEYWORDS = {
    "federal reserve": "美联储", "fomc": "美联储FOMC", "warsh": "美联储主席沃什",
    "interest rate": "利率决议", "rate cut": "降息", "rate hike": "加息",
    "美联储": "美联储", "沃什": "美联储主席沃什", "降息": "降息", "加息": "加息",
    "央行决议": "央行决议", "货币政策": "货币政策"
}

EARNINGS_CHANNEL_KEYWORDS = {"earning", "profit", "quarter", "results", "guidance", "fiscal", "财报", "业绩", "季度", "指引"}

FEEDS = [
    {"url": "https://www.reutersagency.com/feed/?best-topics=business-finance&format=rss", "source": "Reuters", "channels": ["global", "earnings"]},
    {"url": "https://feeds.a.bloomberg.com/bpol/rss", "source": "Bloomberg", "channels": ["global", "earnings"]},
    {"url": "https://www.wsj.com/xml/rss/3_7014.xml", "source": "WSJ", "channels": ["global"]},
    {"url": "https://www.investors.com/feed/investing/", "source": "IBD", "channels": ["earnings"]},
    {"url": "https://sc.hkexnews.hk/rss", "source": "HKEx", "channels": ["china", "earnings"]},
    {"url": "https://rss.caixin.com/finance/all", "source": "Caixin", "channels": ["china"]},
    {"url": "http://www.nbd.com.cn/rss", "source": "NBD", "channels": ["china"]}
]

TIME_LIMIT = timedelta(hours=24)

def get_importance_weight(title, channels):
    title_lower = title.lower()
    weight = 50 
    if "earnings" in channels: weight += 15
    if any(k in title_lower for k in EARNINGS_CHANNEL_KEYWORDS): weight += 10
    
    is_mega = any(kw in title_lower for kw in MEGA_CAP_KEYWORDS)
    is_macro = any(kw in title_lower for kw in MACRO_EVENT_KEYWORDS)

    if is_mega or is_macro:
        weight += 45  
        if any(k in title_lower for k in EARNINGS_CHANNEL_KEYWORDS) or is_macro:
            weight += 20  

    return min(100, weight)

def fetch_all_news():
    now = datetime.now(timezone.utc)
    seen_urls = set()
    news_pool = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }
    
    for f in FEEDS:
        try:
            res = requests.get(f["url"], headers=headers, timeout=15)
            feed = feedparser.parse(res.content)
            
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url or url in seen_urls: continue
                
                # 安全的时效性解析
                published_at = now.astimezone(TZ).strftime("%m-%d %H:%M")
                pub_time = entry.get("published_parsed", None)
                if pub_time:
                    try:
                        dt = datetime(*pub_time[:6], tzinfo=timezone.utc)
                        # 超过 24 小时坚决滤除
                        if now - dt > TIME_LIMIT:
                            continue
                        published_at = dt.astimezone(TZ).strftime("%m-%d %H:%M")
                    except Exception:
                        pass
                
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                weight = get_importance_weight(title, f["channels"])
                
                news_pool.append({
                    "title": title, "summary": summary, "url": url,
                    "source": f["source"], "publishedAt": published_at,
                    "channels": f["channels"], "_weight": weight
                })
                seen_urls.add(url)
        except Exception as e:
            print(f"Error fetching feed {f['source']}: {e}")
            
    news_pool.sort(key=lambda x: x["_weight"], reverse=True)
    print(f"抓取到有效时效新闻：{len(news_pool)} 条")
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
        return None, "🟠 DeepSeek API 密钥未检测到"

    text_en = f"Title: {entry['title']}\nSummary: {entry['summary']}\nSource: {entry['source']}"
    prompt = f"""
Input text:
'''
{text_en}
'''
Task: Perform financial/geopolitical macro analysis and generate a structured JSON object in simplified Chinese (zh-CN).
Constraints: 
1. Do not use Markdown formatting in keys/values.
2. Provide precise translations.
JSON Structure:
{{
  "zh_title": "精准的中文标题",
  "zh_summary": "精准简练的中文摘要",
  "importance": 70,
  "analysis": "100字以内的宏观时政深度拆解与逻辑链条。",
  "coreLogic": "核心逻辑 (2句话内)",
  "marketImpact": "潜在市场影响 (资产/板块)",
  "risk": "风险提示"
}}
"""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    payload = {"model": AI_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "response_format": {"type": "json_object"}}

    try:
        response = requests.post(f"{API_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=40)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        clean_content = re.sub(r"^```json\n|\n```$", "", content.strip(), flags=re.MULTILINE)
        ai_data = json.loads(clean_content)
        
        title_zh_lower = ai_data.get("zh_title", "").lower()
        is_mega = any(kw in title_zh_lower for kw in MEGA_CAP_KEYWORDS)
        is_macro = any(kw in title_zh_lower for kw in MACRO_EVENT_KEYWORDS)
        is_earnings = any(k in title_zh_lower for k in EARNINGS_CHANNEL_KEYWORDS)

        if is_mega or is_macro:
            ai_data["importance"] = max(ai_data.get("importance", 70), 95) 
            if is_earnings or is_macro:
                ai_data["importance"] = 100 
            ai_data["zh_title"] = f"🔥 {ai_data['zh_title']}"

        return ai_data, None
    except Exception as e:
        return None, f"🟠 AI 分析不可用: {str(e)}"

def main():
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

    for entry in all_news:
        if all(c <= 0 for c in count_limit.values()): break
        item_channels = entry["channels"]
        can_add_to = [ch for ch in item_channels if ch in count_limit and count_limit[ch] > 0]
        if not can_add_to: continue

        cache_key = base64.b64encode(f"{entry['url']}_{entry['source']}".encode("utf-8")).decode("utf-8")
        ai_data = cache.get(cache_key)
        
        news_item = {
            "source": entry["source"], "url": entry["url"],
            "publishedAt": entry["publishedAt"], "title": entry["title"], "summary": entry["summary"]
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

        if ai_data and "ai_msg" not in ai_data:
            news_item["title"] = ai_data.get("zh_title", entry["title"])
            news_item["summary"] = ai_data.get("zh_summary", entry["summary"])
            news_item["analysis"] = ai_data.get("analysis", "")
            news_item["impact"] = ai_data.get("marketImpact", "")
            news_item["coreLogic"] = ai_data.get("coreLogic", "")
            news_item["risk"] = ai_data.get("risk", "")
            news_item["_importance"] = ai_data.get("importance", 50)
        else:
            news_item["ai_msg"] = ai_msg or "AI分析不可用"
            news_item["_importance"] = entry["_weight"]

        for ch in can_add_to:
            result[ch].append(news_item)
            count_limit[ch] -= 1

    for ch in ["global", "asia", "china", "earnings"]:
        result[ch].sort(key=lambda x: x["_importance"], reverse=True)

    save_cache(cache)
    
    # 安全写入逻辑
    page = Path(OUTPUT_FILE).read_text(encoding="utf-8")
    json_data_json = json.dumps(result, ensure_ascii=False, indent=2)
    injection_code = f"<!-- NEWS_DATA_START -->\n<script>window.newsData={json_data_json};</script>\n<!-- NEWS_DATA_END -->"
    
    if "<!-- NEWS_DATA_START -->" in page:
        new_page = re.sub(
            r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->",
            injection_code,
            page,
            flags=re.S
        )
    else:
        # 兜底注入：防止标记被删导致数据写不进去
        new_page = page.replace("</head>", f"{injection_code}\n</head>")

    Path(OUTPUT_FILE).write_text(new_page, encoding="utf-8")
    print("✅ 脚本执行完毕，数据已安全写入 index.html。")

if __name__ == "__main__":
    main()
