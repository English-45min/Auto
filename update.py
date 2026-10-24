# -*- coding: utf-8 -*-
"""
财经研究助手 update.py

功能：
1. 抓取国际、亚太、国内财经及核心财报新闻。
2. 兼容 RSS 2.0 和 Atom。
3. 按发布时间过滤、按标题去重、按重要性排序。
4. 读取 GitHub Actions 环境变量中的 Gemini API 密钥及模型名称。
5. 每次运行最多进行 8 次 Gemini 分析调用。
6. 缓存 AI 分析结果，减少重复调用。
7. 只更新 index.html 的新闻数据区域，不修改页面布局。
8. 输出详细日志，方便定位新闻为空、API 失败等问题。
"""

import os
import re
import json
import time
import html
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ============================================================
# 1. 配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"
CACHE_FILE = BASE_DIR / "ai_cache.json"

BEIJING_TZ = timezone(timedelta(hours=8))

MAX_AI_CALLS = 8
MAX_NEWS_AGE_DAYS = 3
MAX_ITEMS_PER_FEED = 30
AI_TIMEOUT = 25
RETRIES = 1

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

if not GEMINI_MODEL:
    GEMINI_MODEL = "gemini-2.5-flash"

LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}

CHANNEL_NAMES = {
    "global": "国际财经新闻",
    "asia": "亚太财经新闻",
    "china": "国内财经新闻",
    "earnings": "核心财报新闻",
}

MAX_SUMMARY_CHARS = 1200


# ============================================================
# 2. 新闻源
# ============================================================

def google_news_rss(query):
    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    return (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode(params)
    )


FEEDS = {
    "global": [
        (
            "Reuters",
            google_news_rss(
                "site:reuters.com/world OR site:reuters.com/business "
                "economy markets finance central bank"
            ),
        ),
        (
            "Bloomberg",
            google_news_rss(
                "site:bloomberg.com markets economy finance"
            ),
        ),
        (
            "Financial Times",
            google_news_rss(
                "site:ft.com global economy finance markets"
            ),
        ),
        (
            "Wall Street Journal",
            google_news_rss(
                "site:wsj.com economy markets business"
            ),
        ),
        (
            "Associated Press",
            google_news_rss(
                "site:apnews.com business economy world"
            ),
        ),
        (
            "CNBC",
            "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        ),
        (
            "BBC World",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        ),
    ],

    "asia": [
        (
            "Nikkei Asia",
            "https://asia.nikkei.com/rss/feed/nar",
        ),
        (
            "Japan Times",
            "https://www.japantimes.co.jp/feed/",
        ),
        (
            "CNA Finance",
            "https://feeds.feedburner.com/rsscna/finance",
        ),
        (
            "Focus Taiwan",
            google_news_rss(
                "site:focustaiwan.tw economy business semiconductor"
            ),
        ),
        (
            "South China Morning Post",
            google_news_rss(
                "site:scmp.com Hong Kong Asia economy business markets"
            ),
        ),
        (
            "Asia-Pacific markets",
            google_news_rss(
                "Asia Pacific markets economy trade central bank"
            ),
        ),
    ],

    "china": [
        (
            "中国宏观经济",
            google_news_rss(
                "中国经济 OR 中国GDP OR 中国央行 OR 人民币 "
                "OR 中国房地产 OR 中国债务"
            ),
        ),
        (
            "中国股市",
            google_news_rss(
                "A股 OR 中国股市 OR 沪深300 OR 港股 OR 中国证券市场"
            ),
        ),
        (
            "中国财经政策",
            google_news_rss(
                "中国财政政策 OR 中国货币政策 OR 中国降息 "
                "OR 中国关税 OR 中国经济刺激"
            ),
        ),
        (
            "中国企业与贸易",
            google_news_rss(
                "中国出口 OR 中国企业 OR 中国制造业 "
                "OR 中国消费 OR 中国贸易"
            ),
        ),
        (
            "China economy",
            google_news_rss(
                '"China economy" OR "China GDP" OR PBOC '
                'OR "China property" OR "China stocks"'
            ),
        ),
    ],

    "earnings": [
        (
            "CNBC Earnings",
            "https://www.cnbc.com/id/15839135/device/rss/rss.html",
        ),
        (
            "MarketWatch",
            "https://feeds.marketwatch.com/marketwatch/marketpulse/",
        ),
        (
            "Financial Times Companies",
            "https://www.ft.com/companies?format=rss",
        ),
        (
            "Company earnings",
            google_news_rss(
                "company earnings revenue profit quarterly results guidance"
            ),
        ),
    ],
}


# ============================================================
# 3. 通用工具
# ============================================================

def log(message):
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def clean_text(value):
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value,
                   flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value,
                   flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def valid_http_url(url):
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_google_news_url(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host == "news.google.com" or host.endswith(".news.google.com")
    except Exception:
        return False


def http_get(url, timeout=18, headers=None):
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
        ),
        "Accept": (
            "application/rss+xml, application/atom+xml, "
            "application/xml, text/xml, */*"
        ),
    }

    if headers:
        request_headers.update(headers)

    request = Request(url, headers=request_headers)

    with urlopen(request, timeout=timeout) as response:
        return response.read(), response.geturl()


