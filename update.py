# -*- coding: utf-8 -*-
"""
财经研究助手 2.2
新闻采集、时效过滤、重要性排序、中文翻译、链接处理和 Gemini 分析。
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

MAX_AI_CALLS = 8
RETRIES = 1
AI_TIMEOUT = 18
MAX_NEWS_AGE_DAYS = 5

GEMINI_QUOTA_BLOCKED = False

LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}


# ============================================================
# 2. Google News RSS 搜索
# ============================================================

def rss(query):
    """生成 Google News RSS 搜索地址。"""
    return "https://news.google.com/rss/search?" + urlencode({
        "q": query,
        "hl": "zh-CN",
        "gl": "US",
        "ceid": "US:zh-Hans",
    })


# ============================================================
# 3. 新闻来源
# ============================================================

FEEDS = {
    # --------------------------------------------------------
    # 国际财经、全球宏观、冲突与突发政策
    # --------------------------------------------------------
    "global": [
        (
            "CNBC World",
            "https://www.cnbc.com/id/100727362/device/rss/rss.html",
        ),
        (
            "CNBC Finance",
            "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        ),
        (
            "BBC World",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        ),
        (
            "Al Jazeera",
            "https://www.aljazeera.com/xml/rss/all.xml",
        ),
        (
            "美国联邦储备委员会",
            "https://www.federalreserve.gov/feeds/press_all.xml",
        ),
        (
            "欧洲央行",
            "https://www.ecb.europa.eu/rss/press.html",
        ),
        (
            "Google News 国际财经",
            rss(
                "global finance economy stock market inflation "
                "central bank interest rates GDP recession"
            ),
        ),
        (
            "Google News 战争与冲突",
            rss(
                "war conflict military escalation ceasefire "
                "geopolitical crisis international security"
            ),
        ),
        (
            "Google News 制裁与贸易战",
            rss(
                "sanctions tariffs trade war export controls "
                "trade restrictions economic retaliation"
            ),
        ),
        (
            "Google News 能源与战略航道",
            rss(
                "oil supply disruption Strait of Hormuz "
                "Red Sea shipping energy crisis LNG"
            ),
        ),
        (
            "Google News 紧急经济政策",
            rss(
                "emergency central bank action financial stability "
                "capital controls currency intervention"
            ),
        ),
        (
            "Google News 美国政策与全球市场",
            rss(
                "United States government policy markets "
                "election fiscal policy trade restrictions"
            ),
        ),
        (
            "Google News 银行与金融风险",
            rss(
                "banking crisis financial stress credit default "
                "liquidity crisis sovereign debt"
            ),
        ),
    ],

    # --------------------------------------------------------
    # 亚太财经：明确覆盖台湾政治、两岸、半导体与金融
    # --------------------------------------------------------
    "asia": [
        (
            "中央社财经",
            "https://feeds.feedburner.com/rsscna/finance",
        ),
        (
            "中央社台湾新闻",
            "https://feeds.feedburner.com/rsscna/politics",
        ),
        (
            "Nikkei Asia",
            "https://asia.nikkei.com/rss/feed/nar",
        ),
        (
            "The Japan Times",
            "https://www.japantimes.co.jp/feed/",
        ),
        (
            "Google News 亚太财经",
            rss(
                "Asia Pacific finance economy markets "
                "China Japan South Korea Taiwan Singapore India"
            ),
        ),
        (
            "Google News 台湾政治与政策",
            rss(
                "Taiwan politics election government policy "
                "Taiwan parliament political party"
            ),
        ),
        (
            "Google News 两岸关系与台海",
            rss(
                "Taiwan Strait cross-strait relations "
                "Taiwan China military diplomatic tensions"
            ),
        ),
        (
            "Google News 台湾经济与金融",
            rss(
                "Taiwan economy stock market Taiwan dollar "
                "exports inflation central bank"
            ),
        ),
        (
            "Google News 台湾半导体",
            rss(
                "Taiwan semiconductor TSMC chip industry "
                "advanced manufacturing export controls"
            ),
        ),
        (
            "Google News 美国政策与台湾",
            rss(
                "United States Taiwan policy semiconductor "
                "tariffs export controls trade relations"
            ),
        ),
        (
            "Google News 亚洲央行与汇率",
            rss(
                "Asia central banks interest rates currency "
                "foreign exchange intervention monetary policy"
            ),
        ),
        (
            "Google News 亚洲贸易与供应链",
            rss(
                "Asia trade supply chains shipping "
                "export restrictions manufacturing"
            ),
        ),
    ],

    # --------------------------------------------------------
    # 国内财经：中国经济、A股、人民币、政策与金融市场
    # --------------------------------------------------------
    "china": [
        (
            "Google News 中国财经",
            rss(
                "中国财经 OR 中国经济 OR 人民币 "
                "OR 中国央行 OR 港股"
            ),
        ),
        (
            "Google News 中国股市",
            rss(
                "A股 OR 沪深股市 OR 中国上市公司 "
                "OR 中国金融市场"
            ),
        ),
        (
            "Google News 中国宏观政策",
            rss(
                "中国财政政策 OR 中国货币政策 "
                "OR 中国经济刺激 OR 中国经济数据"
            ),
        ),
        (
            "Google News 中国房地产与债务",
            rss(
                "中国房地产 OR 中国地方债务 "
                "OR 中国债券市场 OR 房地产风险"
            ),
        ),
        (
            "Google News 中国贸易与产业",
            rss(
                "中国出口 OR 中国贸易 OR 中国制造业 "
                "OR 中国半导体 OR 中国科技产业"
            ),
        ),
        (
            "Google News 香港金融市场",
            rss(
                "香港股市 OR 港股 OR 香港经济 "
                "OR 香港金融市场 OR 恒生指数"
            ),
        ),
    ],

    # --------------------------------------------------------
    # 企业财报
    # --------------------------------------------------------
    "earnings": [
        (
            "CNBC Finance",
            "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        ),
        (
            "CNBC Earnings",
            "https://www.cnbc.com/id/15839135/device/rss/rss.html",
        ),
        (
            "MarketWatch",
            "https://feeds.marketwatch.com/marketwatch/marketpulse/",
        ),
        (
            "Google News 企业财报",
            rss(
                "company earnings revenue profit "
                "quarterly results forecast guidance"
            ),
        ),
        (
            "Google News 科技公司财报",
            rss(
                "technology company earnings cloud AI "
                "semiconductor quarterly results"
            ),
        ),
        (
            "Google News 银行与金融公司财报",
            rss(
                "bank earnings net interest income "
                "financial company quarterly results"
            ),
        ),
    ],
}


# ============================================================
# 4. 通用工具
# ============================================================

def clean(value):
    """去除 HTML 标签、实体和多余空白。"""
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def chinese(text):
    """判断文本是否包含中文。"""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def is_http_url(url):
    """只接受 HTTP 或 HTTPS 地址。"""
    try:
        parsed = urlparse(str(url or ""))
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
        )
    except Exception:
        return False


def is_google_news_url(url):
    """判断是否为 Google News 中转链接。"""
    try:
        host = (urlparse(url).hostname or "").lower()
        return (
            host == "news.google.com"
            or host.endswith(".news.google.com")
        )
    except Exception:
        return False


def get_response(url, timeout=18, headers=None):
    """请求网络资源，并返回正文及最终跳转地址。"""
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
    """获取网络响应正文。"""
    body, _ = get_response(
        url,
        timeout=timeout,
        headers=headers,
    )
    return body


def make_search_url(title):
    """生成备用搜索入口，不将搜索页伪装成新闻原文。"""
    title = clean(title)

    if not title:
        return ""

    query = quote(title)

    if chinese(title):
        return "https://www.baidu.com/s?wd=" + query

    return "https://www.bing.com/search?q=" + query


# ============================================================
# 5. 新闻链接处理
# ============================================================

def extract_article_url(url):
    """
    尝试解析 Google News 中转链接。

    如果不能可靠解析，就返回空字符串，
    由调用方使用备用搜索入口。
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

    headers = {
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Cookie": "CONSENT=YES+cb; SOCS=CAI",
    }

    try:
        body, final_url = get_response(
            url,
            timeout=8,
            headers=headers,
        )

        if (
            is_http_url(final_url)
            and not is_google_news_url(final_url)
        ):
            return final_url

        page = body[:700000].decode(
            "utf-8",
            errors="replace",
        )

        patterns = [
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
            r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
        ]

        for pattern in patterns:
            match = re.search(pattern, page, flags=re.I)

            if not match:
                continue

            candidate = html.unescape(match.group(1)).strip()

            if (
                is_http_url(candidate)
                and not is_google_news_url(candidate)
            ):
                return candidate

    except Exception as error:
        print(
            "[INFO] Google News 原文解析未成功：",
            type(error).__name__,
        )

    return ""


