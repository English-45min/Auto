# -*- coding: utf-8 -*-
"""
财经研究助手：新闻自动更新脚本
文件名：update.py

功能：
1. 获取国际财经、亚太财经、国内财经及公司财报新闻
2. 筛选最近三天的新闻
3. 按重要性评分排序
4. 中文标题与摘要翻译
5. Gemini AI 新闻分析，每次运行最多调用 8 次
6. 缓存 AI 分析，减少重复调用
7. 对新闻进行相似度去重
8. 更新 index.html 中的 NEWS_DATA 标记区域
9. 保留真实原文链接；无法确认时明确标记

依赖：Python 标准库
环境变量：GEMINI_API_KEY（可选，但 AI 分析需要）
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


# =========================================================
# 一、基本设置
# =========================================================

BASE = Path(__file__).resolve().parent
PAGE = BASE / "index.html"
CACHE = BASE / "ai_cache.json"

TZ = timezone(timedelta(hours=8))

MAX_AI_CALLS = 8
RETRIES = 1
AI_TIMEOUT = 25
MAX_NEWS_AGE_DAYS = 3

LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}

GEMINI_MODEL = "gemini-2.5-flash"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

GEMINI_QUOTA_BLOCKED = False


# =========================================================
# 二、新闻来源
# =========================================================

def google_rss(query):
    """生成 Google News RSS 搜索地址。"""
    params = {
        "q": query,
        "hl": "zh-CN",
        "gl": "US",
        "ceid": "US:zh-Hans",
    }
    return (
        "https://news.google.com/rss/search?"
        + urlencode(params)
    )


FEEDS = {
    "global": [
        google_rss(
            "site:reuters.com markets economy central bank "
            "war sanctions tariffs oil"
        ),
        google_rss(
            "site:bloomberg.com markets economy finance "
            "Federal Reserve trade"
        ),
        google_rss(
            "site:ft.com world economy markets inflation"
        ),
        google_rss(
            "site:wsj.com markets economy business"
        ),
        google_rss(
            "site:apnews.com economy business world conflict"
        ),
        google_rss(
            "site:cnbc.com markets economy Federal Reserve"
        ),
        google_rss(
            "site:bbc.com/news business world economy conflict"
        ),
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://moxie.foxbusiness.com/google-publisher/latest.xml",
    ],

    "asia": [
        "https://asia.nikkei.com/rss/feed/nar",
        "https://www.japantimes.co.jp/feed/",
        "https://feeds.feedburner.com/rsscna/finance",
        google_rss(
            "Taiwan economy stocks semiconductors TSMC finance"
        ),
        google_rss(
            "Taiwan cross-strait relations US policy Taiwan Strait"
        ),
        google_rss(
            "Japan economy Bank of Japan yen markets"
        ),
        google_rss(
            "South Korea economy semiconductor exports markets"
        ),
        google_rss(
            "Hong Kong markets economy finance"
        ),
        google_rss(
            "Asia Pacific economy trade tariffs supply chain"
        ),
        google_rss(
            "site:scmp.com business economy markets Asia"
        ),
    ],

    "china": [
        google_rss(
            "China economy GDP inflation deflation stimulus"
        ),
        google_rss(
            "China stocks A-shares CSI 300 Shanghai Shenzhen"
        ),
        google_rss(
            "China property real estate debt local government"
        ),
        google_rss(
            "People's Bank of China PBOC yuan interest rates"
        ),
        google_rss(
            "China exports imports trade tariffs manufacturing"
        ),
        google_rss(
            "China Hong Kong stocks capital markets regulation"
        ),
        google_rss(
            "中国 宏观经济 GDP 通胀 货币政策"
        ),
        google_rss(
            "中国 A股 沪深300 股市 资本市场"
        ),
        google_rss(
            "中国 房地产 地方债 经济刺激政策"
        ),
        google_rss(
            "人民币 汇率 中国人民银行 利率"
        ),
    ],

    "earnings": [
        "https://www.cnbc.com/id/15839135/device/rss/rss.html",
        "https://feeds.marketwatch.com/marketwatch/marketpulse/",
        "https://www.ft.com/companies?format=rss",
        google_rss(
            "company earnings quarterly results revenue profit"
        ),
        google_rss(
            "US technology companies earnings guidance"
        ),
        google_rss(
            "semiconductor company earnings outlook"
        ),
    ],
}


# =========================================================
# 三、基础工具
# =========================================================

def now_local():
    return datetime.now(TZ)


def clean(text):
    """清除 HTML 标签并解码实体。"""
    if not text:
        return ""

    text = html.unescape(str(text))
    text = re.sub(
        r"(?is)<(script|style).*?>.*?</\1>",
        " ",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def contains_chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def is_http_url(url):
    if not url:
        return False

    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_google_news_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith("news.google.com")
    except Exception:
        return False


def get_response(url, timeout=15):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; FinanceDashboard/1.0)"
            ),
            "Accept": (
                "application/rss+xml, application/xml, "
                "text/xml, text/html, */*"
            ),
        },
    )

    with urlopen(request, timeout=timeout) as response:
        body = response.read()
        final_url = response.geturl()

    return body, final_url


def get(url, timeout=15):
    body, _ = get_response(url, timeout)
    return body


# =========================================================
# 四、新闻链接处理
# =========================================================

def extract_article_url(url):
    """
    尝试从 Google News URL 参数中提取明确的原文地址。

    无法确认时保留原始地址，不声称它是媒体直链。
    """
    if not url:
        return ""

    url = html.unescape(url.strip())

    if not is_http_url(url):
        return ""

    if not is_google_news_url(url):
        return url

    try:
        query = parse_qs(urlparse(url).query)

        for key in (
            "url",
            "u",
            "target",
            "article_url",
        ):
            values = query.get(key, [])

            for value in values:
                value = value.strip()

                if (
                    is_http_url(value)
                    and not is_google_news_url(value)
                ):
                    return value

    except Exception:
        pass

    return url


def prepare_article_links(item):
    url = item.get("url", "")

    if not url:
        item["linkStatus"] = "unavailable"
        return item

    if is_google_news_url(url):
        item["linkStatus"] = "aggregator"
    else:
        item["linkStatus"] = "original"

    return item


# =========================================================
# 五、RSS 解析
# =========================================================

def local_tag(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element, names):
    names = {name.lower() for name in names}

    for child in element.iter():
        if local_tag(child.tag) in names:
            value = child.text or ""

            if value.strip():
                return clean(value)

    return ""


def parse_date(value):
    if not value:
        return None

    value = value.strip()

    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(TZ)

    except Exception:
        pass

    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(TZ)

    except Exception:
        return None


def parse_feed(xml_data, feed_url):
    results = []

    try:
        root = ET.fromstring(xml_data)
    except Exception:
        return results

    for element in root.iter():
        tag = local_tag(element.tag)

        if tag not in ("item", "entry"):
            continue

        title = child_text(element, {"title"})
        summary = child_text(
            element,
            {"description", "summary", "encoded", "content"},
        )

        link = ""
        source = ""

        for child in element.iter():
            child_tag = local_tag(child.tag)

            if child_tag == "link":
                href = child.attrib.get("href", "").strip()
                rel = child.attrib.get("rel", "alternate")

                if href and rel in ("alternate", ""):
                    link = href
                    break

                if href and not link:
                    link = href

                if child.text and child.text.strip() and not link:
                    link = child.text.strip()

            elif child_tag == "source":
                source = clean(child.text or "")

        if not link:
            link = feed_url

        published = child_text(
            element,
            {
                "pubdate",
                "published",
                "updated",
                "date",
            },
        )

        date_value = parse_date(published)

        if not title:
            continue

        link = extract_article_url(link)

        if not source:
            try:
                host = urlparse(link).hostname or ""
                source = host.replace("www.", "")
            except Exception:
                source = "未知来源"

        results.append({
            "title": title,
            "summary": summary,
            "url": link,
            "source": source,
            "published": date_value,
            "feed": feed_url,
        })

    return results


# =========================================================
# 六、翻译
# =========================================================

def translate(text):
    text = clean(text)

    if not text:
        return ""

    if contains_chinese(text):
        return text

    try:
        endpoint = (
            "https://translate.googleapis.com/translate_a/single?"
            + urlencode({
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": text[:3000],
            })
        )

        raw = get(endpoint, timeout=8)
        data = json.loads(raw.decode("utf-8"))

        translated = "".join(
            part[0]
            for part in data[0]
            if part and part[0]
        )

        return clean(translated) or text

    except Exception:
        return text


# =========================================================
# 七、缓存与去重
# =========================================================

def load_cache():
    if not CACHE.exists():
        return {}

    try:
        with CACHE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def save_cache(cache):
    try:
        with CACHE.open("w", encoding="utf-8") as file:
            json.dump(
                cache,
                file,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        print("缓存保存失败：", exc)


def normalized_title(title):
    title = clean(title).lower()
    title = re.sub(r"https?://\S+", " ", title)
    title = re.sub(r"[^\w\u4e00-\u9fff]+", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def token_set(text):
    text = normalized_title(text)

    # 中文采用字符二元组；英文采用单词
    if contains_chinese(text):
        compact = text.replace(" ", "")

        if len(compact) <= 2:
            return {compact} if compact else set()

        return {
            compact[i:i + 2]
            for i in range(len(compact) - 1)
        }

    return set(text.split())


def similarity(a, b):
    left = token_set(a)
    right = token_set(b)

    if not left or not right:
        return 0.0

    return len(left & right) / len(left | right)


def deduplicate(items):
    result = []

    for item in items:
        duplicate = False

        for existing in result:
            same_url = (
                item.get("url")
                and item.get("url") == existing.get("url")
            )

            same_title = (
                similarity(
                    item.get("title", ""),
                    existing.get("title", ""),
                ) > 0.55
            )

            overlap = (
                len(
                    token_set(item.get("title", ""))
                    & token_set(existing.get("title", ""))
                ) >= 8
            )

            if same_url or same_title or overlap:
                duplicate = True
                break

        if not duplicate:
            result.append(item)

    return result


# =========================================================
# 八、新闻重要性评分
# =========================================================

IMPORTANCE_RULES = [
    (
        10,
        [
            "war", "invasion", "missile", "military escalation",
            "ceasefire", "armed conflict", "blockade",
            "战争", "入侵", "导弹", "军事升级", "停火",
            "武装冲突", "封锁", "台海危机",
        ],
    ),
    (
        9,
        [
            "financial crisis", "bank failure", "emergency meeting",
            "capital controls", "default", "sovereign debt crisis",
            "金融危机", "银行倒闭", "紧急会议",
            "资本管制", "违约", "主权债务危机",
        ],
    ),
    (
        8,
        [
            "sanctions", "tariffs", "export controls",
            "trade war", "embargo", "oil supply disruption",
            "central bank intervention", "rate cut", "rate hike",
            "制裁", "关税", "出口管制", "贸易战", "禁运",
            "石油供应中断", "央行干预", "降息", "加息",
        ],
    ),
    (
        7,
        [
            "federal reserve", "interest rates", "inflation",
            "recession", "unemployment", "gdp",
            "earnings warning", "profit warning",
            "美联储", "利率", "通胀", "衰退", "失业率",
            "国内生产总值", "盈利预警",
        ],
    ),
    (
        6,
        [
            "semiconductor", "artificial intelligence",
            "energy prices", "shipping", "supply chain",
            "election", "budget", "fiscal stimulus",
            "半导体", "人工智能", "能源价格", "航运",
            "供应链", "选举", "预算", "财政刺激",
        ],
    ),
]


def importance_score(item):
    text = (
        item.get("title", "")
        + " "
        + item.get("summary", "")
    ).lower()

    score = 1

    for points, keywords in IMPORTANCE_RULES:
        if any(word.lower() in text for word in keywords):
            score = max(score, points)

    # 信息量较大的新闻略微加分
    if len(item.get("summary", "")) > 160:
        score += 1

    return min(score, 10)


# =========================================================
# 九、栏目分类
# =========================================================

ASIA_TERMS = [
    "asia", "asian", "japan", "japanese", "taiwan",
    "taiwanese", "south korea", "korean", "hong kong",
    "singapore", "india", "indonesia", "thailand",
    "malaysia", "philippines", "vietnam", "pacific",
    "中国", "日本", "台湾", "台海", "韩国", "香港",
    "新加坡", "印度", "东南亚", "亚太",
]

CHINA_TERMS = [
    "china", "chinese", "mainland china", "renminbi",
    "yuan", "pboc", "people's bank of china",
    "a-shares", "csi 300", "shanghai composite",
    "shenzhen", "china property", "china real estate",
    "中国", "人民币", "央行", "A股", "沪深",
    "上证", "深证", "房地产", "地方债",
]

EARNINGS_TERMS = [
    "earnings", "quarterly results", "revenue",
    "net profit", "operating profit", "profit warning",
    "guidance", "quarterly report", "fiscal results",
    "财报", "业绩", "营收", "净利润", "盈利预警",
    "季度报告", "业绩指引",
]


def matches_terms(text, terms):
    lower = text.lower()
    return any(term.lower() in lower for term in terms)


def classify(item, channel):
    text = (
        item.get("title", "")
        + " "
        + item.get("summary", "")
        + " "
        + item.get("source", "")
    )

    if channel == "china":
        return matches_terms(text, CHINA_TERMS)

    if channel == "asia":
        return matches_terms(text, ASIA_TERMS)

    if channel == "earnings":
        return matches_terms(text, EARNINGS_TERMS)

    if channel == "global":
        return True

    return False


# =========================================================
# 十、Gemini AI 分析
# =========================================================

def call_gemini(prompt):
    global GEMINI_QUOTA_BLOCKED

    if not GEMINI_API_KEY or GEMINI_QUOTA_BLOCKED:
        return None

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + GEMINI_MODEL
        + ":generateContent?key="
        + GEMINI_API_KEY
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
            "responseMimeType": "application/json",
        },
    }

    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "FinanceDashboard/1.0",
        },
        method="POST",
    )

    for attempt in range(RETRIES + 1):
        try:
            with urlopen(request, timeout=AI_TIMEOUT) as response:
                result = json.loads(
                    response.read().decode("utf-8")
                )

            text = (
                result.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )

            if text:
                return text

        except HTTPError as exc:
            if exc.code == 429:
                GEMINI_QUOTA_BLOCKED = True
                print("Gemini 配额不足或请求过于频繁，停止本轮 AI 请求。")
                return None

            print("Gemini HTTP 错误：", exc.code)

        except (URLError, TimeoutError, OSError, ValueError) as exc:
            print("Gemini 请求失败：", exc)

        if attempt < RETRIES:
            time.sleep(2)

    return None


def parse_ai(text):
    if not text:
        return None

    try:
        data = json.loads(text)

        if not isinstance(data, dict):
            return None

        fields = [
            "analysis",
            "coreLogic",
            "marketImpact",
            "transmission",
            "impact",
            "asset",
            "timeHorizon",
            "scenarios",
            "evidence",
            "risk",
            "confidence",
        ]

        result = {}

        for field in fields:
            value = data.get(field, "")

            if isinstance(value, (list, dict)):
                value = json.dumps(
                    value,
                    ensure_ascii=False,
                )

            result[field] = clean(str(value)) if value else ""

        result["provider"] = "Gemini"
        return result

    except Exception:
        return None


def insight(item, cache, budget):
    """
    返回缓存分析，或在预算允许时请求 Gemini。
    """
    title = item.get("title", "")
    url = item.get("url", "")

    key = hashlib.sha256(
        (title + "|" + url).encode("utf-8")
    ).hexdigest()

    if key in cache and isinstance(cache[key], dict):
        return cache[key]

    if budget["attempts"] >= MAX_AI_CALLS:
        return None

    if not GEMINI_API_KEY or GEMINI_QUOTA_BLOCKED:
        return None

    prompt = f"""
