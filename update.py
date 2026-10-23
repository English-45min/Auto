# -*- coding: utf-8 -*-
"""财经研究助手 2.1：新闻采集、链接解析与 AI 分析。"""

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

MAX_AI_CALLS = 3
RETRIES = 1
AI_TIMEOUT = 18

GEMINI_QUOTA_BLOCKED = False

LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}


# ============================================================
# 2. 新闻源
# ============================================================

def rss(query):
    """生成 Google News RSS 搜索地址。"""
    return "https://news.google.com/rss/search?" + urlencode({
        "q": query,
        "hl": "zh-CN",
        "gl": "US",
        "ceid": "US:zh-Hans",
    })


FEEDS = {
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
            "Google News 国际财经",
            rss("global finance economy markets stocks"),
        ),
    ],

    "asia": [
        (
            "中央社财经",
            "https://feeds.feedburner.com/rsscna/finance",
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
                "China Japan Korea Taiwan Singapore"
            ),
        ),
    ],

    "china": [
        (
            "Google News 中国财经",
            rss(
                "中国财经 OR 中国经济 OR A股 OR 人民币 "
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
    ],

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
                "quarterly results forecast"
            ),
        ),
    ],
}


# ============================================================
# 3. 通用工具
# ============================================================

def clean(value):
    """清理 HTML 标签和多余空白。"""
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def chinese(text):
    """判断文本是否包含中文字符。"""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def get_response(url, timeout=15, headers=None):
    """获取网络响应，返回正文和最终跳转地址。"""
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
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


def get(url, timeout=20, headers=None):
    """兼容原有调用方式，只返回响应正文。"""
    body, _ = get_response(
        url,
        timeout=timeout,
        headers=headers,
    )
    return body


# ============================================================
# 4. Google News 链接解析
# ============================================================

def is_google_news_url(url):
    """判断链接是否属于 Google News 中转地址。"""
    try:
        host = (urlparse(url).hostname or "").lower()

        return (
            host == "news.google.com"
            or host.endswith(".news.google.com")
        )

    except Exception:
        return False


def is_http_url(url):
    """只允许 HTTP/HTTPS 地址。"""
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
        )

    except Exception:
        return False


def extract_article_url(url):
    """
    尝试从 Google News 中转地址提取真实文章地址。

    处理策略：
    1. 先检查 URL 中是否存在明确的目标地址参数。
    2. 再尝试跟随 HTTP 跳转。
    3. 最后检查返回页面的 canonical 或 og:url。
    4. 如果无法确定真实目标，则返回空字符串。
    """
    if not url or not is_http_url(url):
        return ""

    if not is_google_news_url(url):
        return url

    # 部分中转链接会直接带有目标 URL 参数。
    try:
        query = parse_qs(urlparse(url).query)

        for key in ("url", "u", "target", "article_url"):
            for value in query.get(key, []):
                value = html.unescape(value.strip())

                if (
                    is_http_url(value)
                    and not is_google_news_url(value)
                ):
                    return value

    except Exception:
        pass

    # 尝试让标准 HTTP 客户端跟随重定向。
    try:
        body, final_url = get_response(
            url,
            timeout=6,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
        )

        if (
            is_http_url(final_url)
            and not is_google_news_url(final_url)
        ):
            return final_url

        # 只提取页面明确标注的 canonical / og:url。
        # 不猜测文章网址，也不将来源主页冒充原文。
        page = body[:500000].decode(
            "utf-8",
            errors="replace",
        )

        patterns = [
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
            r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                page,
                flags=re.I,
            )

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


def make_search_url(title):
    """
    为无法解析的新闻生成备用搜索入口。

    这不是原文地址，而是按新闻标题搜索媒体报道的入口。
    """
    query = quote((title or "").strip())

    if not query:
        return ""

    return "https://www.google.com/search?q=" + query


def prepare_article_links(item):
    """
    规范化新闻链接，并保留备用入口。

    url：优先使用解析成功的原文链接。
    originalUrl：采集时获得的原始链接。
    searchUrl：备用搜索入口。
    linkStatus：用于区分真实原文与中转链接。
    """
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
        # 不能确认原文地址时，不伪造媒体链接。
        # 保留原链接供用户自行尝试，并提供备用搜索入口。
        item["url"] = original_url
        item["linkStatus"] = "Google News 中转链接"
    
    return item