def prepare_article_links(item):
    """为新闻保留原始链接，并生成备用搜索入口。"""
    original_url = (item.get("url") or "").strip()
    title = item.get("title", "")

    item["originalUrl"] = original_url
    item["searchUrl"] = make_search_url(title)

    if not is_http_url(original_url):
        item["url"] = item["searchUrl"]
        item["linkStatus"] = "备用搜索入口"
        return item

    if not is_google_news_url(original_url):
        item["url"] = original_url
        item["linkStatus"] = "媒体链接"
        return item

    resolved = extract_article_url(original_url)

    if resolved and not is_google_news_url(resolved):
        item["url"] = resolved
        item["linkStatus"] = "已解析原文链接"
    else:
        item["url"] = original_url
        item["linkStatus"] = "Google News 中转链接"

    return item


# ============================================================
# 6. RSS / Atom 解析
# ============================================================

def parse_feed(source, url):
    """解析 RSS 或 Atom 新闻源。"""
    try:
        root = ET.fromstring(get(url, timeout=18))

    except Exception as error:
        print(
            f"[WARN] RSS {source} 获取失败："
            f"{type(error).__name__}: {error}"
        )
        return []

    output = []

    # RSS
    for entry in root.findall(".//item"):
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

    # Atom
    if not output:
        ns = {"a": "http://www.w3.org/2005/Atom"}

        for entry in root.findall(".//a:entry", ns):
            title = clean(
                entry.findtext("a:title", default="", namespaces=ns)
            )

            link_element = entry.find("a:link", ns)
            link = (
                link_element.get("href", "")
                if link_element is not None
                else ""
            )

            summary = clean(
                entry.findtext("a:summary", default="", namespaces=ns)
                or entry.findtext("a:content", default="", namespaces=ns)
            )

            published = clean(
                entry.findtext("a:published", default="", namespaces=ns)
                or entry.findtext("a:updated", default="", namespaces=ns)
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


# ============================================================
# 7. 时间处理
# ============================================================

def parse_date(value):
    """将发布时间统一转换为 UTC；无法识别时返回 None。"""
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
# 8. 中文翻译
# ============================================================

def translate(text):
    """使用 Google 翻译接口尝试翻译，失败时保留原文。"""
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

    except Exception as error:
        print(
            "[WARN] 翻译失败，保留原文：",
            type(error).__name__,
        )
        return text


# ============================================================
# 9. AI 缓存
# ============================================================

def load_cache():
    """读取 AI 分析缓存。"""
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ============================================================
# 10. Gemini 接口
# ============================================================

def gemini(prompt):
    """调用 Gemini；遇到配额限制时停止本次后续调用。"""
    global GEMINI_QUOTA_BLOCKED

    if GEMINI_QUOTA_BLOCKED:
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        print("[INFO] 未配置 GEMINI_API_KEY，跳过 AI 分析。")
        return None

    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + quote(model, safe="-._")
        + ":generateContent"
    )

    body = json.dumps({
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
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
                "User-Agent": "FinanceResearchDashboard/2.2",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=AI_TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))

            candidates = data.get("candidates", [])
            parts = (
                candidates[0].get("content", {}).get("parts", [])
                if candidates
                else []
            )

            result = "".join(
                part.get("text", "")
                for part in parts
                if part.get("text")
            ).strip()

            if result:
                return result

            print("[WARN] Gemini 返回空内容。")

        except HTTPError as error:
            detail = error.read().decode(
                "utf-8",
                errors="replace",
            )[:400]

            print(f"[ERROR] Gemini HTTP {error.code}: {detail}")

            if error.code == 429:
                GEMINI_QUOTA_BLOCKED = True
                print("[INFO] Gemini 触发限流，停止本次后续调用。")
                return None

            if error.code not in (408, 500, 502, 503, 504):
                return None

        except (TimeoutError, URLError) as error:
            print(
                "[WARN] Gemini 网络请求失败：",
                type(error).__name__,
            )

        except Exception as error:
            print(
                "[WARN] Gemini 调用异常：",
                type(error).__name__,
            )
            return None

        if attempt < RETRIES:
            time.sleep(2)

    return None


