# -*- coding: utf-8 -*-
"""财经研究辅助看板更新器。Python 标准库实现。"""

import os
import re
import json
import time
import hashlib
import html
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
import xml.etree.ElementTree as ET


# =========================
# 基本配置
# =========================

BASE = Path(__file__).resolve().parent
PAGE = BASE / "index.html"
CACHE = BASE / "ai_cache.json"

# 北京时间
TZ = timezone(timedelta(hours=8))

# AI 调用限制：每次运行最多尝试 3 次
MAX_AI_CALLS = 3

# 每次请求最多重试 1 次
RETRIES = 1

# 一旦遇到 Gemini 429 限流，本次运行停止后续 AI 调用
GEMINI_QUOTA_BLOCKED = False

# 每个栏目的新闻数量上限
LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}


# =========================
# 新闻来源
# =========================

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


# =========================
# 文本处理与网络请求
# =========================

def clean(value):
    """去除 HTML 标签和多余空白。"""
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def get(url, timeout=20, headers=None):
    """发送网络请求。"""
    request_headers = {
        "User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36",
        "Accept": (
            "application/rss+xml, application/xml, "
            "text/xml, */*"
        ),
    }

    request_headers.update(headers or {})

    request = Request(url, headers=request_headers)

    with urlopen(request, timeout=timeout) as response:
        return response.read()


def chinese(text):
    """判断文本中是否含有中文。"""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


# =========================
# RSS 新闻解析
# =========================

def parse_feed(source, url):
    """读取 RSS 或 Atom 新闻源。"""
    try:
        root = ET.fromstring(get(url))
    except Exception as error:
        print(
            f"[WARN] RSS {source}: "
            f"{type(error).__name__}: {error}"
        )
        return []

    output = []

    # 先解析常见 RSS 格式
    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        link = clean(item.findtext("link"))

        if not title or not link:
            continue

        output.append({
            "title": title,
            "url": link,
            "summary": clean(item.findtext("description")),
            "source": source,
            "published": clean(item.findtext("pubDate")),
        })

    # 如果 RSS 格式没有新闻，尝试解析 Atom
    if not output:
        namespaces = {
            "a": "http://www.w3.org/2005/Atom"
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


# =========================
# 新闻时间处理
# =========================

def date(value):
    """将新闻发布时间转换为 UTC 时间。"""
    try:
        from email.utils import parsedate_to_datetime

        result = parsedate_to_datetime(value)

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)

    except Exception:
        try:
            result = datetime.fromisoformat(
                (value or "").replace("Z", "+00:00")
            )

            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)

            return result.astimezone(timezone.utc)

        except Exception:
            # 无法解析时间时使用当前时间。
            # 这意味着该新闻的时间可能不准确。
            return datetime.now(timezone.utc)


# =========================
# 新闻翻译
# =========================

def translate(text):
    """
    将英文新闻翻译为简体中文。
    已含中文的文本直接保留。
    翻译失败时保留原文。
    """
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
        result = json.loads(get(url, timeout=10).decode())

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


# =========================
# AI 分析缓存
# =========================

def load_cache():
    """读取之前保存的 AI 分析结果。"""
    try:
        data = json.loads(
            CACHE.read_text(encoding="utf-8")
        )

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


# =========================
# Gemini AI 接口
# =========================