def parse_published_date(value):
    if not value:
        return None

    try:
        result = parsedate_to_datetime(value)

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)

    except Exception:
        pass

    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)

    except Exception:
        return None


def format_beijing_time(dt):
    if not dt:
        return ""

    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def normalize_title(title):
    title = clean_text(title).lower()
    title = re.sub(r"[^\w\u4e00-\u9fff]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def make_news_id(title, url):
    raw = normalize_title(title) + "|" + str(url or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ============================================================
# 4. 新闻链接处理
# ============================================================

def extract_original_url(url):
    """
    对聚合链接只提取明确存在的目标 URL。
    不猜测原文地址。
    无法解析时保留聚合链接，稍后在页面上明确标记。
    """
    if not valid_http_url(url):
        return ""

    if not is_google_news_url(url):
        return url

    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        for key in ("url", "u", "target", "article_url"):
            for candidate in params.get(key, []):
                candidate = html.unescape(candidate.strip())

                if (
                    valid_http_url(candidate)
                    and not is_google_news_url(candidate)
                ):
                    return candidate

    except Exception as exc:
        log(f"链接解析异常：{exc}")

    return url


def prepare_links(item):
    original = (item.get("url") or "").strip()
    resolved = extract_original_url(original)

    item["url"] = resolved

    if not resolved:
        item["linkStatus"] = "无有效链接"

    elif is_google_news_url(resolved):
        item["linkStatus"] = "聚合链接，原文尚未解析"

        query = item.get("title", "")
        item["searchUrl"] = (
            "https://www.google.com/search?q="
            + urllib.parse.quote(query)
        )

    else:
        item["linkStatus"] = "媒体链接"

    return item


# ============================================================
# 5. RSS / Atom 解析
# ============================================================

def child_text(element, name):
    child = element.find(name)

    if child is None:
        return ""

    return clean_text("".join(child.itertext()))


def parse_feed(source_name, feed_url):
    try:
        raw, final_url = http_get(feed_url, timeout=18)
        root = ET.fromstring(raw)

    except HTTPError as exc:
        log(f"RSS HTTP 错误：{source_name}，状态 {exc.code}")
        return []

    except URLError as exc:
        log(f"RSS 网络错误：{source_name}，{exc}")
        return []

    except Exception as exc:
        log(f"RSS 解析失败：{source_name}，{type(exc).__name__}: {exc}")
        return []

    items = []

    # RSS 2.0
    rss_items = root.findall(".//item")

    for entry in rss_items[:MAX_ITEMS_PER_FEED]:
        title = child_text(entry, "title")
        link = child_text(entry, "link")
        summary = (
            child_text(entry, "description")
            or child_text(entry, "{http://purl.org/rss/1.0/modules/content/}encoded")
        )

        published = (
            child_text(entry, "pubDate")
            or child_text(entry, "{http://purl.org/dc/elements/1.1/}date")
        )

        source_element = entry.find("source")

        source = (
            clean_text("".join(source_element.itertext()))
            if source_element is not None
            else source_name
        )

        if title and link and valid_http_url(link):
            items.append({
                "title": title,
                "url": link,
                "summary": summary[:MAX_SUMMARY_CHARS],
                "source": source or source_name,
                "publishedRaw": published,
                "feed": source_name,
            })

    # Atom
    if not items:
        atom_entries = root.findall(".//{*}entry")

        for entry in atom_entries[:MAX_ITEMS_PER_FEED]:
            title = child_text(entry, "{*}title")
            summary = (
                child_text(entry, "{*}summary")
                or child_text(entry, "{*}content")
            )

            link = ""

            for link_element in entry.findall("{*}link"):
                rel = link_element.attrib.get("rel", "alternate")
                href = link_element.attrib.get("href", "")

                if href and rel in ("alternate", ""):
                    link = urllib.parse.urljoin(final_url, href)
                    break

            published = (
                child_text(entry, "{*}published")
                or child_text(entry, "{*}updated")
            )

            if title and link and valid_http_url(link):
                items.append({
                    "title": title,
                    "url": link,
                    "summary": summary[:MAX_SUMMARY_CHARS],
                    "source": source_name,
                    "publishedRaw": published,
                    "feed": source_name,
                })

    log(f"RSS 来源：{source_name}，取得 {len(items)} 条")

    return items


# ============================================================
# 6. 分类
# ============================================================

CHINA_RE = re.compile(
    r"中国|大陆|A股|沪深|上证|深证|科创板|创业板|港股|恒生指数|"
    r"人民币|中国央行|中国人民银行|中国财政|中国经济|中国出口|"
    r"中国房地产|中国房企|中国制造业|中国消费|中国债务|"
    r"China economy|China GDP|Chinese economy|China stocks|"
    r"China property|China exports|China stimulus|China policy|"
    r"PBOC|renminbi|yuan|A-shares|CSI 300",
    re.I,
)

ASIA_RE = re.compile(
    r"日本|东京|日经|韩国|首尔|台湾|台积电|新加坡|香港|东南亚|"
    r"亚太|印度|印尼|越南|泰国|马来西亚|菲律宾|澳大利亚|"
    r"Japan|Tokyo|Nikkei|South Korea|Seoul|Taiwan|Singapore|"
    r"Hong Kong|Asia Pacific|Southeast Asia|India|Indonesia|"
    r"Vietnam|Thailand|Malaysia|Philippines|Australia",
    re.I,
)

EARNINGS_RE = re.compile(
    r"财报|季报|年报|业绩|营收|净利润|盈利|业绩指引|"
    r"earnings|quarterly results|annual results|revenue|"
    r"net income|profit|guidance|financial results",
    re.I,
)


def classify_news(item, preferred_channel):
    text = " ".join([
        item.get("title", ""),
        item.get("summary", ""),
        item.get("source", ""),
    ])

    # 财报作为独立栏目，但只在内容确实与业绩有关时优先归入。
    if preferred_channel == "earnings" and EARNINGS_RE.search(text):
        return "earnings"

    if preferred_channel == "china":
        if CHINA_RE.search(text):
            return "china"

    if preferred_channel == "asia":
        if ASIA_RE.search(text) and not CHINA_RE.search(text):
            return "asia"

    if preferred_channel == "global":
        if not CHINA_RE.search(text) and not ASIA_RE.search(text):
            return "global"

    # 兜底：按内容判断，避免某个栏目因来源英文而直接为空。
    if CHINA_RE.search(text):
        return "china"

    if ASIA_RE.search(text):
        return "asia"

    if EARNINGS_RE.search(text):
        return "earnings"

    return "global"


# ============================================================
# 7. 新闻重要性排序
# ============================================================

IMPORTANCE_RULES = [
    (
        re.compile(
            r"美联储|央行|利率决议|通胀|CPI|PCE|非农|就业报告|"
            r"Federal Reserve|interest rate|inflation|CPI|PCE|"
            r"central bank|jobs report",
            re.I,
        ),
        25,
    ),
    (
        re.compile(
            r"战争|制裁|关税|贸易战|违约|金融危机|银行倒闭|"
            r"war|sanctions|tariffs|trade war|default|financial crisis",
            re.I,
        ),
        25,
    ),
    (
        re.compile(
            r"GDP|经济衰退|国债收益率|美元|人民币汇率|"
            r"recession|Treasury yields|dollar|exchange rate",
            re.I,
        ),
        18,
    ),
    (
        re.compile(
            r"财报|营收|净利润|业绩预警|"
            r"earnings|revenue|net income|profit warning|guidance",
            re.I,
        ),
        12,
    ),
    (
        re.compile(
            r"原油|黄金|半导体|芯片|人工智能|"
            r"oil|crude|gold|semiconductor|chip|artificial intelligence",
            re.I,
        ),
        8,
    ),
]


def calculate_importance(item):
    text = " ".join([
        item.get("title", ""),
        item.get("summary", ""),
    ])

    score = 20

    for pattern, points in IMPORTANCE_RULES:
        if pattern.search(text):
            score += points

    published = item.get("timestamp", 0)

    if published:
        age_hours = max(
            0,
            (time.time() - published) / 3600,
        )

        if age_hours < 12:
            score += 15
        elif age_hours < 24:
            score += 10
        elif age_hours < 48:
            score += 5

    if item.get("url") and not is_google_news_url(item["url"]):
        score += 5

    return min(score, 100)


# ============================================================
# 8. 缓存
# ============================================================

def load_cache():
    if not CACHE_FILE.exists():
        return {}

    try:
        with CACHE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except Exception as exc:
        log(f"缓存读取失败，将创建新缓存：{exc}")
        return {}


def save_cache(cache):
    try:
        with CACHE_FILE.open("w", encoding="utf-8") as file:
            json.dump(
                cache,
                file,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as exc:
        log(f"缓存写入失败：{exc}")


def cache_key(item):
    raw = (
        normalize_title(item.get("title", ""))
        + "|"
        + item.get("url", "")
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# 9. Gemini API
# ============================================================

def call_gemini(prompt):
    """
    返回：(结果文本, 错误信息)
    API 密钥只从环境变量读取，不打印密钥。
    """
    global GEMINI_API_KEY

    if not GEMINI_API_KEY:
        return None, (
            "未检测到 GEMINI_API_KEY 环境变量；"
            "请检查 GitHub Actions Secrets 名称。"
        )

    model = GEMINI_MODEL.strip()

    if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
        return None, "GEMINI_MODEL 格式无效。"

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + urllib.parse.quote(model, safe="")
        + ":generateContent"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1200,
            "responseMimeType": "application/json",
        },
    }

    body = json.dumps(payload).encode("utf-8")

    request = Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
            "User-Agent": "FinanceResearchAssistant/3.2",
        },
        method="POST",
    )

    for attempt in range(RETRIES + 1):
        try:
            with urlopen(request, timeout=AI_TIMEOUT) as response:
                raw = response.read().decode("utf-8", errors="replace")

            response_json = json.loads(raw)
            candidates = response_json.get("candidates") or []

            if not candidates:
                return None, (
                    "Gemini 没有返回候选内容；"
                    "可能是请求被安全过滤或响应为空。"
                )

            parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
            )

            text = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict)
            ).strip()

            if not text:
                return None, "Gemini 返回了空文本。"

            return text, None

        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                detail_json = json.loads(detail)
                message = (
                    detail_json.get("error", {}).get("message")
                    or detail[:500]
                )
            except Exception:
                message = str(exc)

            if exc.code == 400:
                return None, f"Gemini 请求参数错误（400）：{message}"

            if exc.code == 401:
                return None, (
                    "Gemini API 返回 401：密钥无效或未被接受。"
                    "请检查 Secret 是否正确，不要把密钥发到聊天中。"
                )

            if exc.code == 403:
                return None, (
                    "Gemini API 返回 403：项目权限、API 服务或模型访问"
                    f"可能存在问题。详情：{message}"
                )

            if exc.code == 404:
                return None, (
                    f"Gemini API 返回 404：模型 {model} 不存在或当前接口"
                    f"不支持。请在环境变量 GEMINI_MODEL 中配置有效模型。"
                )

            if exc.code == 429:
                return None, (
                    "Gemini API 返回 429：请求频率或项目额度受限。"
                    f"详情：{message}"
                )

            if exc.code >= 500 and attempt < RETRIES:
                time.sleep(2)
                continue

            return None, f"Gemini HTTP {exc.code}：{message}"

        except URLError as exc:
            if attempt < RETRIES:
                time.sleep(2)
                continue

            return None, f"Gemini 网络连接失败：{exc}"

        except Exception as exc:
            return None, (
                f"Gemini 调用异常：{type(exc).__name__}: {exc}"
            )

    return None, "Gemini 请求重试后仍然失败。"