# ============================================================
# 11. 解析 Gemini 返回结果
# ============================================================

def parse_ai(raw):
    """将 AI 输出解析为网页需要的字段。"""
    if not raw:
        return None

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        raw.strip(),
        flags=re.I,
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
        return clean(data.get(key, ""))[:limit]

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
# 12. 新闻重要性初筛
# ============================================================

IMPORTANCE_RULES = [
    (
        r"war|invasion|missile|military escalation|"
        r"ceasefire|armed conflict|战争|入侵|导弹|"
        r"军事冲突|停火|武装冲突|台海危机",
        30,
        "战争与安全事件",
    ),
    (
        r"sanction|tariff|trade war|export controls|"
        r"embargo|制裁|关税|贸易战|出口管制|禁运",
        24,
        "制裁与贸易政策",
    ),
    (
        r"emergency|financial crisis|bank failure|"
        r"capital controls|紧急措施|金融危机|银行倒闭|"
        r"资本管制",
        26,
        "紧急政策与金融风险",
    ),
    (
        r"central bank|interest rate|rate cut|rate hike|"
        r"Federal Reserve|央行|利率|降息|加息|美联储",
        20,
        "央行与利率政策",
    ),
    (
        r"oil supply|Strait of Hormuz|Red Sea shipping|"
        r"energy crisis|石油供应|霍尔木兹海峡|"
        r"红海航运|能源危机",
        22,
        "能源供应与战略航道",
    ),
    (
        r"election|government collapse|snap election|"
        r"major policy shift|选举|政府倒台|提前选举|"
        r"重大政策变化",
        15,
        "重大政治与政策变化",
    ),
    (
        r"GDP|inflation|CPI|unemployment|recession|"
        r"GDP|通胀|消费者物价|失业|经济衰退",
        12,
        "宏观经济数据",
    ),
    (
        r"earnings|revenue|profit|guidance|财报|营收|"
        r"利润|业绩指引",
        10,
        "企业业绩",
    ),
]


