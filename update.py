#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财经焦点看板自动更新脚本
功能：
1. 抓取国际财经、亚太财经、国内财经新闻；
2. 去重并按启发式重要性排序；
3. 国际频道最多 10 条，亚太和国内频道各最多 5 条；
4. 编号按重要性倒序显示：NO.10 -> NO.01、NO.05 -> NO.01；
5. 将数据写入 index.html 的 NEWS_DATA_START / NEWS_DATA_END 区域；
6. 抓取失败时显示真实错误/空列表，不编造新闻。

环境变量可选：
  RSS2JSON_API_KEY  如果你有 rss2json API key，可设置此变量。
"""

import datetime
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path


# ========================= 基本设置 =========================

BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"

GLOBAL_LIMIT = 10
ASIA_LIMIT = 5
CHINA_LIMIT = 5

RSS_FEEDS = {
    # 国际财经：CNBC 全球、财经、经济
    "global": [
        ("CNBC World", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ],
    # 亚太财经：CNA 商业/亚洲、CNBC Asia、日经亚洲
    "asia": [
        ("CNA Business", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),
        ("CNA Asia", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511"),
        ("CNBC Asia", "https://www.cnbc.com/id/19832390/device/rss/rss.html"),
        ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
    ],
}

SINA_URL = (
    "https://zhibo.sina.com.cn/api/zhibo/feed"
    "?page=1&page_size=50&zhibo_id=152"
)

# 国内频道备用公开 RSS。若新浪接口失败，可尝试这些源。
CHINA_RSS_FEEDS = [
    ("财新", "https://www.caixin.com/rss/finance.xml"),
    ("中国证券网", "https://www.cnstock.com/common/rss/"),
]


# ========================= 时间与文本处理 =========================

def now_beijing():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(BEIJING_TZ)


def clean_text(value):
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_title(value):
    text = clean_text(value).lower()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def parse_date(value):
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        try:
            stamp = float(value)
            if stamp > 10**12:
                stamp /= 1000
            return datetime.datetime.fromtimestamp(
                stamp, tz=datetime.timezone.utc
            ).astimezone(BEIJING_TZ)
        except (ValueError, TypeError, OverflowError, OSError):
            return None

    value = str(value).strip()
    if not value:
        return None

    try:
        result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=BEIJING_TZ)
        return result.astimezone(BEIJING_TZ)
    except (ValueError, TypeError):
        pass

    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=datetime.timezone.utc)
        return result.astimezone(BEIJING_TZ)
    except (ValueError, TypeError, OverflowError):
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
    return None


def http_get(url, timeout=12):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, "
                      "text/xml, application/json, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ========================= RSS 抓取 =========================

def parse_rss_xml(xml_bytes, source_name):
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        print(f"⚠️ RSS XML 解析失败 [{source_name}]: {exc}")
        return items

    # 兼容 RSS 2.0 与 Atom
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue

        values = {}
        for child in list(node):
            key = child.tag.rsplit("}", 1)[-1].lower()
            if key == "link":
                link = child.attrib.get("href") or child.text or ""
                values[key] = link.strip()
            elif key not in values:
                values[key] = child.text or ""

        title = clean_text(values.get("title", ""))
        summary = clean_text(
            values.get("description") or values.get("summary")
            or values.get("content") or ""
        )
        link = values.get("link", "").strip()
        published_raw = (
            values.get("pubdate") or values.get("published")
            or values.get("updated") or values.get("date") or ""
        )
        published = parse_date(published_raw)

        if title:
            items.append({
                "title": title,
                "summary": summary,
                "published": published,
                "time": published.strftime("%H:%M") if published else "",
                "source": source_name,
                "url": link,
            })
    return items


def fetch_rss_direct(source_name, rss_url):
    try:
        data = http_get(rss_url, timeout=12)
        items = parse_rss_xml(data, source_name)
        print(f"✅ RSS [{source_name}]：获取 {len(items)} 条")
        return items
    except Exception as exc:
        print(f"⚠️ RSS [{source_name}] 抓取失败：{type(exc).__name__}: {exc}")
        return []


def fetch_rss2json(source_name, rss_url):
    """直接 RSS 失败时，可使用 rss2json 网关尝试一次。"""
    api_key = os.environ.get("RSS2JSON_API_KEY", "").strip()
    params = {"rss_url": rss_url}
    if api_key:
        params["api_key"] = api_key
    gateway = "https://api.rss2json.com/v1/api.json?" + urllib.parse.urlencode(params)

    try:
        data = json.loads(http_get(gateway, timeout=12).decode("utf-8", errors="replace"))
        if data.get("status") != "ok":
            print(f"⚠️ RSS 网关 [{source_name}] 返回状态：{data.get('status')}")
            return []

        results = []
        for item in data.get("items", []):
            title = clean_text(item.get("title", ""))
            summary = clean_text(item.get("description", "") or item.get("content", ""))
            published = parse_date(item.get("pubDate") or item.get("published") or "")
            if title:
                results.append({
                    "title": title,
                    "summary": summary,
                    "published": published,
                    "time": published.strftime("%H:%M") if published else "",
                    "source": source_name,
                    "url": item.get("link", ""),
                })
        print(f"✅ RSS 网关 [{source_name}]：获取 {len(results)} 条")
        return results
    except Exception as exc:
        print(f"⚠️ RSS 网关 [{source_name}] 失败：{type(exc).__name__}: {exc}")
        return []


def fetch_feed(source_name, url):
    items = fetch_rss_direct(source_name, url)
    if items:
        return items
    # 直连没有抓到时尝试网关；如果网关也失败，返回空列表。
    return fetch_rss2json(source_name, url)


def collect_rss(feed_defs):
    all_items = []
    for source_name, url in feed_defs:
        all_items.extend(fetch_feed(source_name, url))
        time.sleep(0.15)
    return all_items


# ========================= 新浪财经国内新闻 =========================

def find_candidate_lists(obj):
    candidates = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (
                key.lower() in {"feed", "feeds", "items", "list"}
                and isinstance(value, list)
                and any(isinstance(row, dict) for row in value)
            ):
                candidates.append(value)
            candidates.extend(find_candidate_lists(value))
    elif isinstance(obj, list):
        for value in obj:
            candidates.extend(find_candidate_lists(value))
    return candidates


def fetch_sina_china():
    try:
        raw = http_get(SINA_URL, timeout=12)
        data = json.loads(raw.decode("utf-8", errors="replace"))

        candidates = find_candidate_lists(data)
        if not candidates:
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            print(f"⚠️ 新浪财经接口结构变化：没有识别到新闻列表；顶层结构={keys}")
            return []

        feed_list = max(candidates, key=len)
        results = []
        seen = set()

        for feed in feed_list:
            if not isinstance(feed, dict):
                continue
            content = clean_text(
                feed.get("rich_text") or feed.get("content")
                or feed.get("title") or ""
            )
            if len(content) < 8:
                continue

            match = re.match(r"^【([^】]+)】\s*(.*)$", content)
            if match:
                title = match.group(1).strip()
                summary = match.group(2).strip() or content
            else:
                title = content[:100].strip()
                summary = content

            key = normalize_title(title)
            if not key or key in seen:
                continue
            seen.add(key)

            raw_date = (
                feed.get("create_time") or feed.get("ctime")
                or feed.get("time") or feed.get("created_at")
                or feed.get("pubDate") or ""
            )
            published = parse_date(raw_date)

            results.append({
                "title": title,
                "summary": summary,
                "published": published,
                "time": published.strftime("%H:%M") if published else "",
                "source": "新浪财经",
                "url": feed.get("url") or feed.get("link") or "",
            })

        print(f"✅ 新浪财经：成功解析 {len(results)} 条")
        return results

    except Exception as exc:
        print(f"⚠️ 新浪财经抓取失败：{type(exc).__name__}: {exc}")
        return []


def fetch_china_news():
    # 首选新浪财经直播接口
    items = fetch_sina_china()
    if items:
        return items

    # 新浪失败时，依次尝试国内财经 RSS。只收录确实抓到的新闻。
    print("ℹ️ 新浪财经没有返回可用新闻，开始尝试国内财经 RSS 备用源。")
    fallback_items = collect_rss(CHINA_RSS_FEEDS)
    if fallback_items:
        return fallback_items

    print("⚠️ 国内财经频道没有抓到可用新闻；不会生成虚构新闻。")
    return []


# ========================= 重要性评分、去重和编号 =========================

IMPORTANCE_RULES = [
    (10, [
        "美联储决议", "央行决议", "利率决议", "紧急降息", "紧急加息",
        "金融危机", "market crash", "fed decision", "rate decision",
        "bank failure",
    ]),
    (9, [
        "通胀", "cpi", "inflation", "gdp", "国内生产总值",
        "非农就业", "nonfarm payrolls", "核心pce", "就业报告",
    ]),
    (8, [
        "关税", "制裁", "贸易协议", "出口禁令", "tariff",
        "sanctions", "export ban", "财政预算", "资本管制",
    ]),
    (7, [
        "股市", "半导体", "芯片", "油价", "债券收益率",
        "stock market", "semiconductor", "oil prices", "bond yields",
        "汇率大幅",
    ]),
    (6, [
        "财报", "业绩预警", "净利润", "earnings",
        "quarterly results", "profit warning",
    ]),
]


def importance_score(item):
    text = (clean_text(item.get("title", "")) + " " +
            clean_text(item.get("summary", ""))).lower()
    for score, words in IMPORTANCE_RULES:
        if any(word.lower() in text for word in words):
            return score
    return 3


def rank_news(items, limit, channel_tag, default_asset):
    deduped = []
    seen = set()

    for raw_item in items:
        item = dict(raw_item)
        title = clean_text(item.get("title", ""))
        if not title:
            continue

        key = normalize_title(title)
        if not key or key in seen:
            continue
        seen.add(key)

        published = item.get("published")
        if isinstance(published, str):
            published = parse_date(published)

        item["title"] = title
        item["summary"] = clean_text(item.get("summary", ""))
        item["published"] = published
        item["time"] = (
            published.strftime("%H:%M")
            if isinstance(published, datetime.datetime)
            else clean_text(item.get("time", ""))
        )
        item["tag"] = channel_tag
        item["source"] = clean_text(item.get("source", "")) or "来源未标注"
        item["url"] = clean_text(item.get("url", ""))
        item["impact"] = "需结合原文、官方数据及实时行情进一步判断影响。"
        item["asset"] = default_asset
        item["_score"] = importance_score(item)
        deduped.append(item)

    # 先按重要性，再按发布时间从新到旧；无时间的排在有时间的后面。
    min_date = datetime.datetime.min.replace(tzinfo=BEIJING_TZ)
    deduped.sort(
        key=lambda item: (
            item["_score"],
            item.get("published") is not None,
            item.get("published") or min_date,
        ),
        reverse=True,
    )

    selected = deduped[:limit]
    total = len(selected)
    output = []

    for idx, item in enumerate(selected):
        item.pop("_score", None)
        item["id"] = f"NO.{total - idx:02d}"
        # 给前端字段提供默认值，避免 undefined。
        item.setdefault("summary", "")
        item.setdefault("time", "")
        item.setdefault("impact", "")
        item.setdefault("asset", default_asset)
        output.append(item)

    return output


# ========================= 安全写入 HTML =========================

def js_safe_json(value):
    """JSON 可直接放进 script 标签；转义 < 防止意外结束 script。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def main():
    current = now_beijing()
    datetime_str = current.strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 58)
    print("开始抓取财经新闻")
    print(f"北京时间：{datetime_str}")
    print("=" * 58)

    raw_global = collect_rss(RSS_FEEDS["global"])
    raw_asia = collect_rss(RSS_FEEDS["asia"])
    raw_china = fetch_china_news()

    final_global = rank_news(
        raw_global, GLOBAL_LIMIT, "全球宏观", "全球股指 / 美元 / 美债 / 大宗商品"
    )
    final_asia = rank_news(
        raw_asia, ASIA_LIMIT, "亚太市场", "亚洲股指 / 港股 / 日股 / 汇率"
    )
    final_china = rank_news(
        raw_china, CHINA_LIMIT, "国内核心", "A股 / 人民币 / 国债 / 相关行业"
    )

    print("-" * 58)
    print(f"频道 全球宏观：实际展示 {len(final_global)} 条")
    print(f"频道 亚太市场：实际展示 {len(final_asia)} 条")
    print(f"频道 国内核心：实际展示 {len(final_china)} 条")
    print("-" * 58)

    if not HTML_FILE.exists():
        raise FileNotFoundError(
            f"找不到 {HTML_FILE}。请确认 update.py 与 index.html 位于同一目录。"
        )

    content = HTML_FILE.read_text(encoding="utf-8")
    pattern = r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->"

    news_data = {
        "global": final_global,
        "asia": final_asia,
        "china": final_china,
    }

    replacement = (
        "<!-- NEWS_DATA_START -->\n"
        "<script>\n"
        f"const newsData = {js_safe_json(news_data)};\n"
        "</script>\n"
        "<!-- NEWS_DATA_END -->"
    )

    updated, count = re.subn(pattern, lambda _m: replacement, content, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(
            "没有找到 NEWS_DATA_START / NEWS_DATA_END 标记。"
            "请确认 index.html 中保留了这两个注释标记。"
        )

    time_pattern = r'<div id="updateTime"[^>]*>.*?</div>'
    time_replacement = (
        '<div id="updateTime" class="text-slate-400 mt-1 sm:mt-0">'
        f"更新时间: {datetime_str} (北京时间)</div>"
    )
    updated, time_count = re.subn(
        time_pattern, lambda _m: time_replacement, updated, count=1, flags=re.DOTALL
    )
    if time_count != 1:
        print("⚠️ 没找到 updateTime 元素，新闻数据已写入，但更新时间未替换。")

    HTML_FILE.write_text(updated, encoding="utf-8")

    print("✅ 财经新闻更新完成")
    print(f"更新时间：{datetime_str}")
    print(f"文件位置：{HTML_FILE}")
    print(f"全球宏观：{len(final_global)} 条")
    print(f"亚太市场：{len(final_asia)} 条")
    print(f"国内核心：{len(final_china)} 条")
    print("=" * 58)


if __name__ == "__main__":
    main()