# ============================================================
# 5. RSS / Atom 解析
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

    # RSS 格式
    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        link = clean(item.findtext("link"))

        if not title or not link:
            continue

        source_element = item.find("source")

        actual_source = (
            clean(source_element.text)
            if source_element is not None
            else source
        )

        output.append({
            "title": title,
            "url": link,
            "summary": clean(item.findtext("description")),
            "source": actual_source or source,
            "published": clean(item.findtext("pubDate")),
        })

    # Atom 格式
    if not output:
        namespaces = {
            "a": "http://www.w3.org/2005/Atom",
        }

        for item in root.findall(".//a:entry", namespaces):
            title = clean(
                item.findtext(
                    "a:title",
                    default="",
                    namespaces=namespaces,
                )
            )

            link_element = item.find("a:link", namespaces)

            link = (
                link_element.get("href", "")
                if link_element is not None
                else ""
            )

            summary = clean(
                item.findtext(
                    "a:summary",
                    default="",
                    namespaces=namespaces,
                )
                or item.findtext(
                    "a:content",
                    default="",
                    namespaces=namespaces,
                )
            )

            published = clean(
                item.findtext(
                    "a:published",
                    default="",
                    namespaces=namespaces,
                )
                or item.findtext(
                    "a:updated",
                    default="",
                    namespaces=namespaces,
                )
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
# 6. 发布时间处理
# ============================================================

def parse_date(value):
    """解析 RSS 新闻时间，统一转换成 UTC。"""
    if value:
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
            pass

    # 不把无法解析的时间默认为“刚刚”。
    # 否则旧新闻会被误认为最新新闻。
    return None


# ============================================================
# 7. 翻译
# ============================================================

def translate(text):
    """尝试将英文文本翻译成简体中文。"""
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
# 8. AI 缓存
# ============================================================

def load_cache():
    """读取 AI 分析缓存。"""
    try:
        data = json.loads(
            CACHE.read_text(encoding="utf-8")
        )

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


# ============================================================
# 9. Gemini 接口
# ============================================================

def gemini(prompt):
    """调用 Gemini；429 后停止本次运行的后续 AI 请求。"""
    global GEMINI_QUOTA_BLOCKED

    if GEMINI_QUOTA_BLOCKED:
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        print("[INFO] 未配置 GEMINI_API_KEY，跳过 AI 分析。")
        return None

    model = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.8-flash",
    ).strip()

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
                "User-Agent": "FinanceResearchDashboard/2.1",
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
            )[:500]

            print(f"[ERROR] Gemini HTTP {error.code}: {detail}")

            if error.code == 429:
                GEMINI_QUOTA_BLOCKED = True
                print("[INFO] Gemini 限流，本次停止后续 AI 调用。")
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
# 10. AI JSON 解析
# ============================================================

def parse_ai(raw):
    """解析 AI 返回的 JSON。"""
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
# 11. AI 深度分析
# ============================================================