def extract_json(text):
    text = (text or "").strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.I,
    )

    text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")

    if start < 0 or end <= start:
        raise ValueError("AI 返回内容中没有有效 JSON 对象")

    return json.loads(text[start:end + 1])


def analyze_news(item):
    prompt = f"""
你是一名谨慎的财经研究助理。请根据给定新闻标题和摘要进行结构化分析。

约束：
1. 不得把推测写成事实。
2. 不得虚构新闻中不存在的数字、公告、引用或证据。
3. 信息不足时明确写“信息不足，需核实”。
4. 不预测确定的涨跌，不提供保证收益的结论。
5. 用简体中文回答。
6. 只返回合法 JSON，不要使用 Markdown 代码围栏。

请返回以下字段：
{{
  "analysis": "简明综合分析",
  "coreLogic": "核心逻辑",
  "marketImpact": "潜在市场影响",
  "transmission": "可能的传导机制",
  "timeHorizon": "可能的影响周期",
  "evidence": ["需要核验的证据或信息线索"],
  "scenarios": ["条件性情景及其成立条件"],
  "risk": "风险、反证和信息缺口",
  "confidence": "低/中/高"
}}

新闻标题：{item.get("title", "")}
媒体来源：{item.get("source", "")}
发布时间：{item.get("publishedAt", "")}
新闻摘要：{item.get("summary", "")}
原始链接：{item.get("url", "")}
"""

    raw, error = call_gemini(prompt)

    if error:
        return None, error

    try:
        result = extract_json(raw)
    except Exception as exc:
        return None, f"AI JSON 解析失败：{exc}"

    required = [
        "analysis",
        "coreLogic",
        "marketImpact",
        "transmission",
        "timeHorizon",
        "evidence",
        "scenarios",
        "risk",
        "confidence",
    ]

    for key in required:
        result.setdefault(key, "" if key not in ("evidence", "scenarios") else [])

    for key in ("evidence", "scenarios"):
        if not isinstance(result.get(key), list):
            result[key] = []

        result[key] = [
            clean_text(value)[:400]
            for value in result[key][:5]
        ]

    for key in (
        "analysis",
        "coreLogic",
        "marketImpact",
        "transmission",
        "timeHorizon",
        "risk",
        "confidence",
    ):
        result[key] = clean_text(result.get(key, ""))[:1500]

    return result, None