def importance_score(item):
    """基于标题和摘要做启发式初筛，不代表市场预测。"""
    text = (
        item.get("title", "")
        + " "
        + item.get("summary", "")
    ).lower()

    score = 10
    reasons = []

    for pattern, points, label in IMPORTANCE_RULES:
        if re.search(pattern, text, re.I):
            score += points
            reasons.append(label)

    published = item.get("_date")
    if published:
        age_hours = max(
            0,
            (datetime.now(timezone.utc) - published).total_seconds()
            / 3600,
        )

        if age_hours < 6:
            score += 15
        elif age_hours < 24:
            score += 10
        elif age_hours < 48:
            score += 5

    return min(score, 100), reasons[:3]


# ============================================================
# 13. 栏目分类
# ============================================================

ASIA_PATTERN = re.compile(
    r"\bAsia\b|\bAsian\b|\bAsia-Pacific\b|"
    r"\bChina\b|\bChinese\b|\bJapan\b|\bJapanese\b|"
    r"\bKorea\b|\bKorean\b|\bTaiwan\b|\bTaiwanese\b|"
    r"\bHong Kong\b|\bSingapore\b|\bIndia\b|\bIndian\b|"
    r"\bAustralia\b|\bASEAN\b|\bIndonesia\b|\bVietnam\b|"
    r"\bThailand\b|\bMalaysia\b|\bPhilippines\b|"
    r"亚太|中国|日本|韩国|台湾|台灣|香港|新加坡|"
    r"印度|澳大利亚|澳洲|东盟|印尼|印度尼西亚|"
    r"越南|泰国|马来西亚|菲律宾|两岸|兩岸|台海|"
    r"台湾海峡|台灣海峽|跨海峡|跨台海|台湾总统|"
    r"台灣總統|台湾选举|台灣選舉|台湾政府|台灣政府|"
    r"台湾立法院|台灣立法院|台湾政策|台灣政策|"
    r"台湾股市|台灣股市|台湾央行|台灣央行|"
    r"台湾经济|台灣經濟|台湾出口|台灣出口|"
    r"台湾半导体|台灣半導體|台积电|台積電",
    re.I,
)

