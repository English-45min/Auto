#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
财经与时政焦点看板自动更新脚本

功能：
1. 国际财经 Top 20
2. 亚太市场 Top 10
3. 国内财经 Top 10
4. 核心财报季 Top 10
5. RSS / Atom 新闻抓取
6. 新浪财经直播接口抓取
7. 英文新闻翻译为中文
8. 新闻去重、时间排序、倒序编号
9. 自动生成市场影响解读
10. 安全写入 index.html
11. 输出详细抓取日志
12. 兼容 datetime JSON 序列化
"""

import datetime
import html
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from email.utils import parsedate_to_datetime
from pathlib import Path


# ============================================================
# 1. 基本设置
# ============================================================

BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"

GLOBAL_LIMIT = 20
ASIA_LIMIT = 10
CHINA_LIMIT = 10
EARNINGS_LIMIT = 10

REQUEST_TIMEOUT = 15
MAX_SUMMARY_LENGTH = 130

# 仅用于避免不同来源的同一新闻重复出现。
# 不是对新闻真实性的保证。


# ============================================================
# 2. 新闻源设置
# ============================================================

RSS_FEEDS = {
    "global": [
        (
            "CNBC World",
            "https://www.cnbc.com/id/100003114/device/rss/rss.html"
        ),
        (
            "CNBC Finance",
            "https://www.cnbc.com/id/10000664/device/rss/rss.html"
        ),
        (
            "CNBC Economy",
            "https://www.cnbc.com/id/20910258/device/rss/rss.html"
        ),
        (
            "BBC World News",
            "https://feeds.bbci.co.uk/news/world/rss.xml"
        ),
    ],

    "asia": [
        (
            "Focus Taiwan",
            "https://focustaiwan.tw/rss/news.xml"
        ),
        (
            "Taipei Times",
            "https://www.taipeitimes.com/xml/index.rss"
        ),
        (
            "CNA Business",
            "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"
        ),
        (
            "Nikkei Asia",
            "https://asia.nikkei.com/rss/feed/nar"
        ),
    ],
}


SINA_URL = (
    "https://zhibo.sina.com.cn/api/zhibo/feed"
    "?page=1&page_size=50&zhibo_id=152"
)


CHINA_RSS_FEEDS = [
    (
        "新浪财经",
        "https://rss.sina.com.cn/roll/finance/hot_roll.xml"
    ),
    (
        "华尔街见闻",
        "https://dedicated.wallstreetcn.com/rss.xml"
    ),
    (
        "证券时报",
        "https://www.stcn.com/rss/rss.xml"
    ),
]


EARNINGS_RSS_FEEDS = [
    (
        "华尔街见闻",
        "https://dedicated.wallstreetcn.com/rss.xml"
    ),
    (
        "新浪财经",
        "https://rss.sina.com.cn/roll/finance/hot_roll.xml"
    ),
    (
        "CNBC Finance",
        "https://www.cnbc.com/id/10000664/device/rss/rss.html"
    ),
]


# ============================================================
# 3. 时间处理
# ============================================================

def now_beijing():
    """获取当前北京时间。"""
    return datetime.datetime.now(
        datetime.timezone.utc
    ).astimezone(BEIJING_TZ)


def parse_date(value):
    """兼容常见日期格式、Unix 时间戳和 RFC 822 日期。"""

    if value is None or value == "":
        return None

    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=BEIJING_TZ)

        return value.astimezone(BEIJING_TZ)

    if isinstance(value, (int, float)):
        try:
            stamp = float(value)

            # 兼容毫秒级 Unix 时间戳
            if stamp > 10**12:
                stamp /= 1000

            return datetime.datetime.fromtimestamp(
                stamp,
                tz=datetime.timezone.utc
            ).astimezone(BEIJING_TZ)

        except (ValueError, OverflowError, OSError):
            return None

    value = str(value).strip()

    if not value:
        return None

    # ISO 8601
    try:
        normalized = value

        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        result = datetime.datetime.fromisoformat(normalized)

        if result.tzinfo is None:
            result = result.replace(tzinfo=BEIJING_TZ)

        return result.astimezone(BEIJING_TZ)

    except (ValueError, TypeError):
        pass

    # RSS 常见日期格式
    try:
        result = parsedate_to_datetime(value)

        if result.tzinfo is None:
            result = result.replace(
                tzinfo=datetime.timezone.utc
            )

        return result.astimezone(BEIJING_TZ)

    except (ValueError, TypeError, OverflowError):
        pass

    # 常见本地日期格式
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            result = datetime.datetime.strptime(value, fmt)
            return result.replace(tzinfo=BEIJING_TZ)

        except ValueError:
            continue

    return None


# ============================================================
# 4. 文本清理
# ============================================================

def clean_text(value):
    """清理 HTML 标签、实体和多余空白。"""

    if value is None:
        return ""

    text = str(value)

    text = re.sub(
        r"(?is)<script.*?>.*?</script>",
        " ",
        text
    )

    text = re.sub(
        r"(?is)<style.*?>.*?</style>",
        " ",
        text
    )

    text = re.sub(
        r"(?s)<[^>]*>",
        " ",
        text
    )

    text = html.unescape(text)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_title(value):
    """标准化标题，用于去重。"""

    text = clean_text(value).lower()

    return re.sub(
        r"[\W_]+",
        "",
        text,
        flags=re.UNICODE
    )


def shorten_text(text, max_length=MAX_SUMMARY_LENGTH):
    """截断过长摘要。"""

    text = clean_text(text)

    if len(text) > max_length:
        return text[:max_length].rstrip() + "..."

    return text


def contains_chinese(text):
    return bool(
        re.search(r"[\u4e00-\u9fff]", text or "")
    )


# ============================================================
# 5. HTTP 请求
# ============================================================

def http_get(url, timeout=REQUEST_TIMEOUT):
    """发起 HTTP 请求。"""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": (
                "application/rss+xml, "
                "application/atom+xml, "
                "application/xml, "
                "text/xml, "
                "application/json, "
                "*/*"
            ),
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout
    ) as response:

        return response.read()


# ============================================================
# 6. 英文新闻翻译
# ============================================================

def translate_to_chinese(text):
    """
    尝试将新闻翻译成中文。

    如果翻译服务不可用，则返回原文。
    不会因为翻译失败而终止整个更新流程。
    """

    text = clean_text(text)

    if not text:
        return ""

    # 已经是中文的内容不重复翻译
    if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 8:
        return text

    try:
        params = urllib.parse.urlencode({
            "client": "gtx",
            "sl": "auto",
            "tl": "zh-CN",
            "dt": "t",
            "q": text[:400],
        })

        url = (
            "https://translate.googleapis.com/translate_a/single?"
            + params
        )

        raw = http_get(url, timeout=10)

        payload = json.loads(
            raw.decode("utf-8", errors="replace")
        )

        result_parts = []

        if (
            isinstance(payload, list)
            and payload
            and isinstance(payload[0], list)
        ):
            for part in payload[0]:
                if (
                    isinstance(part, list)
                    and part
                    and isinstance(part[0], str)
                ):
                    result_parts.append(part[0])

        result = "".join(result_parts).strip()

        if result:
            return result

    except Exception as exc:
        print(
            f"⚠️ 翻译服务暂不可用，保留原文：{exc}"
        )

    return text


# ============================================================
# 7. RSS / Atom 解析
# ============================================================

def child_text(node, wanted_names):
    """按标签名称获取 XML 子节点内容，兼容命名空间。"""

    wanted_names = {
        name.lower()
        for name in wanted_names
    }

    for child in list(node):
        key = child.tag.rsplit("}", 1)[-1].lower()

        if key in wanted_names:
            if key == "link":
                href = child.attrib.get("href")

                if href:
                    return href.strip()

            return clean_text(child.text or "")

    return ""


def parse_rss_xml(xml_bytes, source_name):
    """解析 RSS 2.0、RSS 1.0 和 Atom 新闻。"""

    items = []

    try:
        try:
            xml_str = xml_bytes.decode("utf-8-sig")

        except UnicodeDecodeError:
            xml_str = xml_bytes.decode(
                "gb18030",
                errors="replace"
            )

        root = ET.fromstring(xml_str)

    except Exception as exc:
        print(
            f"⚠️ [{source_name}] XML 解析失败：{exc}"
        )
        return items

    for node in root.iter():

        tag = node.tag.rsplit("}", 1)[-1].lower()

        if tag not in ("item", "entry"):
            continue

        title = child_text(
            node,
            {"title"}
        )

        summary = child_text(
            node,
            {
                "description",
                "summary",
                "encoded",
                "content",
            }
        )

        # 兼容 Atom 和普通 RSS 的链接格式
        link = ""

        for child in list(node):

            key = child.tag.rsplit("}", 1)[-1].lower()

            if key != "link":
                continue

            candidate = (
                child.attrib.get("href")
                or child.text
                or ""
            ).strip()

            rel = child.attrib.get("rel", "alternate")

            if candidate and rel in ("alternate", ""):
                link = candidate
                break

            if candidate and not link:
                link = candidate

        # RSS 可能使用不同的发布时间字段
        published_text = child_text(
            node,
            {
                "pubdate",
                "published",
                "updated",
                "date",
                "issued",
            }
        )

        published = parse_date(published_text)

        if not title:
            continue

        items.append({
            "title": title,
            "summary": summary,
            "published": published,
            "source": source_name,
            "url": link,
        })

    return items


# ============================================================
# 8. 新闻源抓取及诊断
# ============================================================

def fetch_feed(source_name, url):
    """抓取单个 RSS 来源，并输出结果。"""

    try:
        data = http_get(url)

        items = parse_rss_xml(
            data,
            source_name
        )

        print(
            f"✅ [{source_name}] RSS 获取 {len(items)} 条"
        )

        return items

    except urllib.error.HTTPError as exc:
        print(
            f"❌ [{source_name}] HTTP 错误："
            f"{exc.code} {exc.reason}"
        )

    except urllib.error.URLError as exc:
        print(
            f"❌ [{source_name}] 网络错误：{exc.reason}"
        )

    except Exception as exc:
        print(
            f"❌ [{source_name}] 抓取失败：{exc}"
        )

    return []


def collect_rss(feed_defs):
    """依次抓取一组 RSS 来源。"""

    all_items = []

    for source_name, url in feed_defs:

        items = fetch_feed(
            source_name,
            url
        )

        all_items.extend(items)

        # 减少连续请求的压力
        time.sleep(0.15)

    return all_items


# ============================================================
# 9. 新浪财经直播接口
# ============================================================

def extract_sina_feed_list(data):
    """
    兼容新浪接口可能出现的多种数据结构。

    常见形式：
    result.data.feed
    result.data
    result.data.list
    result.data.items
    """

    if not isinstance(data, dict):
        return []

    result = data.get("result", {})

    if not isinstance(result, dict):
        return []

    result_data = result.get("data", {})

    if isinstance(result_data, list):
        return result_data

    if not isinstance(result_data, dict):
        return []

    for key in (
        "feed",
        "list",
        "items",
        "data",
    ):

        value = result_data.get(key)

        if isinstance(value, list):
            return value

    # 某些接口可能将列表直接放在 result.data 下的其他字段
    for value in result_data.values():

        if isinstance(value, list) and value:
            if isinstance(value[0], dict):
                return value

    return []


def fetch_china_direct():
    """抓取新浪财经直播消息。"""

    items = []

    try:
        raw = http_get(
            SINA_URL,
            timeout=12
        )

        data = json.loads(
            raw.decode("utf-8", errors="replace")
        )

        feed_list = extract_sina_feed_list(data)

        print(
            f"ℹ️ 新浪财经接口返回候选记录："
            f"{len(feed_list)} 条"
        )

        for feed in feed_list:

            if not isinstance(feed, dict):
                continue

            content = clean_text(
                feed.get("rich_text")
                or feed.get("title")
                or feed.get("content")
                or ""
            )

            if len(content) < 8:
                continue

            # 兼容新浪直播常见的【标题】正文格式
            match = re.match(
                r"^【([^】]+)】\s*(.*)$",
                content
            )

            if match:

                title = match.group(1).strip()

                summary = (
                    match.group(2).strip()
                    or content
                )

            else:

                title = content[:50].strip()

                if len(content) > 50:
                    title += "..."

                summary = content

            published = parse_date(
                feed.get("create_time")
                or feed.get("ctime")
                or feed.get("time")
                or feed.get("timestamp")
            )

            link = (
                feed.get("url")
                or feed.get("link")
                or ""
            )

            items.append({
                "title": title,
                "summary": summary,
                "published": published,
                "source": "新浪财经",
                "url": str(link).strip(),
            })

        print(
            f"✅ 新浪财经直播解析完成："
            f"{len(items)} 条"
        )

    except urllib.error.HTTPError as exc:
        print(
            f"❌ 新浪财经接口 HTTP 错误：{exc.code}"
        )

    except urllib.error.URLError as exc:
        print(
            f"❌ 新浪财经接口网络错误：{exc.reason}"
        )

    except Exception as exc:
        print(
            f"❌ 新浪财经接口解析失败：{exc}"
        )

    return items


# ============================================================
# 10. 新闻分类规则
# ============================================================

ASIA_KEYWORDS = (
    "asia",
    "singapore",
    "japan",
    "korea",
    "south korea",
    "hong kong",
    "china",
    "taiwan",
    "india",
    "indonesia",
    "malaysia",
    "thailand",
    "vietnam",
    "philippines",
    "亚太",
    "亚洲",
    "新加坡",
    "日本",
    "韩国",
    "香港",
    "台湾",
    "中国",
    "印度",
    "印尼",
    "马来西亚",
    "泰国",
    "越南",
    "菲律宾",
    "日经",
    "港股",
    "恒生",
    "上证",
    "人民币",
    "台积电",
    "三星",
)


def filter_asia_news(items):
    """
    筛选亚太相关报道。

    注意：这是关键词筛选，不是人工核实的地域分类。
    """

    result = []

    for item in items:

        text = (
            item.get("title", "")
            + " "
            + item.get("summary", "")
        ).lower()

        if any(
            keyword.lower() in text
            for keyword in ASIA_KEYWORDS
        ):
            result.append(item)

    return result


CHINA_RELEVANCE_KEYWORDS = (
    "中国",
    "国内",
    "央行",
    "人民币",
    "a股",
    "沪指",
    "上证",
    "深证",
    "创业板",
    "科创板",
    "港股",
    "证监会",
    "财政部",
    "国家统计局",
    "中国经济",
    "中国市场",
    "中国股市",
    "gdp",
    "cpi",
    "ppi",
    "lpr",
    "房地产",
    "房贷",
    "国债",
    "中美贸易",
    "中国出口",
    "中国进口",
    "中国制造",
    "国务院",
    "金融监管",
    "存款准备金",
    "降准",
    "降息",
    "证券时报",
    "沪深",
    "北交所",
    "中国人民银行",
)


CHINA_EXCLUDE_KEYWORDS = (
    "美联储",
    "美国大选",
    "白宫",
    "乌克兰",
    "俄罗斯",
    "泽连斯基",
    "加沙",
    "中东局势",
)


def is_china_relevant(item):
    """
    判断新闻是否与中国财经相关。

    如果标题包含明确的国内财经关键词，即使摘要提及
    国际事件，也允许保留，减少过度过滤。
    """

    title = clean_text(
        item.get("title", "")
    ).lower()

    summary = clean_text(
        item.get("summary", "")
    ).lower()

    text = title + " " + summary

    # 优先判断标题中的国内财经相关性
    if any(
        keyword.lower() in title
        for keyword in CHINA_RELEVANCE_KEYWORDS
    ):
        return True

    # 对明显无关的国际政治新闻进行过滤
    if any(
        keyword.lower() in text
        for keyword in CHINA_EXCLUDE_KEYWORDS
    ):
        return False

    return any(
        keyword.lower() in text
        for keyword in CHINA_RELEVANCE_KEYWORDS
    )


# ============================================================
# 11. 国内财经新闻聚合
# ============================================================

def fetch_china_news():

    print("\n========== 国内财经新闻抓取 ==========")

    # 第一优先级：新浪财经直播
    direct_items = fetch_china_direct()

    filtered = [
        item
        for item in direct_items
        if is_china_relevant(item)
    ]

    print(
        f"ℹ️ 新浪财经过滤后：{len(filtered)} 条"
    )

    # 第二优先级：补充 RSS 来源
    if len(filtered) < CHINA_LIMIT:

        rss_items = collect_rss(
            CHINA_RSS_FEEDS
        )

        existing_titles = {
            normalize_title(item.get("title", ""))
            for item in filtered
        }

        for item in rss_items:

            if not is_china_relevant(item):
                continue

            key = normalize_title(
                item.get("title", "")
            )

            if not key or key in existing_titles:
                continue

            existing_titles.add(key)

            filtered.append(item)

    print(
        f"✅ 国内财经新闻候选总数：{len(filtered)} 条"
    )

    return filtered


# ============================================================
# 12. 财报新闻聚合
# ============================================================

EARNINGS_KEYWORDS = (
    "财报",
    "净利润",
    "营收",
    "业绩",
    "季度报告",
    "年度报告",
    "财务报告",
    "盈利",
    "亏损",
    "每股收益",
    "earnings",
    "revenue",
    "profit",
    "quarterly results",
    "quarterly earnings",
    "eps",
    "guidance",
    "forecast",
)


def fetch_earnings_news():

    print("\n========== 财报新闻抓取 ==========")

    items = collect_rss(
        EARNINGS_RSS_FEEDS
    )

    earnings_items = []

    for item in items:

        text = (
            item.get("title", "")
            + " "
            + item.get("summary", "")
        ).lower()

        if any(
            keyword.lower() in text
            for keyword in EARNINGS_KEYWORDS
        ):
            earnings_items.append(item)

    print(
        f"✅ 财报相关新闻候选："
        f"{len(earnings_items)} 条"
    )

    return earnings_items


# ============================================================
# 13. 市场影响解读
# ============================================================

def generate_ai_insights(title, summary, channel_tag):
    """
    基于关键词生成规则化市场解读。

    说明：
    此函数不是调用大语言模型生成内容，
    而是根据关键词匹配给出提示性解读。
    """

    text = (
        title + " " + summary
    ).lower()

    impact = (
        "需结合新闻原文、官方数据及实时行情，"
        "进一步判断对市场的实际影响。"
    )

    asset = "相关市场指数 / 行业板块"

    if any(
        word in text
        for word in (
            "财报",
            "营收",
            "净利润",
            "earnings",
            "revenue",
            "eps",
            "业绩",
        )
    ):

        impact = (
            "业绩及盈利预期可能影响公司估值，"
            "应重点核对实际财报与市场预期的差异。"
        )

        asset = (
            "相关上市公司 / 所属行业指数"
        )

    elif any(
        word in text
        for word in (
            "降息",
            "加息",
            "美联储",
            "央行",
            "lpr",
            "利率",
            "货币政策",
        )
    ):

        impact = (
            "货币政策及利率预期可能改变融资成本和资产定价，"
            "需关注官方政策、债券收益率及汇率变化。"
        )

        asset = (
            "国债 / 汇率 / 黄金 / 银行板块"
        )

    elif any(
        word in text
        for word in (
            "半导体",
            "芯片",
            "台积电",
            "英伟达",
            "nvidia",
            "人工智能",
            "ai算力",
        )
    ):

        impact = (
            "半导体及人工智能产业链的供需、订单和资本开支"
            "可能影响相关企业盈利预期。"
        )

        asset = (
            "半导体指数 / 纳斯达克 / 相关科技股"
        )

    elif any(
        word in text
        for word in (
            "房地产",
            "楼市",
            "房贷",
            "房价",
            "地产",
        )
    ):

        impact = (
            "房地产政策、销售和融资条件变化"
            "可能影响地产链企业及相关信用风险。"
        )

        asset = (
            "房地产板块 / 建材 / 银行"
        )

    elif any(
        word in text
        for word in (
            "关税",
            "贸易",
            "出口",
            "进口",
            "tariff",
            "trade",
        )
    ):

        impact = (
            "贸易政策及供应链变化可能影响企业成本、"
            "出口需求与汇率预期。"
        )

        asset = (
            "出口企业 / 航运物流 / 相关汇率"
        )

    elif channel_tag == "国内核心":

        impact = (
            "国内政策、经济数据和市场预期可能影响"
            "A股及人民币资产表现，需核对相关官方信息。"
        )

        asset = (
            "沪深300 / 上证指数 / 人民币资产"
        )

    elif channel_tag == "亚太市场":

        impact = (
            "亚太地区经济、政策与企业动态可能影响"
            "区域市场及相关产业链。"
        )

        asset = (
            "日经225 / 恒生指数 / 亚太相关资产"
        )

    return impact, asset


# ============================================================
# 14. 新闻排序、去重与编号
# ============================================================

def rank_news(items, limit, channel_tag):

    deduped = []
    seen = set()

    # 过滤超过 48 小时的新闻。
    # 对没有发布时间的记录暂时保留，
    # 避免新闻源日期缺失导致整条新闻被丢弃。
    cutoff_time = (
        now_beijing()
        - datetime.timedelta(hours=48)
    )

    for raw in items:

        item = dict(raw)

        title = clean_text(
            item.get("title", "")
        )

        if not title:
            continue

        key = normalize_title(title)

        if not key or key in seen:
            continue

        seen.add(key)

        published = parse_date(
            item.get("published")
        )

        if published and published < cutoff_time:
            continue

        # 标题翻译
        if channel_tag == "国内核心":
            item["title"] = title
        else:
            item["title"] = translate_to_chinese(title)

        # 摘要翻译
        summary = clean_text(
            item.get("summary", "")
        )

        if summary and channel_tag != "国内核心":
            summary = translate_to_chinese(summary)

        item["summary"] = shorten_text(
            summary
        )

        # 统一时间格式
        item["published"] = published

        if published:
            item["time"] = published.strftime(
                "%m-%d %H:%M"
            )
        else:
            item["time"] = ""

        item["tag"] = channel_tag

        item["source"] = clean_text(
            item.get("source", "")
        ) or "公开新闻源"

        item["url"] = clean_text(
            item.get("url", "")
        )

        # 市场影响解读
        impact, asset = generate_ai_insights(
            item["title"],
            item["summary"],
            channel_tag
        )

        item["impact"] = impact
        item["asset"] = asset

        deduped.append(item)

    # 按发布时间从新到旧排序。
    # 这代表时间排序，不代表经过人工核验的重要性排名。
    min_date = datetime.datetime.min.replace(
        tzinfo=BEIJING_TZ
    )

    deduped.sort(
        key=lambda item: (
            item.get("published") or min_date
        ),
        reverse=True
    )

    selected = deduped[:limit]

    total = len(selected)

    output = []

    for index, item in enumerate(selected):

        # 倒序编号：第一条 NO.20，第二条 NO.19……
        item["id"] = (
            f"NO.{total - index:02d}"
        )

        # datetime 对象只在 Python 内部参与排序；
        # 写入 JSON 前将其转换为字符串。
        published = item.get("published")

        if isinstance(
            published,
            datetime.datetime
        ):
            item["publishedAt"] = (
                published.isoformat()
            )
        else:
            item["publishedAt"] = ""

        item.pop("published", None)

        output.append(item)

    return output


# ============================================================
# 15. 安全 JSON 序列化
# ============================================================

def js_safe_json(value):
    """
    将 Python 数据转换为可以安全嵌入 HTML 的 JSON。

    default=str 作为最后一道防线，
    避免 datetime 等特殊对象导致序列化失败。
    """

    result = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )

    # 避免字符串意外结束 script 标签
    result = result.replace(
        "</",
        "<\\/"
    )

    # 避免新闻文本中的 HTML 特殊字符破坏页面结构
    result = result.replace(
        "<",
        "\\u003c"
    ).replace(
        ">",
        "\\u003e"
    ).replace(
        "&",
        "\\u0026"
    )

    return result


# ============================================================
# 16. 更新 index.html
# ============================================================

def update_html(news_data, datetime_str):

    if not HTML_FILE.exists():

        raise FileNotFoundError(
            f"找不到网页文件：{HTML_FILE}"
        )

    content = HTML_FILE.read_text(
        encoding="utf-8"
    )

    # 必须同时存在起止标记
    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    if (
        start_marker not in content
        or end_marker not in content
    ):

        raise RuntimeError(
            "index.html 缺少 NEWS_DATA_START 或 "
            "NEWS_DATA_END 标记，已停止写入，"
            "以免误删网页内容。"
        )

    replacement = (
        start_marker
        + "\n"
        + "<script>\n"
        + "window.newsData = "
        + js_safe_json(news_data)
        + ";\n"
        + "</script>\n"
        + end_marker
    )

    pattern = (
        re.escape(start_marker)
        + r".*?"
        + re.escape(end_marker)
    )

    updated, count = re.subn(
        pattern,
        lambda _: replacement,
        content,
        count=1,
        flags=re.DOTALL,
    )

    if count != 1:

        raise RuntimeError(
            "未能准确替换网页中的新闻数据区域。"
        )

    # 更新时间
    time_pattern = (
        r'<div\s+id="updateTime"[^>]*>.*?</div>'
    )

    time_replacement = (
        '<div id="updateTime" '
        'class="text-slate-400 font-medium text-xs">'
        f'更新时间: {datetime_str} (北京时间)'
        '</div>'
    )

    updated, time_count = re.subn(
        time_pattern,
        lambda _: time_replacement,
        updated,
        count=1,
        flags=re.DOTALL,
    )

    if time_count != 1:

        print(
            "⚠️ 未找到 updateTime 元素，"
            "新闻数据仍可更新，但网页时间不会自动改变。"
        )

    # 先写入临时文件，再替换原文件
    temp_file = HTML_FILE.with_suffix(
        ".html.tmp"
    )

    temp_file.write_text(
        updated,
        encoding="utf-8"
    )

    temp_file.replace(HTML_FILE)

    print(
        f"✅ 已成功写入网页：{HTML_FILE.name}"
    )


# ============================================================
# 17. 主程序
# ============================================================

def main():

    current = now_beijing()

    datetime_str = current.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    print("=" * 60)
    print("财经与时政焦点看板：开始更新")
    print(f"北京时间：{datetime_str}")
    print("=" * 60)

    # ------------------------
    # 国际财经
    # ------------------------

    print("\n========== 国际财经 ==========")

    global_raw = collect_rss(
        RSS_FEEDS["global"]
    )

    final_global = rank_news(
        global_raw,
        GLOBAL_LIMIT,
        "全球宏观"
    )

    print(
        f"🌍 国际财经最终入选："
        f"{len(final_global)} 条"
    )

    # ------------------------
    # 亚太市场
    # ------------------------

    print("\n========== 亚太市场 ==========")

    asia_raw = collect_rss(
        RSS_FEEDS["asia"]
    )

    asia_filtered = filter_asia_news(
        asia_raw
    )

    final_asia = rank_news(
        asia_filtered,
        ASIA_LIMIT,
        "亚太市场"
    )

    print(
        f"🌏 亚太市场最终入选："
        f"{len(final_asia)} 条"
    )

    # ------------------------
    # 国内财经
    # ------------------------

    china_raw = fetch_china_news()

    final_china = rank_news(
        china_raw,
        CHINA_LIMIT,
        "国内核心"
    )

    print(
        f"🇨🇳 国内财经最终入选："
        f"{len(final_china)} 条"
    )

    # ------------------------
    # 核心财报
    # ------------------------

    earnings_raw = fetch_earnings_news()

    final_earnings = rank_news(
        earnings_raw,
        EARNINGS_LIMIT,
        "核心财报季"
    )

    print(
        f"📊 财报新闻最终入选："
        f"{len(final_earnings)} 条"
    )

    # ------------------------
    # 汇总
    # ------------------------

    news_data = {
        "global": final_global,
        "asia": final_asia,
        "china": final_china,
        "earnings": final_earnings,
    }

    total_news = sum(
        len(items)
        for items in news_data.values()
    )

    print("\n========== 更新统计 ==========")

    for key, items in news_data.items():

        print(
            f"{key}: {len(items)} 条"
        )

    print(
        f"合计：{total_news} 条"
    )

    # 四个板块全部为空时，停止覆盖现有网页数据
    if total_news == 0:

        raise RuntimeError(
            "所有新闻板块均为空。"
            "为防止覆盖现有有效新闻，本次停止写入。"
            "请检查 GitHub Actions 日志、网络和新闻源。"
        )

    # 写入网页
    update_html(
        news_data,
        datetime_str
    )

    print("\n" + "=" * 60)
    print("✅ 看板更新流程完成")
    print("=" * 60)


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print("\n❌ 更新失败")
        print(f"错误类型：{type(exc).__name__}")
        print(f"错误详情：{exc}")

        # 让 GitHub Actions 正确识别任务失败
        raise