# ============================================================
# 10. 新闻整理
# ============================================================

def collect_news():
    collected = {key: [] for key in LIMITS}

    stats = {
        key: {
            "sources": 0,
            "fetched": 0,
            "accepted": 0,
            "expired": 0,
            "duplicates": 0,
        }
        for key in LIMITS
    }

    seen = set()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_NEWS_AGE_DAYS)

    for preferred_channel, feeds in FEEDS.items():
        for source_name, feed_url in feeds:
            stats[preferred_channel]["sources"] += 1

            entries = parse_feed(source_name, feed_url)
            stats[preferred_channel]["fetched"] += len(entries)

            for entry in entries:
                title = clean_text(entry.get("title", ""))

                if not title:
                    continue

                published_dt = parse_published_date(
                    entry.get("publishedRaw", "")
                )

                # 没有发布时间时不假造时间。
                # 保留条目，但标记为未知时间，方便后续检查。
                if published_dt and published_dt < cutoff:
                    stats[preferred_channel]["expired"] += 1
                    continue

                item = {
                    "title": title,
                    "originalTitle": title,
                    "summary": clean_text(
                        entry.get("summary", "")
                    )[:MAX_SUMMARY_CHARS],
                    "source": clean_text(
                        entry.get("source", source_name)
                    ),
                    "url": entry.get("url", ""),
                    "feed": source_name,
                    "publishedAt": (
                        format_beijing_time(published_dt)
                        if published_dt
                        else "发布时间未知"
                    ),
                    "timestamp": (
                        int(published_dt.timestamp())
                        if published_dt
                        else 0
                    ),
                }

                prepare_links(item)

                key = normalize_title(item["title"])

                if not key or key in seen:
                    stats[preferred_channel]["duplicates"] += 1
                    continue

                seen.add(key)

                channel = classify_news(item, preferred_channel)

                item["id"] = make_news_id(
                    item["title"],
                    item["url"],
                )

                item["importance"] = calculate_importance(item)

                collected[channel].append(item)
                stats[preferred_channel]["accepted"] += 1

    # 每个栏目单独排序及限制条数。
    for channel in collected:
        collected[channel].sort(
            key=lambda item: (
                item.get("importance", 0),
                item.get("timestamp", 0),
            ),
            reverse=True,
        )

        collected[channel] = collected[channel][:LIMITS[channel]]

    log("========== 新闻抓取统计 ==========")

    for channel in LIMITS:
        log(
            f"{CHANNEL_NAMES[channel]}："
            f"最终 {len(collected[channel])} 条；"
            f"来源 {stats[channel]['sources']} 个；"
            f"抓取 {stats[channel]['fetched']} 条；"
            f"过滤过期 {stats[channel]['expired']} 条；"
            f"重复 {stats[channel]['duplicates']} 条"
        )

    return collected, stats