你是一名谨慎、专业的中文财经研究分析师。

请根据下面提供的新闻标题和摘要进行分析。
不要把推测写成事实；信息不足时必须明确说明。
不要编造新闻细节、数据、引述或来源。
不要给出保证收益的投资承诺。

新闻标题：{title}
新闻摘要：{item.get("summary", "")}
媒体来源：{item.get("source", "")}
发布时间：{item.get("publishedAt", "")}

请只输出有效 JSON，不要使用 Markdown 代码围栏。
字段必须包括：
{{
  "analysis": "简明中文新闻解读",
  "coreLogic": "事件的核心逻辑",
  "marketImpact": "对市场可能产生的影响",
  "transmission": "影响如何传导至经济或市场",
  "impact": "影响程度及理由",
  "asset": "可能相关的资产类别",
  "timeHorizon": "短期、中期或长期",
  "scenarios": "可能情景及条件",
  "evidence": "已知事实与证据边界",
  "risk": "主要风险及不确定性",
  "confidence": "高、中或低"
}}

请使用简洁中文。若无法判断，请明确说明无法判断。
"""

    budget["attempts"] += 1

    response = call_gemini(prompt)
    parsed = parse_ai(response)

    if parsed:
        cache[key] = parsed
        budget["successes"] += 1
        return parsed

    budget["failures"] += 1
    return None


# =========================================================
# 十一、采集并构建新闻栏目
# =========================================================

def fetch_channel_items(channel):
    collected = []
    seen_feeds = set()

    for feed_url in FEEDS.get(channel, []):
        if feed_url in seen_feeds:
            continue

        seen_feeds.add(feed_url)

        try:
            body = get(feed_url, timeout=15)
            entries = parse_feed(body, feed_url)
            collected.extend(entries)
            print(
                f"[{channel}] 获取 {len(entries)} 条："
                f"{feed_url}"
            )

        except Exception as exc:
            print(
                f"[{channel}] 来源获取失败："
                f"{feed_url}；{exc}"
            )

    return collected


def build(channel, cache, budget):
    items = fetch_channel_items(channel)
    current = now_local()

    filtered = []

    for item in items:
        published = item.get("published")

        # 没有发布时间的条目不直接丢弃，但会降低可信度排序。
        if published:
            age = current - published

            if age > timedelta(days=MAX_NEWS_AGE_DAYS):
                continue

            if age < timedelta(days=-1):
                continue

        if not classify(item, channel):
            continue

        item["score"] = importance_score(item)
        filtered.append(item)

    filtered = deduplicate(filtered)

    filtered.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("published") or datetime.min.replace(tzinfo=TZ),
        ),
        reverse=True,
    )

    filtered = filtered[:LIMITS.get(channel, 10)]

    result = []

    for index, item in enumerate(filtered, start=1):
        original_title = clean(item.get("title", ""))
        original_summary = clean(item.get("summary", ""))

        title_cn = translate(original_title)
        summary_cn = translate(original_summary)

        published = item.get("published")

        if published:
            published_at = published.strftime("%Y-%m-%d %H:%M:%S")
            timestamp = int(published.timestamp() * 1000)
        else:
            published_at = ""
            timestamp = 0

        article = {
            "id": f"{channel.upper()}-{index:02d}",
            "rank": index,
            "importance": item.get("score", 1),
            "title": title_cn or original_title,
            "originalTitle": original_title,
            "summary": summary_cn or original_summary,
            "source": clean(item.get("source", "")) or "未知来源",
            "url": item.get("url", ""),
            "publishedAt": published_at,
            "timestamp": timestamp,
            "analysis": "",
            "coreLogic": "",
            "marketImpact": "",
            "transmission": "",
            "impact": "",
            "asset": "",
            "timeHorizon": "",
            "scenarios": "",
            "evidence": "",
            "risk": "",
            "confidence": "",
            "provider": "",
        }

        prepare_article_links(article)

        ai_data = insight(article, cache, budget)

        if ai_data:
            article.update(ai_data)

        result.append(article)

    return result


# =========================================================
# 十二、写入网页
# =========================================================

def main():
    if not PAGE.exists():
        raise FileNotFoundError(
            f"找不到 {PAGE.name}。"
            "请确保 update.py 与 index.html 位于同一目录。"
        )

    page_text = PAGE.read_text(encoding="utf-8")

    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    if start_marker not in page_text or end_marker not in page_text:
        raise RuntimeError(
            "index.html 缺少 NEWS_DATA_START 或 NEWS_DATA_END 标记。"
            "请检查你使用的 index.html 是否为对应版本。"
        )

    cache = load_cache()

    budget = {
        "attempts": 0,
        "successes": 0,
        "failures": 0,
    }

    news_data = {}

    for channel in ("global", "asia", "china", "earnings"):
        print(f"\n正在更新栏目：{channel}")

        try:
            news_data[channel] = build(
                channel,
                cache,
                budget,
            )

        except Exception as exc:
            print(f"栏目 {channel} 更新失败：{exc}")
            news_data[channel] = []

    updated_at = now_local().strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "updatedAt": updated_at,
        "aiEnabled": bool(GEMINI_API_KEY),
        "aiModel": GEMINI_MODEL,
        "aiStats": {
            "attempts": budget["attempts"],
            "successes": budget["successes"],
            "failures": budget["failures"],
            "maxPerRun": MAX_AI_CALLS,
        },
        "global": news_data.get("global", []),
        "asia": news_data.get("asia", []),
        "china": news_data.get("china", []),
        "earnings": news_data.get("earnings", []),
    }

    script = (
        start_marker
        + "\n<script>\n"
        + "window.newsData = "
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + ";\n</script>\n"
        + end_marker
    )

    updated_page = re.sub(
        re.escape(start_marker)
        + r".*?"
        + re.escape(end_marker),
        lambda _: script,
        page_text,
        count=1,
        flags=re.DOTALL,
    )

    # 更新时间显示元素（如果当前 index.html 中存在）
    updated_page = re.sub(
        r'(<span[^>]*id=["\']updateTime["\'][^>]*>).*?(</span>)',
        lambda match: (
            match.group(1)
            + updated_at
            + match.group(2)
        ),
        updated_page,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    PAGE.write_text(updated_page, encoding="utf-8")
    save_cache(cache)

    print("\n========== 更新完成 ==========")
    print("更新时间：", updated_at)
    print("国际财经：", len(payload["global"]), "条")
    print("亚太财经：", len(payload["asia"]), "条")
    print("国内财经：", len(payload["china"]), "条")
    print("公司财报：", len(payload["earnings"]), "条")
    print("AI 调用：", budget["attempts"], "/", MAX_AI_CALLS)
    print("AI 成功：", budget["successes"])
    print("AI 失败：", budget["failures"])
    print("网页文件：", PAGE)
    print("缓存文件：", CACHE)


if __name__ == "__main__":
    main()