def insight(item, cache, budget):
    """先读取缓存；每次运行最多发起 3 次 AI 请求。"""
    cache_key = hashlib.sha256(
        (item["title"] + "|" + item["url"]).encode("utf-8")
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
            "特别分析营收、利润、利润率、现金流、"
            "业绩指引和市场预期的差异。"
        )

    prompt = f"""
你是审慎的财经研究助理，不是荐股员。使用简体中文。

只能依据输入的标题、来源和摘要。
不得编造事实、数字、估值或市场预期。
材料不足时，明确说明需要核实原文。

{special}

请只输出合法 JSON，不要 Markdown。

必须包含：
analysis：100—180 字的新闻解读。
core_logic：事件→变量变化→影响对象的逻辑链。
market_impact：潜在受益或承压方向及不确定性。
transmission：通过利率、汇率、成本、利润、供需、
融资条件或风险偏好等因素的传导机制。
impact：一句话概括潜在市场影响。
assets：最多 4 个相关资产、指数、行业或 ETF 类别。
time_horizon：短期、中期、长期影响及不确定性。
scenarios：最多 3 条情景及各自触发条件。
evidence：最多 3 条输入摘要确实提供的证据线索，
每条注明“摘要提供，未独立核实”。
risk：反证、混杂因素、滞后效应和信息缺口。
confidence：低、中、高的信心等级及理由。

必须区分材料直接支持的事实、推断和待核实信息。
不得把推断说成已证实事实。
不得声称已独立核实新闻原文或数据。
不提供个性化投资建议。

标题：{item['title']}
来源：{item['source']}
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
# 12. 栏目分类
# ============================================================

def classify(items, channel):
    """按关键词筛选亚太、国内及财报新闻。"""

    patterns = {
        "asia": (
            r"asia|asian|china|chinese|japan|korea|taiwan|"
            r"hong kong|singapore|india|australia|亚太|中国|"
            r"日本|韩国|台湾|香港|新加坡|印度|澳大利亚"
        ),

        "china": (
            r"中国|人民币|A股|沪深|港股|央行|中国经济|"
            r"China|Chinese|yuan|Shanghai|Shenzhen|PBOC|CSI 300"
        ),

        "earnings": (
            r"earnings|revenue|profit|quarter|forecast|"
            r"guidance|results|财报|业绩|营收|利润|季度|盈利"
        ),
    }

    pattern = patterns.get(channel)

    if not pattern:
        return items

    return [
        item
        for item in items
        if re.search(
            pattern,
            item["title"] + " " + item.get("summary", ""),
            re.I,
        )
    ]


# ============================================================
# 13. 新闻采集和处理
# ============================================================

def build(channel, cache, budget):
    """采集一个栏目，处理链接、翻译、分析并返回新闻。"""

    gathered = []
    seen_links = set()
    seen_titles = set()

    for source, feed_url in FEEDS[channel]:
        entries = parse_feed(source, feed_url)

        for item in entries:
            title_key = clean(item["title"]).lower()
            link = item["url"].strip()

            if (
                not title_key
                or not link
                or link in seen_links
                or title_key in seen_titles
            ):
                continue

            seen_links.add(link)
            seen_titles.add(title_key)

            item["_date"] = parse_date(item.get("published"))
            gathered.append(item)

    now = datetime.now(timezone.utc)

    gathered = classify(gathered, channel)

    # 仅保留时间明确、最近 5 天内的新闻。
    gathered = [
        item
        for item in gathered
        if item.get("_date") is not None
        and 0 <= (now - item["_date"]).total_seconds() < 5 * 86400
    ]

    gathered.sort(
        key=lambda item: item["_date"],
        reverse=True,
    )

    output = []

    for item in gathered:
        if len(output) >= LIMITS[channel]:
            break

        original_title = item["title"]
        original_summary = item.get("summary", "")

        if channel == "china" and chinese(original_title):
            title = original_title
        else:
            title = translate(original_title)

        if channel == "china" and chinese(original_summary):
            summary = original_summary
        else:
            summary = translate(original_summary)

        news_item = {
            "title": title or original_title,
            "originalTitle": original_title,
            "summary": (summary or original_summary)[:900],
            "source": item["source"],
            "url": item["url"],
            "publishedAt": item["_date"].astimezone(TZ).strftime(
                "%Y-%m-%d %H:%M"
            ),
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
        }

        # 新增：尝试把 Google News 中转链接解析为原文链接。
        prepare_article_links(news_item)

        # 使用最终 URL 作为缓存标识的一部分。
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
# 14. 写入网页
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
            "请确认使用的是对应版本的 index.html。"
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

    # 如果原页面有 updateTime 元素，就更新它。
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

    PAGE.write_text(
        page,
        encoding="utf-8",
    )

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
            "新闻采集仍会继续，但部分新闻没有 AI 分析。"
        )

    print(
        "提示：Google News 原文解析失败时，"
        "程序会保留中转链接，并提供备用搜索地址。"
    )

    print(
        "注意：新闻重要性评分属于启发式排序，"
        "不是经过验证的市场预测。"
    )


if __name__ == "__main__":
    main()