# ============================================================
# 11. 翻译
# ============================================================

def translate_to_chinese(text):
    """
    仅在摘要明显没有中文时尝试 MyMemory。
    翻译失败时返回原文，不影响整个更新任务。
    """
    text = clean_text(text)

    if not text or re.search(r"[\u4e00-\u9fff]", text):
        return text

    try:
        params = urllib.parse.urlencode({
            "q": text[:450],
            "langpair": "en|zh-CN",
        })

        url = "https://api.mymemory.translated.net/get?" + params

        raw, _ = http_get(
            url,
            timeout=8,
            headers={"Accept": "application/json"},
        )

        data = json.loads(raw.decode("utf-8", errors="replace"))

        translated = clean_text(
            data.get("responseData", {}).get("translatedText", "")
        )

        if translated and translated.lower() != text.lower():
            return translated

    except Exception as exc:
        log(f"翻译服务不可用，保留原文：{type(exc).__name__}")

    return text


# ============================================================
# 12. AI 分析与缓存
# ============================================================

def enrich_with_ai(news):
    cache = load_cache()

    attempts = 0
    successes = 0
    failures = 0
    cached_count = 0
    last_error = ""

    if not GEMINI_API_KEY:
        log(
            "警告：没有读取到 GEMINI_API_KEY。"
            "新闻仍会更新，但本次不会进行 AI 分析。"
        )
    else:
        log(f"Gemini 模型配置：{GEMINI_MODEL}")
        log(f"本次 AI 调用上限：{MAX_AI_CALLS}")

    # 跨栏目按重要性挑选新闻，最多尝试 MAX_AI_CALLS 次。
    candidates = []

    for channel, items in news.items():
        for item in items:
            item["channel"] = channel
            item["summary"] = translate_to_chinese(
                item.get("summary", "")
            )
            candidates.append(item)

    candidates.sort(
        key=lambda item: (
            item.get("importance", 0),
            item.get("timestamp", 0),
        ),
        reverse=True,
    )

    for item in candidates:
        key = cache_key(item)

        if key in cache and isinstance(cache[key], dict):
            item.update(cache[key])
            item["aiStatus"] = "Gemini AI 缓存"
            cached_count += 1
            continue

        if not GEMINI_API_KEY:
            item["aiStatus"] = "未配置 API 密钥"
            continue

        if attempts >= MAX_AI_CALLS:
            item["aiStatus"] = "达到本次 AI 调用上限"
            continue

        attempts += 1

        log(
            f"AI 分析 {attempts}/{MAX_AI_CALLS}："
            f"{item.get('title', '')[:90]}"
        )

        result, error = analyze_news(item)

        if error:
            failures += 1
            last_error = error
            item["aiStatus"] = "AI 分析失败"
            item["aiError"] = error

            log(f"AI 分析失败：{error}")
            continue

        item.update(result)
        item["aiStatus"] = "Gemini AI"
        item["aiError"] = ""

        cache[key] = result
        successes += 1

    save_cache(cache)

    log(
        "AI 统计："
        f"本次尝试 {attempts} 次，"
        f"成功 {successes} 次，"
        f"失败 {failures} 次，"
        f"命中缓存 {cached_count} 条"
    )

    if last_error:
        log(f"最近一次 AI 错误：{last_error}")

    return {
        "attempts": attempts,
        "successes": successes,
        "failures": failures,
        "cached": cached_count,
        "lastError": last_error,
    }