CHINA_PATTERN = re.compile(
    r"中国|人民币|A股|沪深|港股|央行|中国经济|"
    r"\bChina\b|\bChinese\b|\byuan\b|Shanghai|Shenzhen|"
    r"\bPBOC\b|CSI 300|Hong Kong stocks|"
    r"中国房地产|中国债务|中国出口|中国财政政策|中国货币政策",
    re.I,
)

EARNINGS_PATTERN = re.compile(
    r"\bearnings\b|\brevenue\b|\bprofit\b|"
    r"\bquarter(?:ly)?\b|\bforecast\b|\bguidance\b|"
    r"\bresults\b|\bnet income\b|\boperating margin\b|"
    r"财报|业绩|营收|利润|季度|盈利|业绩指引|净利润",
    re.I,
)


def classify(items, channel):
    """按栏目关键词筛选新闻。"""
    pattern = {
        "asia": ASIA_PATTERN,
        "china": CHINA_PATTERN,
        "earnings": EARNINGS_PATTERN,
    }.get(channel)

    if pattern is None:
        return items

    output = []

    for item in items:
        text = item.get("title", "") + " " + item.get("summary", "")

        if pattern.search(text):
            output.append(item)

    return output


# ============================================================
# 14. 去重
# ============================================================

def normalize_title(title):
    """去除标题中的标点和空白，辅助判断重复新闻。"""
    title = clean(title).lower()
    return re.sub(r"[\W_]+", "", title, flags=re.UNICODE)


def deduplicate(items):
    """
    按标题和链接去重。
    相同事件若标题写法略有不同，仍可能重复；
    后续版本可再加强事件级去重。
    """
    output = []
    seen_titles = set()
    seen_links = set()

    for item in items:
        title_key = normalize_title(item.get("title", ""))
        link_key = (item.get("url") or "").strip().rstrip("/").lower()

        if not title_key:
            continue

        if title_key in seen_titles:
            continue

        if link_key and link_key in seen_links:
            continue

        seen_titles.add(title_key)

        if link_key:
            seen_links.add(link_key)

        output.append(item)

    return output


# ============================================================
# 15. Gemini 分析与缓存
# ============================================================

def insight(item, cache, budget):
    """优先使用缓存；每次运行限制 AI 请求数量。"""
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

    special = ""

    if item.get("channel") == "earnings":
        special = (
            "特别关注营收、利润、利润率、现金流、"
            "业绩指引及市场预期之间的差异。"
        )

    prompt = f"""
你是审慎的财经研究助理，使用简体中文。

只能依据输入的标题、来源和摘要。
不得编造事实、数字、估值或市场预期。
材料不足时，明确指出需要核实原文。

{special}

请只输出合法 JSON，不要 Markdown。

字段要求：
analysis：100—180 字新闻解读。
core_logic：事件→变量变化→影响对象的逻辑链。
market_impact：潜在受益或承压方向及不确定性。
transmission：利率、汇率、成本、利润、供需、
融资条件或风险偏好等传导机制。
impact：一句话概括潜在市场影响。
assets：最多 4 个相关资产、指数、行业或 ETF 类别。
time_horizon：短期、中期、长期影响及不确定性。
scenarios：最多 3 条情景及触发条件。
evidence：最多 3 条证据线索，注明“摘要提供，未独立核实”。
risk：反证、混杂因素、滞后效应和信息缺口。
confidence：低、中、高的信心等级及理由。

必须区分输入直接支持的事实、推断和待核实信息。
不得声称已独立核实新闻原文或数据。
不得提供个性化投资建议。

标题：{item.get('title', '')}
来源：{item.get('source', '')}
摘要：{item.get('summary', '')[:1200]}
"""

    budget["attempts"] += 1

    ai = parse_ai(gemini(prompt))

    if ai and ai.get("analysis") and ai.get("coreLogic"):
        cache[cache_key] = ai
        budget["successes"] += 1
        return ai

    budget["failures"] += 1
    return None


# ============================================================
# 16. 采集和整理单个栏目
# ============================================================