def gemini(prompt):
    """
    调用 Gemini。

    改进：
    1. 单次请求超时 18 秒。
    2. 最多重试 1 次。
    3. 遇到 HTTP 429 后停止本次运行的后续 AI 请求。
    4. 没有 API Key 时跳过 AI 分析，不阻止新闻生成。
    """
    global GEMINI_QUOTA_BLOCKED

    if GEMINI_QUOTA_BLOCKED:
        print("[提示] Gemini 已触发限流，跳过本次 AI 请求。")
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        print("[ERROR] 未配置 GEMINI_API_KEY")
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
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt}
                ],
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
                "User-Agent": "FinanceResearchDashboard/2.0",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=18) as response:
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

            print("[WARN] Gemini 返回空内容")

        except HTTPError as error:
            detail = error.read().decode(
                "utf-8",
                errors="replace",
            )[:800]

            print(
                f"[ERROR] Gemini HTTP {error.code}: {detail}"
            )

            # HTTP 429：停止本次运行后续 AI 调用
            if error.code == 429:
                GEMINI_QUOTA_BLOCKED = True

                print(
                    "[提示] Gemini 额度或速率限制已触发；"
                    "停止后续 AI 调用，但继续生成新闻。"
                )

                return None

            # 对于无法通过重试解决的错误，直接跳过
            if error.code not in (
                408,
                500,
                502,
                503,
                504,
            ):
                if error.code in (401, 403):
                    print("[提示] 请检查 API Key 与权限。")

                if error.code == 404:
                    print(
                        "[提示] 请检查 GEMINI_MODEL：",
                        model,
                    )

                return None

        except (TimeoutError, URLError) as error:
            print(
                f"[ERROR] Gemini 网络超时或连接失败"
                f"（第 {attempt + 1} 次）："
                f"{type(error).__name__}: {error}"
            )

        except Exception as error:
            print(
                "[ERROR] Gemini 调用异常：",
                type(error).__name__,
                error,
            )
            return None

        # 只对可重试的错误进行一次重试
        if attempt < RETRIES:
            delay = min(2 ** (attempt + 1), 12)

            print(
                f"[提示] {delay} 秒后重试"
                f"（{attempt + 1}/{RETRIES}）"
            )

            time.sleep(delay)

    print("[WARN] 重试已达上限，本条留待下次更新。")
    return None


# =========================
# AI 返回内容解析
# =========================