# ============================================================
# 13. 写入 HTML
# ============================================================

def safe_json(data):
    """
    防止新闻文本中的特殊字符破坏 HTML 内的 script 标签。
    """
    serialized = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    serialized = serialized.replace("<", "\\u003c")
    serialized = serialized.replace(">", "\\u003e")
    serialized = serialized.replace("&", "\\u0026")

    return serialized


def build_page_data(news, ai_stats):
    now = datetime.now(BEIJING_TZ)

    return {
        "global": news["global"],
        "asia": news["asia"],
        "china": news["china"],
        "earnings": news["earnings"],
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "aiEnabled": bool(GEMINI_API_KEY),
        "aiModel": GEMINI_MODEL if GEMINI_API_KEY else "",
        "aiStats": ai_stats,
    }


def update_html(page_data):
    if not HTML_FILE.exists():
        raise FileNotFoundError(
            f"找不到 {HTML_FILE}；请确认 update.py 与 index.html 位于正确位置。"
        )

    content = HTML_FILE.read_text(encoding="utf-8")

    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    start = content.find(start_marker)
    end = content.find(end_marker)

    if start < 0 or end < 0 or end <= start:
        raise RuntimeError(
            "index.html 缺少有效的 NEWS_DATA_START / NEWS_DATA_END 标记。"
            "为避免破坏页面，程序停止写入。"
        )

    start_end = start + len(start_marker)

    replacement = (
        "\n<script>\n"
        "window.newsData = "
        + safe_json(page_data)
        + ";\n"
        "</script>\n"
    )

    new_content = (
        content[:start_end]
        + replacement
        + content[end:]
    )

    # 先写临时文件，成功后再替换，降低写入中断风险。
    temp_file = HTML_FILE.with_suffix(".html.tmp")

    temp_file.write_text(new_content, encoding="utf-8")
    temp_file.replace(HTML_FILE)

    log("已更新 index.html 新闻数据区域。")
    log(f"页面更新时间：{page_data['updatedAt']}")

    for channel in LIMITS:
        log(
            f"写入栏目 {channel}："
            f"{len(page_data[channel])} 条"
        )


