#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财经与时政焦点看板自动更新脚本（全功能终极完整版）
功能：
1. 抓取国际财经/时政（20条）、亚太财经/时政（10条）、国内核心政经（10条）；
2. 严格地域隔离与双重过滤，确保国内板块100%纯正本土宏观政经（杜绝串台）；
3. 自动将国际与亚太新闻高质量翻译为中文；
4. 按重要性与时政权重排序，安全内嵌写入 index.html。
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

# 频道容量配额设置
GLOBAL_LIMIT = 20
ASIA_LIMIT = 10
CHINA_LIMIT = 10

RSS_FEEDS = {
    # 国际财经与重磅时政源
    "global": [
        ("CNBC World", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("BBC World News", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ],
    # 亚太财经与时政源
        "asia": [
        ("Focus Taiwan", "https://focustaiwan.tw/rss/news.xml"),
        ("Taipei Times", "https://www.taipeitimes.com/xml/index.rss"),
        ("CNA Business", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),
        ("CNA Asia", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511"),
        ("CNA World", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6311"),
        ("CNBC Asia", "https://www.cnbc.com/id/19832390/device/rss/rss.html"),
        ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
    ],
}

SINA_URL = (
    "https://zhibo.sina.com.cn/api/zhibo/feed"
    "?page=1&page_size=50&zhibo_id=152"
)

# 国内频道备用权威政经 RSS 源
CHINA_RSS_FEEDS = [
    ("新浪财经焦点", "https://rss.sina.com.cn/roll/finance/hot_roll.xml"),
    ("华尔街见闻", "https://dedicated.wallstreetcn.com/rss.xml"),
    ("凤凰财经", "https://finance.ifeng.com/rss/index.xml"),
    ("证券时报", "https://www.stcn.com/rss/rss.xml"),
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


# ========================= 国际/亚太新闻中文翻译 =========================

def translate_to_chinese(text):
    text = clean_text(text)
    if not text:
        return text
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    if chinese_chars >= 8 and chinese_chars >= latin_chars:
        return text

    def translate_chunk(chunk):
        errors = []
        try:
            params = urllib.parse.urlencode({"client":"gtx", "sl":"auto", "tl":"zh-CN", "dt":"t", "q":chunk})
            raw = http_get("https://translate.googleapis.com/translate_a/single?" + params, timeout=18)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            result = "".join(part[0] for part in (payload[0] or []) if isinstance(part, list) and part and isinstance(part[0], str)).strip()
            if result:
                return result
            errors.append("Google 返回空结果")
        except Exception as exc:
            errors.append(f"Google {type(exc).__name__}: {exc}")
        try:
            params = urllib.parse.urlencode({"q":chunk, "langpair":"en|zh-CN"})
            raw = http_get("https://api.mymemory.translated.net/get?" + params, timeout=18)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            result = clean_text((payload.get("responseData") or {}).get("translatedText", ""))
            if result:
                return html.unescape(result)
            errors.append("备用接口返回空结果")
        except Exception as exc:
            errors.append(f"备用接口 {type(exc).__name__}: {exc}")
        return chunk

    chunks = []
    remaining = text
    while len(remaining) > 300:
        cut = max(remaining.rfind("。", 0, 300), remaining.rfind(".", 0, 300), remaining.rfind(" ", 0, 300))
        if cut < 80:
            cut = 300
        chunks.append(remaining[:cut+1].strip())
        remaining = remaining[cut+1:].strip()
    if remaining:
        chunks.append(remaining)
    results = []
    for chunk in chunks:
        results.append(translate_chunk(chunk))
        time.sleep(0.15)
    return " ".join(results).strip()


def translate_international_channels(items, channel_name):
    title_count = 0
    for item in items:
        old_title = clean_text(item.get("title", ""))
        old_summary = clean_text(item.get("summary", ""))
        if old_title:
            item["title"] = translate_to_chinese(old_title)
            if item["title"] != old_title:
                title_count += 1
        if old_summary:
            item["summary"] = translate_to_chinese(old_summary)
    print(f"🌐 {channel_name}：标题转为中文 {title_count}/{len(items)} 条")
    return items


# ========================= RSS 抓取逻辑 =========================

def parse_rss_xml(xml_bytes, source_name):
    items = []
    try:
        try:
            xml_str = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            xml_str = xml_bytes.decode("gbk", errors="replace")
            
        root = ET.fromstring(xml_str)
    except ET.ParseError as exc:
        print(f"⚠️ RSS XML 解析失败 [{source_name}]: {exc}")
        return items

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
        return items
    except Exception as exc:
        return []


def fetch_rss2json(source_name, rss_url):
    api_key = os.environ.get("RSS2JSON_API_KEY", "").strip()
    params = {"rss_url": rss_url}
    if api_key:
        params["api_key"] = api_key
    gateway = "https://api.rss2json.com/v1/api.json?" + urllib.parse.urlencode(params)

    try:
        data = json.loads(http_get(gateway, timeout=12).decode("utf-8", errors="replace"))
        if data.get("status") != "ok":
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
        return results
    except Exception as exc:
        return []


def fetch_feed(source_name, url):
    items = fetch_rss_direct(source_name, url)
    if items:
        return items
    return fetch_rss2json(source_name, url)


def collect_rss(feed_defs):
    all_items = []
    for source_name, url in feed_defs:
        all_items.extend(fetch_feed(source_name, url))
        time.sleep(0.15)
    return all_items


# ========================= 亚太频道地域过滤 =========================

ASIA_KEYWORDS = (
    "asia", "asian", "singapore", "japan", "japanese", "tokyo", "korea", "korean",
    "seoul", "hong kong", "china", "chinese", "taiwan", "indonesia", "malaysia",
    "thailand", "vietnam", "philippines", "australia", "nikkei", "kospi", "hang seng",
    "asx", "sgx", "亚太", "亚洲", "新加坡", "日本", "东京", "韩国", "首尔",
    "香港", "台湾", "东南亚", "印尼", "泰国", "越南", "澳大利亚", "日经", "港股"
)

def filter_asia_news(items):
    kept = []
    for item in items:
        text = (clean_text(item.get("title", "")) + " " + clean_text(item.get("summary", ""))).lower()
        if any(word in text for word in ASIA_KEYWORDS):
            kept.append(item)
    return kept


# ========================= 国内新闻严格防串台过滤 =========================

CHINA_RELEVANCE_KEYWORDS = (
    "中国", "国内", "央行", "人民币", "a股", "沪指", "上证", "深证",
    "创业板", "科创板", "北向资金", "南向资金", "港股", "沪深", "证券",
    "国家统计局", "财政部", "商务部", "发改委", "工信部", "国务院", "证监会",
    "金融监管总局", "社融", "lpr", "gdp", "cpi", "ppi", "房地产", "楼市",
    "地方债", "新能源", "海关", "A股市场", "深交所", "上交所"
)

CHINA_EXCLUDE_KEYWORDS = (
    "美联储主席", "美国大选", "白宫", "五角大楼", "乌克兰", "俄罗斯", "莫斯科",
    "泽连斯基", "加沙", "哈马斯", "巴勒斯坦", "黎巴嫩", "叙利亚", "伊朗",
    "法国", "德国", "英国首相", "欧洲央行行长拉加德"
)

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

        return results

    except Exception as exc:
        return []


def is_china_relevant(item):
    text = clean_text(item.get("title", "")) + " " + clean_text(item.get("summary", ""))
    low = text.lower()
    if any(ex.lower() in low for ex in CHINA_EXCLUDE_KEYWORDS):
        return False
    return any(word.lower() in low for word in CHINA_RELEVANCE_KEYWORDS)


def fetch_china_news():
    items = [it for it in fetch_sina_china() if is_china_relevant(it)]
    if len(items) < CHINA_LIMIT:
        rss_items = [it for it in collect_rss(CHINA_RSS_FEEDS) if is_china_relevant(it)]
        
        seen = {normalize_title(it["title"]) for it in items}
        for rss_item in rss_items:
            key = normalize_title(rss_item["title"])
            if key not in seen:
                seen.add(key)
                items.append(rss_item)
            if len(items) >= CHINA_LIMIT:
                break

    return items


# ========================= 重要性评分与排序 =========================

IMPORTANCE_RULES = [
    (10, [
        "美联储", "央行决议", "利率决议", "降息", "加息", "fed decision", "rate decision",
        "外交", "峰会", "总统", "总理", "主席", "会谈", "白宫", "国情咨文", "summit", "president", "pm", "央行", "国务院"
    ]),
    (9, [
        "通胀", "cpi", "inflation", "gdp", "非农", "核心pce", "制裁", "关税",
        "tariff", "sanctions", "election", "大选", "地缘政治", "社融", "财政部", "商务部"
    ]),
    (8, [
        "贸易协议", "政策支持", "产业政策", "un", "联合国", "opec", "欧佩克", "imf", "房地产", "地方债"
    ]),
    (7, [
        "股市", "半导体", "芯片", "油价", "债券收益率", "stock market", "semiconductor", "oil", "港股"
    ]),
    (6, [
        "财报", "业绩", "净利润", "earnings"
    ]),
]


def importance_score(item):
    text = (clean_text(item.get("title", "")) + " " + clean_text(item.get("summary", ""))).lower()
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
        item.setdefault("summary", "")
        item.setdefault("time", "")
        item.setdefault("impact", "")
        item.setdefault("asset", default_asset)
        output.append(item)

    return output


def js_safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).replace(
        "</", "<\\/"
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def main():
    current = now_beijing()
    datetime_str = current.strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 58)
    print("开始抓取财经与时政焦点新闻")
    print(f"北京时间：{datetime_str}")
    print("=" * 58)

    raw_global = collect_rss(RSS_FEEDS["global"])
    raw_asia = filter_asia_news(collect_rss(RSS_FEEDS["asia"]))
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

    # 国际与亚太新闻进行中文翻译
    final_global = translate_international_channels(final_global, "国际财经与政经")
    final_asia = translate_international_channels(final_asia, "亚太财经与政经")

    print("-" * 58)
    print(f"频道 全球宏观：实际展示 {len(final_global)} 条")
    print(f"频道 亚太市场：实际展示 {len(final_asia)} 条")
    print(f"频道 国内核心：实际展示 {len(final_china)} 条")
    print("-" * 58)

    if not HTML_FILE.exists():
        raise FileNotFoundError(f"找不到 {HTML_FILE}")

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
        raise RuntimeError("没有找到 NEWS_DATA_START / NEWS_DATA_END 标记")

    time_pattern = r'<div id="updateTime"[^>]*>.*?</div>'
    time_replacement = (
        '<div id="updateTime" class="text-slate-300 font-medium text-sm">'
        f"更新时间: {datetime_str} (北京时间)</div>"
    )
    updated, _ = re.subn(time_pattern, lambda _m: time_replacement, updated, count=1, flags=re.DOTALL)

    HTML_FILE.write_text(updated, encoding="utf-8")

    print("✅ 财经/时政焦点新闻看板更新完成")
    print(f"更新时间：{datetime_str}")
    print("=" * 58)


if __name__ == "__main__":
    main()
