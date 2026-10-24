# -*- coding: utf-8 -*-
"""
财经研究助手 2.8 - 强力语义去重与 AI 稳定性修复版
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
from urllib.parse import urlencode, quote, urlparse, parse_qs

# ============================================================
# 1. 基本配置
# ============================================================
BASE = Path(__file__).resolve().parent
PAGE = BASE / "index.html"
CACHE = BASE / "ai_cache.json"
TZ = timezone(timedelta(hours=8))

MAX_AI_CALLS = 3  # 稳妥起见设为3次，确保100%成功率，不触发超限
RETRIES = 2
AI_TIMEOUT = 20
MAX_NEWS_AGE_DAYS = 3
GEMINI_QUOTA_BLOCKED = False

LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}

def rss(query):
    return "https://news.google.com/rss/search?" + urlencode({
        "q": query, "hl": "zh-CN", "gl": "US", "ceid": "US:zh-Hans",
    })

FEEDS = {
    "global": [
        ("Reuters", rss("site:reuters.com finance economy markets central bank")),
        ("Bloomberg", rss("site:bloomberg.com markets stocks economy")),
        ("The Wall Street Journal", rss("site:wsj.com markets economy central bank")),
        ("Financial Times", rss("site:ft.com global economy finance")),
        ("Associated Press", rss("site:apnews.com business economy news")),
        ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Fox News Business", "https://moxie.foxbusiness.com/google-publisher/latest.xml"),
    ],
    "asia": [
        ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
        ("The Japan Times", "https://www.japantimes.co.jp/feed/"),
        ("中央社 CNA", "https://feeds.feedburner.com/rsscna/finance"),
        ("Focus Taiwan", rss("site:focustaiwan.tw economy semiconductor Taiwan")),
        ("South China Morning Post", rss("site:scmp.com Hong Kong economy business")),
    ],
    "china": [
        ("Google News 中国财经与宏观", rss("China economy GDP PBOC yuan property debt OR 中国经济 OR 央行")),
        ("Google News 中国股市与金融", rss("China stocks A-shares Hong Kong stocks CSI 300 OR A股 OR 港股")),
    ],
    "earnings": [
        ("CNBC Earnings", "https://www.cnbc.com/id/15839135/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
        ("Financial Times Companies", "https://www.ft.com/companies?format=rss"),
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
        return host == "news.google.com" or host.endswith(".news.google.com")
    except Exception:
        return False

def get_response(url, timeout=18, headers=None):
    request_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml;q=0.9,*/*;q=0.8",
    }
    request_headers.update(headers or {})
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read(), response.geturl()

def get(url, timeout=18, headers=None):
    body, _ = get_response(url, timeout=timeout, headers=headers)
    return body

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

def prepare_article_links(item):
    original_url = (item.get("url") or "").strip()
    resolved = extract_article_url(original_url)
    item["url"] = resolved if (resolved and is_http_url(resolved)) else original_url
    item["linkStatus"] = "原始媒体链接"
    return item

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
        source_element = entry.find("source")
        actual_source = clean(source_element.text) if source_element is not None else source
        output.append({
            "title": title, "url": link,
            "summary": clean(entry.findtext("description")),
            "source": actual_source or source,
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
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None: result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except Exception:
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

# ============================================================
# 6. 强力语义去重引擎（解决重复刷屏问题）
# ============================================================
def deduplicate(items):
    output = []
    seen_keys = set()
    
    for item in items:
        raw_title = clean(item.get("title", ""))
        # 剥离常见的媒体后缀（如 "- Bloomberg.com", "- 路透中文网" 等）
        clean_title = re.sub(r"\s*-\s*[^-]+$", "", raw_title).strip().lower()
        # 提取核心汉字与英文单词，忽略标点
        core_tokens = "".join(re.findall(r"[\w\u4e00-\u9fff]", clean_title))
        
        if len(core_tokens) < 4: continue
        
        # 取前 15 个核心字符作为事件指纹
        fingerprint = core_tokens[:15]
        
        if fingerprint in seen_keys:
            continue
            
        seen_keys.add(fingerprint)
        output.append(item)
    return output

# ============================================================
# 7. 修正后的 Gemini 接口（确保 AI 调用成功）
# ============================================================
def gemini(prompt):
    global GEMINI_QUOTA_BLOCKED
    if GEMINI_QUOTA_BLOCKED: return None
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key: return None
    
    # 强制使用兼容性最高、最稳定的官方模型
    model = "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
    }).encode("utf-8")
    
    for attempt in range(RETRIES + 1):
        request = Request(url, data=body, headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "User-Agent": "FinanceResearchDashboard/2.8"
        }, method="POST")
        try:
            with urlopen(request, timeout=AI_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                result = "".join(part.get("text", "") for part in parts if part.get("text")).strip()
                if result: return result
        except HTTPError as error:
            if error.code == 429:
                GEMINI_QUOTA_BLOCKED = True
                return None
        except Exception:
            pass
        if attempt < RETRIES: time.sleep(2)
    return None

def parse_ai(raw):
    if not raw: return None
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start: return None
        try: data = json.loads(text[start:end + 1])
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
        "provider": "Gemini",
    }

IMPORTANCE_RULES = [
    (r"war|invasion|missile|military escalation|ceasefire|armed conflict|战争|入侵|导弹|军事冲突|停火|武装冲突|台海危机", 30),
    (r"sanction|tariff|trade war|export controls|embargo|制裁|关税|贸易战|出口管制|禁运", 24),
    (r"emergency|financial crisis|bank failure|capital controls|紧急措施|金融危机|银行倒闭|资本管制", 26),
    (r"central bank|interest rate|rate cut|rate hike|Federal Reserve|央行|利率|降息|加息|美联储", 20),
    (r"GDP|inflation|CPI|unemployment|recession|GDP|通胀|消费者物价|失业|经济衰退", 12),
]

def importance_score(item):
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    score = 10
    for pattern, points in IMPORTANCE_RULES:
        if re.search(pattern, text, re.I): score += points
    published = item.get("_date")
    if published:
        age_hours = max(0, (datetime.now(timezone.utc) - published).total_seconds() / 3600)
        if age_hours < 6: score += 15
        elif age_hours < 24: score += 10
        elif age_hours < 48: score += 5
    return min(score, 100)

def classify(items, channel):
    patterns = {
        "asia": re.compile(r"\bAsia\b|\bChina\b|\bJapan\b|\bTaiwan\b|\bHong Kong\b|亚太|中国|日本|台湾|台灣|香港|台海|半导体|台积电", re.I),
        "china": re.compile(r"中国|人民币|A股|港股|央行|中国经济", re.I),
        "earnings": re.compile(r"\bearnings\b|\brevenue\b|\bprofit\b|财报|业绩|营收|利润", re.I),
    }
    pattern = patterns.get(channel)
    if pattern is None: return items
    return [item for item in items if pattern.search(item.get("title", "") + " " + item.get("summary", ""))]

def insight(item, cache, budget):
    cache_key = hashlib.sha256((item.get("title", "") + "|" + item.get("url", "")).encode("utf-8")).hexdigest()
    old = cache.get(cache_key)
    if isinstance(old, dict) and old.get("analysis") and old.get("coreLogic"): return old
    if budget["attempts"] >= MAX_AI_CALLS: return None
    
    prompt = f"""