def build(channel, cache, budget):
    """采集新闻、过滤时效、去重、翻译、排序并分析。"""
    gathered = []

    for source, feed_url in FEEDS[channel]:
        entries = parse_feed(source, feed_url)

        for item in entries:
            item["_date"] = parse_date(item.get("published"))

            if item["_date"] is None:
                continue

            age = (
                datetime.now(timezone.utc) - item["_date"]
            ).total_seconds()

            # 过滤未来时间和超过设定期限的新闻。
            if age < 0 or age >= MAX_NEWS_AGE_DAYS * 86400:
                continue

            gathered.append(item)

    gathered = classify(gathered, channel)
    gathered = deduplicate(gathered)

    # 初步计算重要性；优先展示高重要性，再按发布时间排序。
    for item in gathered:
        score, reasons = importance_score(item)
        item["_importance"] = score
        item["_importanceReasons"] = reasons

    gathered.sort(
        key=lambda item: (
            item.get("_importance", 0),
            item["_date"].timestamp() if item.get("_date") else 0,
        ),
        reverse=True,
    )

    output = []

    for item in gathered:
        if len(output) >= LIMITS[channel]:
            break

        original_title = item.get("title", "")
        original_summary = item.get("summary", "")

        title = translate(original_title)
        summary = translate(original_summary)

        published = item["_date"].astimezone(TZ).strftime(
            "%Y-%m-%d %H:%M"
        )

        news_item = {
            "title": title or original_title,
            "originalTitle": original_title,
            "summary": (summary or original_summary)[:900],
            "source": item.get("source", "新闻来源"),
            "url": item.get("url", ""),
            "publishedAt": published,
            "timestamp": int(item["_date"].timestamp()),
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
            "channel": channel,
            "importance": item.get("_importance", 0),
            "importanceReasons": item.get("_importanceReasons", []),
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

        news_item.pop("channel", None)
        output.append(news_item)

    total = len(output)

    for index, item in enumerate(output):
        item["id"] = f"NO.{total - index:02d}"

    return output


# ============================================================
# 17. 写入网页
# ============================================================

def main():
    """更新 index.html 中的新闻数据。"""
    if not PAGE.exists():
        raise FileNotFoundError(
            "找不到 index.html。请确认两个文件在同一目录。"
        )

    page = PAGE.read_text(encoding="utf-8")

    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    if start_marker not in page or end_marker not in page:
        raise RuntimeError(
            "index.html 缺少 NEWS_DATA_START / NEWS_DATA_END 标记。"
            "请确认使用的是对应版本。"
        )

    cache = load_cache()

    budget = {
        "attempts": 0,
        "successes": 0,
        "failures": 0,
    }

    news = {}

    for channel in ("global", "asia", "china", "earnings"):
        print(f"\n===== 更新栏目：{channel} =====")

        news[channel] = build(
            channel,
            cache,
            budget,
        )

    news["updatedAt"] = datetime.now(TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    news["aiEnabled"] = bool(
        os.getenv("GEMINI_API_KEY", "").strip()
    )

    news["aiModel"] = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.8-flash",
    ).strip()

    news["aiStats"] = {
        **budget,
        "maxPerRun": MAX_AI_CALLS,
    }

    start = page.index(start_marker) + len(start_marker)
    end = page.index(end_marker, start)

    data_block = (
        "\n<script>window.newsData = "
        + json.dumps(
            news,
            ensure_ascii=False,
        ).replace("</", "<\\/")
        + ";</script>\n"
    )

    page = page[:start] + data_block + page[end:]

    # 更新页面上的最后更新时间。
    page = re.sub(
        r"""(<span[^>]*id=["']updateTime["'][^>]*>).*?(</span>)""",
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
        json.dumps(
            cache,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n===== 更新完成 =====")
    print("北京时间：", news["updatedAt"])
    print("国际财经新闻：", len(news["global"]), "条")
    print("亚太财经新闻：", len(news["asia"]), "条")
    print("国内财经新闻：", len(news["china"]), "条")
    print("企业财报新闻：", len(news["earnings"]), "条")
    print(
        "AI 尝试/成功/失败：",
        budget["attempts"],
        budget["successes"],
        budget["failures"],
    )

    if GEMINI_QUOTA_BLOCKED:
        print(
            "提示：Gemini 本次触发限流；"
            "新闻采集继续，但部分新闻没有 AI 分析。"
        )

    print(
        "提示：无法解析 Google News 原文时，"
        "会保留中转链接并提供备用搜索入口。"
    )

    print(
        "注意：重要性排序是关键词和时效性初筛，"
        "不是经过验证的市场预测。"
    )

    print(
        "注意：本脚本按现有定时任务更新，"
        "不保证突发事件实时推送。"
    )


if __name__ == "__main__":
    main()
