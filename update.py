# -*- coding: utf-8 -*-
"""
财经研究助手 3.1
修复：
1. 每次运行最多尝试 8 次 AI 分析
2. 新闻数据明确输出 importance，避免前端评分缺失
3. 扩展国内及亚太新闻分类
4. 不再将未解析的 Google News 链接误标为媒体原文
5. 检查 NEWS_DATA_START / NEWS_DATA_END 标记
6. 保留新闻缓存、语义去重、翻译、排序和四个栏目
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
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, parse_qs


# ============================================================
# 1. 基本配置
# ============================================================

BASE = Path(__file__).resolve().parent
PAGE = BASE / "index.html"
CACHE = BASE / "ai_cache.json"

TZ = timezone(timedelta(hours=8))

MAX_AI_CALLS = 8
RETRIES = 1
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
        "q": query,
        "hl": "zh-CN",
        "gl": "US",
        "ceid": "US:zh-Hans",
    })


# ============================================================
# 2. 新闻来源
# ============================================================

FEEDS = {
    "global": [
        ("Reuters", rss(
            "site:reuters.com finance economy markets central bank "
            "war sanctions tariffs energy"
        )),
        ("Bloomberg", rss(
            "site:bloomberg.com markets stocks economy central bank"
        )),
        ("The Wall Street Journal", rss(
            "site:wsj.com markets economy central bank trade"
        )),
        ("Financial Times", rss(
            "site:ft.com global economy finance markets"
        )),
        ("Associated Press", rss(
            "site:apnews.com business economy international"
        )),
        ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Fox News Business",
         "https://moxie.foxbusiness.com/google-publisher/latest.xml"),
    ],

    "asia": [
        ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
        ("The Japan Times", "https://www.japantimes.co.jp/feed/"),
        ("中央社 CNA", "https://feeds.feedburner.com/rsscna/finance"),
        ("Focus Taiwan", rss(
            "site:focustaiwan.tw Taiwan economy semiconductor stocks "
            "cross-strait politics"
        )),
        ("Taiwan semiconductor news", rss(
            "Taiwan TSMC semiconductor exports Taiwan dollar economy"
        )),
        ("Taiwan cross-strait news", rss(
            "Taiwan cross-strait relations US policy Taiwan Strait"
        )),
        ("South China Morning Post", rss(
            "site:scmp.com Hong Kong Asia economy business markets"
        )),
        ("Asia-Pacific markets", rss(
            "Asia Pacific markets economy central bank trade"
        )),
    ],

    "china": [
        ("中国财经与宏观", rss(
            '"China economy" OR "Chinese economy" OR GDP China '
            'OR PBOC OR yuan OR "China property" OR "China debt"'
        )),
        ("中国股市与金融", rss(
            '"China stocks" OR "Chinese stocks" OR "A-shares" '
            'OR "CSI 300" OR "China financial markets"'
        )),
        ("中国政策与贸易", rss(
            '"China policy" OR "China trade" OR "China tariffs" '
            'OR "China export controls" OR "China stimulus"'
        )),
        ("人民币与中国市场", rss(
            'yuan OR renminbi OR "China bond market" '
            'OR "China economic data"'
        )),
    ],

    "earnings": [
        ("CNBC Earnings",
         "https://www.cnbc.com/id/15839135/device/rss/rss.html"),
        ("MarketWatch",
         "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
        ("Financial Times Companies",
         "https://www.ft.com/companies?format=rss"),
        ("Company earnings", rss(
            "company earnings revenue profit outlook quarterly results"
        )),
    ],
}


# ============================================================
# 3. 通用工具
# ============================================================

def clean(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def is_http_url(url):
    try:
        parsed = urlparse(str(url or ""))
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
        )
    except Exception:
        return False


def is_google_news_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "news.google.com"
    except Exception:
        return False


def get_response(url, timeout=18, headers=None):
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml,"
            "application/rss+xml;q=0.9,*/*;q=0.8"
        ),
    }

    request_headers.update(headers or {})
    request = Request(url, headers=request_headers)

    with urlopen(request, timeout=timeout) as response:
        return response.read(), response.geturl()


def get(url, timeout=18, headers=None):
    body, _ = get_response(
        url,
        timeout=timeout,
        headers=headers
    )
    return body


def extract_article_url(url):
    """
    优先解析聚合链接中明确包含的媒体原文地址。
    如果无法解析，则保留原链接，不伪造原文地址。
    """
    if not url or not is_http_url(url):
        return ""

    if not is_google_news_url(url):
        return url

    try:
        query = parse_qs(urlparse(url).query)

        for key in ("url", "u", "target", "article_url"):
            for value in query.get(key, []):
                candidate = html.unescape(value.strip())

                if (
                    is_http_url(candidate)
                    and not is_google_news_url(candidate)
                ):
                    return candidate

    except Exception:
        pass

    return url


def prepare_article_links(item):
    original_url = (item.get("url") or "").strip()
    resolved = extract_article_url(original_url)

    item["url"] = resolved or original_url

    if not item["url"]:
        item["linkStatus"] = "无可用链接"
    elif is_google_news_url(item["url"]):
        item["linkStatus"] = "Google News 聚合链接"
    else:
        item["linkStatus"] = "媒体链接"

    return item


# ============================================================
# 4. RSS 采集
# ============================================================

def parse_feed(source, url):
    try:
        root = ET.fromstring(get(url, timeout=18))
    except Exception as error:
        print(f"[RSS失败] {source}: {type(error).__name__}")
        return []

    output = []

    entries = root.findall(".//item")

    # 同时兼容 Atom 格式的部分来源
    if not entries:
        for element in root.findall(".//{*}entry"):
            title = clean(element.findtext("{*}title"))

            link_element = element.find("{*}link")
            link = ""

            if link_element is not None:
                link = (
                    link_element.attrib.get("href", "")
                    or clean(link_element.text)
                )

            summary_element = (
                element.find("{*}summary")
                or element.find("{*}content")
            )

            summary = (
                clean(summary_element.text)
                if summary_element is not None
                else ""
            )

            published = (
                clean(element.findtext("{*}published"))
                or clean(element.findtext("{*}updated"))
            )

            if title and link:
                output.append({
                    "title": title,
                    "url": link,
                    "summary": summary,
                    "source": source,
                    "published": published,
                })

        return output

    for entry in entries:
        title = clean(entry.findtext("title"))
        link = clean(entry.findtext("link"))

        if not title or not link:
            continue

        source_element = entry.find("source")

        actual_source = (
            clean(source_element.text)
            if source_element is not None
            else source
        )

        output.append({
            "title": title,
            "url": link,
            "summary": clean(entry.findtext("description")),
            "source": actual_source or source,
            "published": clean(entry.findtext("pubDate")),
        })

    return output


def parse_date(value):
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


# ============================================================
# 5. 翻译与缓存
# ============================================================

def translate(text):
    text = clean(text)

    if not text or chinese(text):
        return text

    url = (
        "https://translate.googleapis.com/translate_a/single?"
        + urlencode({
            "client": "gtx",
            "sl": "auto",
            "tl": "zh-CN",
            "dt": "t",
            "q": text[:1600],
        })
    )

    try:
        result = json.loads(
            get(url, timeout=10).decode("utf-8")
        )

        translated = "".join(
            part[0]
            for part in result[0]
            if part and part[0]
        )

        return translated or text

    except Exception:
        return text


def load_cache():
    try:
        data = json.loads(
            CACHE.read_text(encoding="utf-8")
        )

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


# ============================================================
# 6. 语义去重
# ============================================================

def normalized_title(title):
    title = clean(title).lower()
    title = re.sub(r"\s*[-|—]\s*[^-|—]+$", "", title)
    return title.strip()


def deduplicate(items):
    output = []

    for item in items:
        title = normalized_title(item.get("title", ""))

        tokens = set(
            re.findall(r"[\w\u4e00-\u9fff]", title)
        )

        if len(tokens) < 3:
            output.append(item)
            continue

        duplicate = False

        for accepted in output:
            accepted_title = normalized_title(
                accepted.get("title", "")
            )

            accepted_tokens = set(
                re.findall(
                    r"[\w\u4e00-\u9fff]",
                    accepted_title
                )
            )

            if not accepted_tokens:
                continue

            intersection = tokens.intersection(accepted_tokens)
            union = tokens.union(accepted_tokens)

            similarity = (
                len(intersection) / len(union)
                if union else 0
            )

            if similarity > 0.38 or len(intersection) >= 7:
                duplicate = True
                break

        if not duplicate:
            output.append(item)

    return output


# ============================================================
# 7. 新闻分类
# ============================================================

CHINA_PATTERN = re.compile(
    r"中国|中国经济|中国股市|中国市场|中国央行|"
    r"人民币|A股|沪深|上证|深证|港股|"
    r"\bChina\b|\bChinese\b|\bPBOC\b|\bRenminbi\b|"
    r"\bYuan\b|\bA-shares?\b|\bCSI\s?300\b|"
    r"\bShanghai Composite\b|\bShenzhen\b|"
    r"\bChinese stocks\b|\bChina's economy\b",
    re.I
)

ASIA_PATTERN = re.compile(
    r"\bAsia\b|\bAsian\b|\bJapan\b|\bJapanese\b|"
    r"\bTaiwan\b|\bTaiwanese\b|\bHong Kong\b|"
    r"\bSouth Korea\b|\bKorean\b|\bSingapore\b|"
    r"\bASEAN\b|\bPacific\b|"
    r"亚太|日本|台湾|台灣|香港|台海|"
    r"韩国|南韩|新加坡|东南亚|半导体|台积电",
    re.I
)

EARNINGS_PATTERN = re.compile(
    r"\bearnings\b|\brevenue\b|\bprofit\b|"
    r"\bquarterly results\b|\bquarterly earnings\b|"
    r"\bfull-year results\b|\bfinancial results\b|"
    r"\bforecast\b|\boutlook\b|\bEPS\b|"
    r"财报|业绩|营收|利润|季度报告|盈利|亏损|"
    r"业绩指引|季度业绩",
    re.I
)


def classify(items, channel):
    output = []

    for item in items:
        text = (
            item.get("title", "")
            + " "
            + item.get("summary", "")
        )

        if channel == "china":
            matched = bool(CHINA_PATTERN.search(text))

        elif channel == "asia":
            matched = (
                bool(ASIA_PATTERN.search(text))
                and not bool(CHINA_PATTERN.search(text))
            )

        elif channel == "earnings":
            matched = bool(EARNINGS_PATTERN.search(text))

        else:
            matched = True

        if matched:
            output.append(item)

    return output


# ============================================================
# 8. 重要性评分
# ============================================================

IMPORTANCE_RULES = [
    (
        r"war|invasion|missile|military escalation|"
        r"ceasefire|armed conflict|"
        r"战争|入侵|导弹|军事冲突|停火|武装冲突|台海危机",
        35
    ),
    (
        r"sanction|tariff|trade war|export controls|embargo|"
        r"制裁|关税|贸易战|出口管制|禁运",
        25
    ),
    (
        r"emergency|financial crisis|bank failure|"
        r"capital controls|紧急措施|金融危机|银行倒闭|资本管制",
        30
    ),
    (
        r"central bank|interest rate|rate cut|rate hike|"
        r"Federal Reserve|央行|利率|降息|加息|美联储",
        20
    ),
    (
        r"GDP|inflation|CPI|unemployment|recession|"
        r"通胀|消费者物价|失业|经济衰退",
        15
    ),
    (
        r"oil prices|crude oil|natural gas|energy supply|"
        r"shipping lane|Strait of Hormuz|"
        r"原油|天然气|能源供应|航运通道|霍尔木兹",
        18
    ),
    (
        r"election|presidential election|government falls|"
        r"policy change|大选|总统选举|政府倒台|政策调整",
        12
    ),
    (
        r"earnings|revenue|profit|quarterly results|"
        r"财报|营收|利润|季度业绩",
        8
    ),
]


def importance_score(item):
    text = (
        item.get("title", "")
        + " "
        + item.get("summary", "")
    ).lower()

    # 基础分从 1 开始，避免所有普通新闻被赋予 10 分
    score = 1

    for pattern, points in IMPORTANCE_RULES:
        if re.search(pattern, text, re.I):
            score += points

    published = item.get("_date")

    if published:
        age_hours = max(
            0,
            (
                datetime.now(timezone.utc) - published
            ).total_seconds() / 3600
        )

        if age_hours < 6:
            score += 15
        elif age_hours < 24:
            score += 10
        elif age_hours < 48:
            score += 5

    # 网页展示使用 1—10 分
    return max(1, min(10, round(score / 10)))


# ============================================================
# 9. Gemini AI 接口
# ============================================================

def gemini(prompt):
    global GEMINI_QUOTA_BLOCKED

    if GEMINI_QUOTA_BLOCKED:
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        return None

    # 保留原有模型设置，避免未经确认擅自更换模型
    model = "gemini-1.5-flash"

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )

    body = json.dumps({
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }).encode("utf-8")

    for attempt in range(RETRIES + 1):
        request = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
                "User-Agent": "FinanceResearchDashboard/3.1",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=AI_TIMEOUT) as response:
                data = json.loads(
                    response.read().decode("utf-8")
                )

            candidates = data.get("candidates", [])

            parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
                if candidates else []
            )

            result = "".join(
                part.get("text", "")
                for part in parts
                if part.get("text")
            ).strip()

            if result:
                return result

        except HTTPError as error:
            if error.code == 429:
                GEMINI_QUOTA_BLOCKED = True
                print("[AI] 已触发 429 限额，本次停止继续调用。")
                return None

            print(f"[AI] HTTP 错误：{error.code}")

        except Exception as error:
            print(f"[AI] 请求失败：{type(error).__name__}")

        if attempt < RETRIES:
            time.sleep(2)

    return None


def parse_ai(raw):
    if not raw:
        return None

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        raw.strip(),
        flags=re.I
    )

    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)

    except Exception:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            return None

        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    def value(key, limit=600):
        result = data.get(key, "")

        if isinstance(result, list):
            result = "；".join(clean(x) for x in result)

        return clean(result)[:limit]

    def array(key, limit=3):
        items = data.get(key, [])

        if not isinstance(items, list):
            return []

        return [
            clean(item)[:250]
            for item in items
            if clean(item)
        ][:limit]

    assets = data.get("assets", [])

    if not isinstance(assets, list):
        assets = []

    return {
        "analysis": value("analysis", 1600),
        "coreLogic": value("core_logic"),
        "marketImpact": value("market_impact"),
        "transmission": value("transmission"),
        "impact": value("impact", 180),
        "asset": [
            clean(item)[:80]
            for item in assets
            if clean(item)
        ][:4],
        "timeHorizon": value("time_horizon", 150),
        "scenarios": array("scenarios"),
        "evidence": array("evidence"),
        "risk": value("risk"),
        "confidence": value("confidence", 100),
        "provider": "Gemini",
    }


# ============================================================
# 10. AI 分析与缓存
# ============================================================

def insight(item, cache, budget):
    cache_key = hashlib.sha256(
        (
            item.get("title", "")
            + "|"
            + item.get("url", "")
        ).encode("utf-8")
    ).hexdigest()

    old = cache.get(cache_key)

    if (
        isinstance(old, dict)
        and old.get("analysis")
        and old.get("coreLogic")
    ):
        return old

    if budget["attempts"] >= MAX_AI_CALLS:
        return None

    prompt = f"""