def parse_ai(raw):
    """解析 Gemini 返回的 JSON。"""
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
        values = data.get(key, [])

        if not isinstance(values, list):
            return []

        return [
            clean(item)[:250]
            for item in values
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


# =========================
# 新闻深度分析
# =========================

def insight(item, cache, budget):
    """
    获取新闻分析。

    优先使用缓存，减少重复 AI 调用。
    每次运行最多发起 MAX_AI_CALLS 次 AI 请求。
    """
    cache_key = hashlib.sha256(
        (
            item["title"]
            + "|"
            + item["url"]
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
            "请特别分析营收、利润、利润率、现金流、"
            "业绩指引与市场预期之间的差异。"
        )

    prompt = f"""
你是审慎的财经研究助理，不是荐股员。
请使用简体中文分析新闻。

只使用给定标题、来源和摘要。
不得编造事实、数字、估值或市场预期。
信息不足时，明确指出需要核验原文。

{special}

严格输出 JSON，不要 Markdown。
必须包含以下字段：

analysis：
100—180 字，说明事件、含义和重要性。

core_logic：
用“事件 → 变量变化 → 影响对象”的方式解释逻辑链。

market_impact：
说明潜在受益或承压方向。
如果证据不足，明确说明不确定性。

transmission：
说明影响如何通过供需、利率、汇率、成本、
利润、融资条件或风险偏好等因素传导。

impact：
用一句话概括潜在市场影响。

assets：
最多 4 个相关资产、指数、行业或 ETF 类别。
它们仅供观察，不暗示必然涨跌。

time_horizon：
分析短期、中期、长期影响，以及相应依据和不确定性。

scenarios：
最多 3 条情景，包括基准、上行和下行情景。
每条情景都应说明触发条件；信息不足时要明确指出。

evidence：
最多 3 条证据线索。
只列出输入摘要中确实出现的信息，不得伪造引文。
每条都要注明“摘要提供，未独立核实”。

证据意识：
必须区分：
1. 输入材料直接支持的事实；
2. 基于材料作出的推断；
3. 仍需核实的信息。

不得把推断当成已经证实的事实。
不得声称已经独立核实来源、数据或新闻原文，
除非输入确实提供了相应证据。

risk：
说明可能的反证、混杂因素、滞后效应和信息缺口。

confidence：
给出低、中或高的信心等级，并解释材料充分程度。
只有标题和摘要时，不得轻易给出高信心。

不提供个性化投资建议。

标题：{item['title']}
来源：{item['source']}
摘要：{item.get('summary', '')[:1200]}
"""

    # 记录实际发起的 AI 尝试次数
    budget["attempts"] += 1

    ai = parse_ai(gemini(prompt))

    if (
        ai
        and ai.get("analysis")
        and ai.get("coreLogic")
    ):
        cache[cache_key] = ai
        budget["successes"] += 1
        return ai

    budget["failures"] += 1
    return None


# =========================
# 栏目分类
# =========================

def classify(items, channel):
    """根据标题和摘要进行基础关键词分类。"""

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

    if channel not in patterns:
        return items

    pattern = patterns[channel]

    return [
        item
        for item in items
        if re.search(
            pattern,
            item["title"] + " " + item.get("summary", ""),
            re.I,
        )
    ]


# =========================
# 获取、处理和排序新闻
# =========================

def build(channel, cache, budget):
    """生成指定栏目新闻列表。"""

    gathered = []
    seen_links = set()
    seen_titles = set()

    # 收集多个 RSS 新闻源
    for source, url in FEEDS[channel]:
        for item in parse_feed(source, url):
            title_key = clean(item["title"]).lower()
            link = item["url"].strip()

            if (
                not link
                or not title_key
                or link in seen_links
                or title_key in seen_titles
            ):
                continue

            seen_links.add(link)
            seen_titles.add(title_key)

            item["_date"] = date(item.get("published"))
            gathered.append(item)

    now = datetime.now(timezone.utc)

    # 根据栏目关键词筛选
    gathered = classify(gathered, channel)

    # 仅保留最近 5 天内的新闻
    gathered = [
        item
        for item in gathered
        if -3600
        < (now - item["_date"]).total_seconds()
        < 5 * 86400
    ]

    # 最新新闻优先
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

        # 中国新闻优先保留原本已经是中文的内容
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

            "summary": (
                summary or original_summary
            )[:900],

            "source": item["source"],
            "url": item["url"],

            "publishedAt": (
                item["_date"]
                .astimezone(TZ)
                .strftime("%Y-%m-%d %H:%M")
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

            # 内部字段，用于识别财报栏目
            "channel": channel,
        }

        # 尝试生成 AI 深度分析
        ai = insight(news_item, cache, budget)

        if ai:
            news_item.update(ai)

            news_item["impact"] = (
                ai.get("impact")
                or ai.get("marketImpact")
                or "见分层分析"
            )

            news_item["aiStatus"] = "Gemini AI"

        # 不向前端暴露内部分类字段
        news_item.pop("channel", None)

        output.append(news_item)

    # 按新闻列表的倒序序号编号
    total = len(output)

    for index, news_item in enumerate(output):
        news_item["id"] = f"NO.{total - index:02d}"

    return output


# =========================
# 更新网页数据
# =========================

def main():
    """主程序：更新新闻数据并写回 index.html。"""

    if not PAGE.exists():
        raise FileNotFoundError(
            "找不到 index.html，请确认 update.py 与 index.html 位于同一目录。"
        )

    page = PAGE.read_text(encoding="utf-8")

    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    if (
        start_marker not in page
        or end_marker not in page
    ):
        raise RuntimeError(
            "index.html 缺少新闻数据标记，已停止写入。"
            "请确认使用的是对应的升级版 index.html。"
        )

    cache = load_cache()

    budget = {
        "attempts": 0,
        "successes": 0,
        "failures": 0,
    }

    news = {}

    # 按栏目依次更新
    for channel in (
        "global",
        "asia",
        "china",
        "earnings",
    ):
        print("\n===== 更新栏目：", channel, "=====")

        news[channel] = build(
            channel,
            cache,
            budget,
        )

    # 更新时间使用北京时间
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

    # 替换网页中的新闻数据
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

    # 更新页面上的更新时间
    page = re.sub(
        r'(<span[^>]*id=["\']updateTime["\'][^>]*>).*?(</span>)',
        lambda match: (
            match.group(1)
            + html.escape(news["updatedAt"])
            + match.group(2)
        ),
        page,
        flags=re.I | re.S,
    )

    # 写回网页
    PAGE.write_text(
        page,
        encoding="utf-8",
    )

    # 保存 AI 分析缓存
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
            "提示：本次运行触发了 Gemini 限流；"
            "新闻仍会生成，但部分新闻没有 AI 深度分析。"
        )

    print(
        "注意：新闻重要性评分是前端启发式排序，"
        "不是经过验证的市场预测。"
    )


if __name__ == "__main__":
    main()
