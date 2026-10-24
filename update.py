# -*- coding: utf-8 -*-
"""
财经与时政新闻助手 6.0 - DeepSeek AI 深度分析版
"""

import os
import re
import json
import time
import html
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, parse_qs

BASE = Path(__file__).resolve().parent
PAGE = BASE / "index.html"
CACHE = BASE / "ai_cache.json"
TZ = timezone(timedelta(hours=8))

MAX_AI_CALLS = 10  # 控制单次最大调用数，确保稳妥
RETRIES = 1
AI_TIMEOUT = 25
MAX_NEWS_AGE_HOURS = 72

LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}

def rss(query, region="US", language="zh-Hans"):
    ceid = f"{region}:{language}"
    return "https://news.google.com/rss/search?" + urlencode({
        "q": query, "hl": "zh-CN", "gl": region, "ceid": ceid,
    })

FEEDS = {
    "global": [
        ("Reuters", rss("site:reuters.com finance economy markets central bank")),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("Associated Press", rss("site:apnews.com business economy news")),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Yahoo Finance", rss("site:finance.yahoo.com markets economy")),
    ],
    "asia": [
        ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
        ("The Japan Times", "https://www.japantimes.co.jp/feed/"),
        ("Focus Taiwan", rss("site:focustaiwan.tw economy semiconductor")),
        ("中央社 CNA (台湾)", "https://feeds.feedburner.com/rsscna/finance"),
    ],
    "china": [
        ("Reuters (China)", rss("site:reuters.com China economy PBOC yuan property A-shares")),
        ("CNBC (China)", rss("site:cnbc.com China economy markets")),
        ("AP News (China)", rss("site:apnews.com China economy trade")),
        ("BBC (China)", rss("site:bbc.com/news/world/asia/china economy")),
    ],
    "earnings": [
        ("CNBC Earnings", "https://www.cnbc.com/id/15839135/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
        ("Yahoo Finance Earnings", rss("site:finance.yahoo.com earnings revenue forecast")),
    ],
}

def clean(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

def is_http_url(url):
    try:
        parsed = urlparse(str(url or ""))
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except Exception:
        return False

def is_google_news_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith("news.google.com")
    except Exception:
        return False

def get(url, timeout=18):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml;q=0.9,*/*;q=0.8",
    })
    with urlopen(request, timeout=timeout) as response:
        return response.read()

def extract_article_url(url):
    if not url or not is_http_url(url): return ""
    if not is_google_news_url(url): return url
    try:
        query = parse_qs(urlparse(url).query)
        for key in ("url", "u", "target", "article_url"):
            for value in query.get(key, []):
                candidate = html.unescape(value.strip())
                if is_http_url(candidate) and not is_google_news_url(candidate):
                    return candidate
    except Exception:
        pass
    return url

def parse_feed(source, url):
    try:
        root = ET.fromstring(get(url, timeout=18))
    except Exception:
        return []
    
    output = []
    for entry in root.findall(".//item"):
        title = clean(entry.findtext("title"))
        link = clean(entry.findtext("link"))
        if not title or not link: continue
        output.append({
            "title": title, "url": link,
            "summary": clean(entry.findtext("description")),
            "source": source,
            "published": clean(entry.findtext("pubDate")),
        })
    return output

def parse_date(value):
    if not value: return None
    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None: result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except Exception:
        pass
    return None

def translate(text):
    text = clean(text)
    if not text or chinese(text): return text
    url = "https://translate.googleapis.com/translate_a/single?" + urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text[:1600]})
    try:
        result = json.loads(get(url, timeout=10).decode("utf-8"))
        return "".join(part[0] for part in result[0] if part and part[0]) or text
    except Exception:
        return text

def load_cache():
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def deduplicate(items):
    output = []
    for item in items:
        raw_title = clean(item.get("title", ""))
        clean_title = re.sub(r"\s*-\s*[^-]+$", "", raw_title).strip().lower()
        tokens = set(re.findall(r"[\w\u4e00-\u9fff]", clean_title))
        if len(tokens) < 3:
            output.append(item)
            continue
        is_duplicate = False
        for accepted in output:
            acc_title = clean(accepted.get("title", ""))
            acc_clean = re.sub(r"\s*-\s*[^-]+$", "", acc_title).strip().lower()
            acc_tokens = set(re.findall(r"[\w\u4e00-\u9fff]", acc_clean))
            if not acc_tokens: continue
            intersection = tokens.intersection(acc_tokens)
            union = tokens.union(acc_tokens)
            similarity = len(intersection) / len(union) if union else 0
            if similarity > 0.85:
                is_duplicate = True
                break
        if not is_duplicate:
            output.append(item)
    return output

def classify(items, channel):
    patterns = {
        "asia": r"asia|asian|japan|korea|taiwan|hong kong|singapore|india|australia|亚太|中国|日本|韩国|台湾|香港",
        "china": r"china|chinese|beijing|shanghai|pboc|yuan|cny|a-shares|hong kong|hkex|property|developer|中国|人民币|A股|港股|央行|地产",
        "earnings": r"earnings|revenue|profit|quarter|forecast|guidance|results|财报|业绩|营收|利润|季度"
    }
    if channel not in patterns: return items
    filtered = [x for x in items if re.search(patterns[channel], x["title"] + " " + x.get("summary", ""), re.I)]
    return filtered if filtered else items

# ============================================================
# DeepSeek API 调用核心函数（完全兼容 OpenAI 标准格式）
# ============================================================
def call_deepseek(prompt):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key: return None
    
    url = "https://api.deepseek.com/chat/completions"
    body = json.dumps({
        "model": "deepseek-chat",  # 使用 DeepSeek 主力对话模型
        "messages": [
            {"role": "system", "content": "你是由 DeepSeek 驱动的资深国际财经时政研究专家。请严格按照要求输出合法的 JSON 格式。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}  # 强制输出 JSON，彻底杜绝解析失败
    }).encode("utf-8")
    
    for attempt in range(RETRIES + 1):
        request = Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "FinanceAssistant/6.0"
        }, method="POST")
        try:
            with urlopen(request, timeout=AI_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "").strip()
                    if content: return content
        except Exception:
            pass
        if attempt < RETRIES: time.sleep(2)
    return None

def parse_ai(raw):
    if not raw: return None
    try:
        data = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start: return None
        try: data = json.loads(raw[start:end + 1])
        except Exception: return None
    if not isinstance(data, dict): return None
    
    def value(key, limit=600): return clean(data.get(key, ""))[:limit]
    def array(key, limit=3):
        items = data.get(key, [])
        return [clean(item)[:250] for item in items if clean(item)][:limit] if isinstance(items, list) else []
    
    assets = data.get("assets", [])
    if not isinstance(assets, list): assets = []
    
    return {
        "analysis": value("analysis", 1600),
        "coreLogic": value("core_logic"),
        "marketImpact": value("market_impact"),
        "transmission": value("transmission"),
        "impact": value("impact", 180),
        "asset": [clean(item)[:80] for item in assets if clean(item)][:4],
        "timeHorizon": value("time_horizon", 150),
        "scenarios": array("scenarios"),
        "evidence": array("evidence"),
        "risk": value("risk"),
        "confidence": value("confidence", 100),
        "provider": "DeepSeek",
    }

IMPORTANCE_RULES = [
    (r"war|invasion|missile|military escalation|ceasefire|战争|入侵|导弹|军事冲突|停火", 30),
    (r"sanction|tariff|trade war|export controls|制裁|关税|贸易战|出口管制", 24),
    (r"central bank|interest rate|rate cut|rate hike|Federal Reserve|央行|利率|降息|加息|美联储", 20),
    (r"GDP|inflation|CPI|unemployment|recession|通胀|CPI|失业|衰退", 12),
]

def importance_score(item):
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    score = 10
    for pattern, points in IMPORTANCE_RULES:
        if re.search(pattern, text, re.I): score += points
    return min(score, 100)

def insight(item, cache, budget):
    ck = hashlib.sha256((item["title"]+"|"+item["url"]).encode()).hexdigest()
    old = cache.get(ck)
    if isinstance(old, dict) and old.get("analysis") and old.get("coreLogic"): return old
    if budget["attempts"] >= MAX_AI_CALLS: return None
    
    prompt = f"""请分析以下国际财经新闻，并严格以 JSON 格式输出（不要包含任何 markdown 代码块符号如 ```json）：
字段要求：
- "analysis": 100-180字，事件含义和宏观重要性。
- "core_logic": 事件/政策到变量变化再到影响对象的逻辑链。
- "market_impact": 潜在受益或承压方向。
- "transmission": 传导机制（供需、利率、汇率或风险偏好）。
- "impact": 一句话概括潜在市场影响。
- "assets": 最多4个相关资产、指数、行业或ETF类别数组。
- "time_horizon": 短中长期及其依据。
- "scenarios": 最多3条情景及触发条件数组。
- "evidence": 最多3条证据线索数组。
- "risk": 反证与风险。
- "confidence": 评估置信度（低/中/高）。

新闻标题：{item['title']}
新闻来源：{item['source']}
新闻摘要：{item.get('summary','')[:1200]}"""
    
    budget["attempts"] += 1
    time.sleep(1.5)
    ai = parse_ai(call_deepseek(prompt))
    if ai and ai["analysis"] and ai["coreLogic"]:
        cache[ck] = ai
        budget["successes"] += 1
        return ai
    budget["failures"] += 1
    return None

def build(channel, cache, budget):
    gathered = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_NEWS_AGE_HOURS)
    
    for src, url in FEEDS[channel]:
        for x in parse_feed(src, url):
            dt = parse_date(x.get("published")) or now
            if not (cutoff <= dt <= now + timedelta(hours=12)): continue
            x["_date"] = dt
            gathered.append(x)
            
    gathered = classify(gathered, channel)
    gathered = deduplicate(gathered)
    
    for item in gathered:
        item["_importance"] = importance_score(item)
        
    gathered.sort(key=lambda x: (x.get("_importance", 0), x["_date"].timestamp()), reverse=True)
    
    output = []
    for i, item in enumerate(gathered):
        if len(output) >= LIMITS[channel]: break
        orig_t, orig_s = item.get("title", ""), item.get("summary", "")
        resolved = extract_article_url(item.get("url", ""))
        
        news_item = {
            "title": translate(orig_t) or orig_t,
            "originalTitle": orig_t,
            "summary": (translate(orig_s) or orig_s)[:900],
            "source": item.get("source", "国际权威媒体"),
            "url": resolved if is_http_url(resolved) else item.get("url", ""),
            "publishedAt": item["_date"].astimezone(TZ).strftime("%Y-%m-%d %H:%M"),
            "timestamp": int(item["_date"].timestamp()),
            "impact": "待分析", "asset": [], "analysis": "", "risk": "",
            "coreLogic": "", "marketImpact": "", "transmission": "", "timeHorizon": "",
            "scenarios": [], "evidence": [], "confidence": "", "aiStatus": "未分析",
            "_importance": item["_importance"]
        }
        
        # 仅对每栏目前 2 条重磅新闻调用 DeepSeek 进行深度分析
        if i < 2:
            ai = insight(news_item, cache, budget)
            if ai:
                news_item.update(ai)
                news_item["impact"] = ai.get("impact") or ai.get("marketImpact") or "见分层分析"
                news_item["aiStatus"] = "DeepSeek AI"
                
        output.append(news_item)
        
    for i, item in enumerate(output):
        item["id"] = f"NO.{len(output) - i:02d}"
    return output

def main():
    if not PAGE.exists(): return
    page = PAGE.read_text(encoding="utf-8")
    sm, em = "<!-- NEWS_DATA_START -->", "<!-- NEWS_DATA_END -->"
    if sm not in page or em not in page: return
    
    cache, budget = load_cache(), {"attempts": 0, "successes": 0, "failures": 0}
    news = {ch: build(ch, cache, budget) for ch in ("global", "asia", "china", "earnings")}
    news["updatedAt"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    news["aiEnabled"] = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    news["aiModel"] = "deepseek-chat"
    news["aiStats"] = {**budget, "maxPerRun": MAX_AI_CALLS}
    
    start = page.index(sm) + len(sm)
    end = page.index(em, start)
    data_block = "\n<script>window.newsData = " + json.dumps(news, ensure_ascii=False).replace("</", "<\\/") + ";</script>\n"
    page = page[:start] + data_block + page[end:]
    page = re.sub(r"""(<span[^>]*id=["']updateTime["'][^>]*>).*?(</span>)""", lambda m: m.group(1) + html.escape(news["updatedAt"])+m.group(2), page, flags=re.I|re.S)
    
    PAGE.write_text(page, encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"更新完成。DeepSeek 尝试/成功/失败：{budget['attempts']}/{budget['successes']}/{budget['failures']}")

if __name__ == "__main__":
    main()