你是一位审慎的财经研究助理，请使用简体中文分析新闻。

只能依据输入的标题、来源和摘要，不得编造事实、
价格、财报数字或事件细节。无法确定的内容应明确说明。

只输出合法 JSON，不要输出 Markdown。

字段：
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
confidence：分析信心等级。

标题：{item.get('title', '')}
原文标题：{item.get('originalTitle', '')}
来源：{item.get('source', '')}
摘要：{item.get('summary', '')[:1200]}
"""

    budget["attempts"] += 1

    # 控制调用频率，降低短时间内连续请求的概率
    time.sleep(2)

    ai = parse_ai(gemini(prompt))

    if ai and ai.get("analysis") and ai.get("coreLogic"):
        cache[cache_key] = ai
        budget["successes"] += 1
        return ai

    budget["failures"] += 1
    return None


# ============================================================
# 11. 生成各栏目新闻
# ============================================================

def build(channel, cache, budget):
    gathered = []

    for source, feed_url in FEEDS[channel]:
        for item in parse_feed(source, feed_url):
            item["_date"] = parse_date(item.get("published"))

            if item["_date"] is None:
                continue

            age = (
                datetime.now(timezone.utc) - item["_date"]
            ).total_seconds()

            # 不收录未来日期或超过保留期的新闻
            if 0 <= age < MAX_NEWS_AGE_DAYS * 86400:
                gathered.append(item)

    gathered = classify(gathered, channel)
    gathered = deduplicate(gathered)

    for item in gathered:
        item["_importance"] = importance_score(item)

    gathered.sort(
        key=lambda item: (
            item.get("_importance", 1),
            item["_date"].timestamp(),
        ),
        reverse=True,
    )

    output = []

    for item in gathered:
        if len(output) >= LIMITS[channel]:
            break

        original_title = item.get("title", "")
        original_summary = item.get("summary", "")

        translated_title = translate(original_title) or original_title
        translated_summary = (
            translate(original_summary) or original_summary
        )

        news_item = {
            "title": translated_title,
            "originalTitle": original_title,
            "summary": translated_summary[:900],
            "source": item.get("source", "未知来源"),
            "url": item.get("url", ""),
            "publishedAt": item["_date"].astimezone(TZ).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "timestamp": int(item["_date"].timestamp()),

            # 关键修复：明确输出网页所需的评分字段
            "importance": item.get("_importance", 1),
            "score": item.get("_importance", 1),

            "impact": "待分析",
            "asset": [],
            "analysis": "",
            "risk": "",
            "coreLogic": "",
            "marketImpact": "",
            "transmission": "",
            "timeHorizon": "",
            "scenarios": [],
            "evidence": [],
            "confidence": "",
            "aiStatus": "未分析",
        }

        prepare_article_links(news_item)

        ai = insight(news_item, cache, budget)

        if ai:
            news_item.update(ai)
            news_item["impact"] = (
                ai.get("impact")
                or ai.get("marketImpact")
                or "见分层分析"
            )
            news_item["aiStatus"] = "Gemini AI"

        output.append(news_item)

    # 第一条编号最大，最后一条编号为 01
    for index, item in enumerate(output):
        item["id"] = f"NO.{len(output) - index:02d}"

    print(f"[栏目完成] {channel}: {len(output)} 条新闻")

    return output


# ============================================================
# 12. 主程序：写入 index.html
# ============================================================

def main():
    if not PAGE.exists():
        raise RuntimeError(
            "找不到 index.html。请确认 update.py 与 index.html 位于同一目录。"
        )

    page = PAGE.read_text(encoding="utf-8")

    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    start_position = page.find(start_marker)
    end_position = page.find(end_marker)

    if (
        start_position < 0
        or end_position < 0
        or end_position <= start_position
    ):
        raise RuntimeError(
            "index.html 缺少有效的 NEWS_DATA_START / "
            "NEWS_DATA_END 标记。请使用兼容的数据区域版本。"
        )

    cache = load_cache()

    budget = {
        "attempts": 0,
        "successes": 0,
        "failures": 0,
    }

    news = {}

    for channel in ("global", "asia", "china", "earnings"):
        news[channel] = build(channel, cache, budget)

    news["updatedAt"] = datetime.now(TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    news["aiEnabled"] = bool(
        os.getenv("GEMINI_API_KEY", "").strip()
    )

    news["aiModel"] = "gemini-1.5-flash"

    news["aiStats"] = {
        **budget,
        "maxPerRun": MAX_AI_CALLS,
    }

    data_block = (
        "\n<script>window.newsData = "
        + json.dumps(
            news,
            ensure_ascii=False
        ).replace("</", "<\\/")
        + ";</script>\n"
    )

    # 仅替换数据标记之间的内容，不改动网页其他布局
    content_start = start_position + len(start_marker)

    page = (
        page[:content_start]
        + data_block
        + page[end_position:]
    )

    # 兼容页面中已有的更新时间元素
    page = re.sub(
        r"""(<[^>]*id=["']updateTime["'][^>]*>).*?(</[^>]+>)""",
        lambda match: (
            match.group(1)
            + html.escape(news["updatedAt"])
            + match.group(2)
        ),
        page,
        flags=re.I | re.S,
    )

    PAGE.write_text(page, encoding="utf-8")

    CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 45)
    print("财经新闻更新完成")
    print(f"更新时间：{news['updatedAt']}")
    print(f"国际财经：{len(news['global'])} 条")
    print(f"亚太财经：{len(news['asia'])} 条")
    print(f"国内财经：{len(news['china'])} 条")
    print(f"公司财报：{len(news['earnings'])} 条")
    print(f"AI 调用：{budget['attempts']} 次")
    print(f"AI 成功：{budget['successes']} 次")
    print(f"AI 失败：{budget['failures']} 次")
    print("=" * 45)


if __name__ == "__main__":
    main()