# ============================================================
# 14. 主流程
# ============================================================

def main():
    log("=" * 60)
    log("财经研究助手开始运行")
    log(f"Python 文件：{Path(__file__).resolve()}")
    log(f"工作目录：{BASE_DIR}")
    log(f"HTML 文件存在：{HTML_FILE.exists()}")
    log(f"AI 密钥已配置：{'是' if GEMINI_API_KEY else '否'}")

    if GEMINI_API_KEY:
        log(f"Gemini 模型：{GEMINI_MODEL}")
    else:
        log(
            "提示：请在 GitHub Settings → Secrets and variables "
            "→ Actions 中检查 GEMINI_API_KEY。"
        )

    log("=" * 60)

    if not HTML_FILE.exists():
        raise FileNotFoundError(
            "找不到 index.html，无法更新网页。"
        )

    content = HTML_FILE.read_text(encoding="utf-8")

    if (
        "<!-- NEWS_DATA_START -->" not in content
        or "<!-- NEWS_DATA_END -->" not in content
    ):
        raise RuntimeError(
            "index.html 缺少新闻数据标记，拒绝覆盖文件。"
        )

    news, feed_stats = collect_news()

    total = sum(len(items) for items in news.values())

    if total == 0:
        log(
            "警告：所有栏目均未获取到新闻。"
            "仍将写入更新时间及错误状态，"
            "请查看前面的 RSS 错误日志。"
        )

    ai_stats = enrich_with_ai(news)

    page_data = build_page_data(news, ai_stats)

    # 把抓取统计写进 JSON，便于之后检查。
    page_data["feedStats"] = feed_stats

    update_html(page_data)

    log("=" * 60)
    log("更新任务结束")
    log(f"新闻总数：{total}")
    log(
        "栏目统计："
        + "，".join(
            f"{channel}={len(news[channel])}"
            for channel in LIMITS
        )
    )
    log(
        f"AI 调用成功率：{ai_stats['successes']}/"
        f"{ai_stats['attempts']}（本次尝试）"
    )
    log("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"任务失败：{type(exc).__name__}: {exc}")
        raise