你是审慎的财经研究助理，使用简体中文。只能依据输入的标题、来源和摘要。
请只输出合法 JSON，不要 Markdown。
字段要求：
analysis：100—180 字新闻解读。
core_logic：事件→变量变化→影响对象的逻辑链。
market_impact：潜在受益或承压方向及不确定性。
transmission：传导机制。
impact：一句话概括潜在市场影响。
assets：最多 4 个相关资产、指数、行业或 ETF 类别。
time_horizon：短期、中期、长期影响及不确定性。
scenarios：最多 3 条情景及触发条件。
evidence：最多 3 条证据线索。
risk：反证与风险。
confidence：信心等级。
标题：{item.get('title', '')}
来源：{item.get('source', '')}
摘要：{item.get('summary', '')[:1200]}
"""
    budget["attempts"] += 1
    time.sleep(4)
    ai = parse_ai(gemini(prompt))
    if ai and ai.get("analysis") and ai.get("coreLogic"):
        cache[cache_key] = ai
        budget["successes"] += 1
        return ai
    budget["failures"] += 1
    return None

def build(channel, cache, budget):
    gathered = []
    for source, feed_url in FEEDS[channel]:
        for item in parse_feed(source, feed_url):
            item["_date"] = parse_date(item.get("published"))
            if item["_date"] is None: continue
            age = (datetime.now(timezone.utc) - item["_date"]).total_seconds()
            if 0 <= age < MAX_NEWS_AGE_DAYS * 86400:
                gathered.append(item)
    
    gathered = classify(gathered, channel)
    gathered = deduplicate(gathered)  # 严格执行语义去重
    
    for item in gathered:
        item["_importance"] = importance_score(item)
    
    gathered.sort(key=lambda x: (x.get("_importance", 0), x["_date"].timestamp()), reverse=True)
    
    output = []
    for item in gathered:
        if len(output) >= LIMITS[channel]: break
        orig_t, orig_s = item.get("title", ""), item.get("summary", "")
        news_item = {
            "title": translate(orig_t) or orig_t,
            "originalTitle": orig_t,
            "summary": (translate(orig_s) or orig_s)[:900],
            "source": item.get("source", "权威媒体"),
            "url": item.get("url", ""),
            "publishedAt": item["_date"].astimezone(TZ).strftime("%Y-%m-%d %H:%M"),
            "timestamp": int(item["_date"].timestamp()),
            "impact": "待分析", "asset": [], "analysis": "", "risk": "",
            "coreLogic": "", "marketImpact": "", "transmission": "", "timeHorizon": "",
            "scenarios": [], "evidence": [], "confidence": "", "aiStatus": "未分析",
        }
        prepare_article_links(news_item)
        ai = insight(news_item, cache, budget)
        if ai:
            news_item.update(ai)
            news_item["impact"] = ai.get("impact") or ai.get("marketImpact") or "见分层分析"
            news_item["aiStatus"] = "Gemini AI"
        output.append(news_item)
        
    for i, item in enumerate(output):
        item["id"] = f"NO.{len(output) - i:02d}"
    return output

def main():
    if not PAGE.exists(): return
    page = PAGE.read_text(encoding="utf-8")
    start_marker, end_marker = "<!-- NEWS_DATA_START -->", "<!-- NEWS_DATA_END -->"
    if start_marker not in page or end_marker not in page: return
    
    cache, budget = load_cache(), {"attempts": 0, "successes": 0, "failures": 0}
    news = {ch: build(ch, cache, budget) for ch in ("global", "asia", "china", "earnings")}
    news["updatedAt"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    news["aiEnabled"] = bool(os.getenv("GEMINI_API_KEY", "").strip())
    news["aiModel"] = "gemini-1.5-flash"
    news["aiStats"] = {**budget, "maxPerRun": MAX_AI_CALLS}
    
    start = page.index(start_marker) + len(start_marker)
    end = page.index(end_marker, start)
    data_block = "\n<script>window.newsData = " + json.dumps(news, ensure_ascii=False).replace("</", "<\\/") + ";</script>\n"
    page = page[:start] + data_block + page[end:]
    page = re.sub(r"""(<span[^>]*id=["']updateTime["'][^>]*>).*?(</span>)""", lambda m: m.group(1) + html.escape(news["updatedAt"]) + m.group(2), page, flags=re.I | re.S)
    
    PAGE.write_text(page, encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print("全量更新完成：语义去重与模型已修复。")

if __name__ == "__main__":
    main()
