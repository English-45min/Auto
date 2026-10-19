#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财经与时政焦点看板自动更新脚本（防串台加固版）
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


BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"

GLOBAL_LIMIT = 20
ASIA_LIMIT = 10
CHINA_LIMIT = 10

RSS_FEEDS = {
    "global": [
        ("CNBC World", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("BBC World News", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ],
    "asia": [
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

CHINA_RSS_FEEDS = [
    ("新浪财经焦点", "https://rss.sina.com.cn/roll/finance/hot_roll.xml"),
    ("华尔街见闻", "https://dedicated.wallstreetcn.com/rss.xml"),
    ("凤凰财经", "https://finance.ifeng.com/rss/index.xml"),
    ("证券时报", "https://www.stcn.com/rss/rss.xml"),
]


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
            return datetime.datetime.fromtimestamp(stamp, tz=datetime.timezone.utc).astimezone(BEIJING_TZ)
        except Exception:
            return None
    value = str(value).strip()
    if not value:
        return None
    try:
        result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=BEIJING_TZ)
        return result.astimezone(BEIJING_TZ)
    except Exception:
        pass
    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=datetime.timezone.utc)
        return result.astimezone(BEIJING_TZ)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
    return None


def http_get(url, timeout=12):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def translate_to_chinese(text):
    text = clean_text(text)
    if not text:
        return text
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    if chinese_chars >= 8 and chinese_chars >= latin_chars:
        return text

    def translate_chunk(chunk):
        try:
            params = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": chunk})
            raw = http_get("https://translate.googleapis.com/translate_a/single?" + params, timeout=18)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            result = "".join(part[0] for part in (payload[0] or []) if isinstance(part, list) and part and isinstance(part[0], str)).strip()
            if result:
                return result
        except Exception:
            pass
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
    results = [translate_chunk(chunk) for chunk in chunks]
    return " ".join(results).strip()


def translate_international_channels(items, channel_name):
    for item in items:
        old_title = clean_text(item.get("title", ""))
        old_summary = clean_text(item.get("summary", ""))
        if old_title:
            item["title"] = translate_to_chinese(old_title)
        if old_summary:
            item["summary"] = translate_to_chinese(old_summary)
    return items


def parse_rss_xml(xml_bytes, source_name):
    items = []
    try:
        try:
            xml_str = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            xml_str = xml_bytes.decode("gbk", errors="replace")
        root = ET.fromstring(xml_str)
    except Exception:
        return items

    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        values = {}
        for child in list(node):
            key = child.tag.rsplit("}", 1)[-1].lower()
            if key == "link":
                values[key] = child.attrib.get("href") or child.text or ""
            elif key not in values:
                values[key] = child.text or ""
        title = clean_text(values.get("title", ""))
        summary = clean_text(values.get("description") or values.get("summary") or values.get("content") or "")
        link = values.get("link", "").strip()
        published = parse_date(values.get("pubdate") or values.get("published") or values.get("updated") or values.get("date") or "")
        if title:
            items.append({"title": title, "summary": summary, "published": published, "time": published.strftime("%H:%M") if published else "", "source": source_name, "url": link})
    return items


def fetch_feed(source_name, url):
    try:
        data = http_get(url, timeout=12)
        items = parse_rss_xml(data, source_name)
        if items:
            return items
    except Exception:
        pass
    
    # 备用 rss2json
    try:
        gateway = "https://api.rss2json.com/v1/api.json?" + urllib.parse.urlencode({"rss_url": url})
        data = json.loads(http_get(gateway, timeout=12).decode("utf-8", errors="replace"))
        if data.get("status") == "ok":
            results = []
            for item in data.get("items", []):
                title = clean_text(item.get("title", ""))
                summary = clean_text(item.get("description", "") or item.get("content", ""))
                published = parse_date(item.get("pubDate") or item.get("published") or "")
                if title:
                    results.append({"title": title, "summary": summary, "published": published, "time": published.strftime("%H:%M") if published else "", "source": source_name, "url": item.get("link", "")})
            return results
    except Exception:
        pass
    return []


def collect_rss(feed_defs):
    all_items = []
    for source_name, url in feed_defs:
        all_items.extend(fetch_feed(source_name, url))
        time.sleep(0.15)
    return all_items


def filter_asia_news(items):
    ASIA_KEYWORDS = (
        "asia", "asian", "singapore", "japan", "tokyo", "korea", "seoul", "hong kong",
        "china", "taiwan", "indonesia", "malaysia", "thailand", "vietnam", "australia",
        "亚太", "亚洲", "新加坡", "日本", "东京", "韩国", "首尔", "香港", "台湾", "东南亚", "印尼", "泰国", "越南", "澳大利亚", "日经", "港股"
    )
    kept = []
    for item in items:
        text = (clean_text(item.get("title", "")) + " " + clean_text(item.get("summary", ""))).lower()
        if any(w in text for w in ASIA_KEYWORDS):
            kept.append(item)
    return kept


# ========================= 国内新闻严格纯净化过滤 =========================

CHINA_RELEVANCE_KEYWORDS = (
    "中国", "国内", "央行", "人民币", "a股", "沪指", "上证", "深证",
    "创业板", "科创板", "北向资金", "南向资金", "港股", "沪深", "证券",
    "国家统计局", "财政部", "商务部", "发改委", "工信部", "国务院", "证监会",
    "金融监管总局", "社融", "lpr", "gdp", "cpi", "ppi", "房地产", "楼市",
    "地方债", "新能源", "海关", "A股市场", "深交所", "上交所"
)

# 强力排除海外纯地缘政治、外国大选、美联储纯内部决议等干扰词
CHINA_EXCLUDE_KEYWORDS = (
    "美联储主席", "美国大选", "白宫", "五角大楼", "乌克兰", "俄罗斯", "莫斯科",
    "泽连斯基", "加沙", "哈马斯", "巴勒斯坦", "黎巴嫩", "叙利亚", "伊朗",
    "法国", "德国", "英国首相", "欧洲央行行长拉加德"
)

def find_candidate_lists(obj):
    candidates = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in {"feed", "feeds", "items", "list"} and isinstance(value, list):
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
            content = clean_text(feed.get("rich_text") or feed.get("content") or feed.get("title") or "")
            if len(content) < 8:
                continue
            match = re.match(r"^【([^】]+)】\s*(.*)$", content)
            if match:
                title, summary = match.group(1).strip(), match.group(2).strip() or content
            else:
                title, summary = content[:100].strip(), content
            key = normalize_title(title)
            if not key or key in seen:
                continue
            seen.add(key)
            published = parse_date(feed.get("create_time") or feed.get("ctime") or feed.get("time") or "")
            results.append({"title": title, "summary": summary, "published": published, "time": published.strftime("%H:%M") if published else "", "source": "新浪财经", "url": feed.get("url") or ""})
        return results
    except Exception:
        return []


def is_china_relevant(item):
    text = clean_text(item.get("title", "")) + " " + clean_text(item.get("summary", ""))
    low = text.lower()
    # 命中排除词直接干掉
    if any(ex.lower() in low for ex in CHINA_EXCLUDE_KEYWORDS):
        return False
    # 必须包含本土核心财经词汇
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


def importance_score(item):
    text = (clean_text(item.get("title", "")) + " " + clean_text(item.get("summary", ""))).lower()
    rules = [
        (10, ["央行", "降息", "加息", "lpr", "gdp", "cpi", "证监会", "国务院"]),
        (9, ["社融", "财政部", "商务部", "发改委", "A股", "沪指", "深证"]),
        (8, ["房地产", "地方债", "新能源", "港股", "人民币"]),
        (7, ["半导体", "芯片", "油价", "财报"]),
    ]
    for score, words in rules:
        if any(w.lower() in text for w in words):
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
        published = parse_date(item.get("published"))
        item["title"] = title
        item["summary"] = clean_text(item.get("summary", ""))
        item["published"] = published
        item["time"] = published.strftime("%H:%M") if isinstance(published, datetime.datetime) else clean_text(item.get("time", ""))
        item["tag"] = channel_tag
        item["source"] = clean_text(item.get("source", "")) or "公开源"
        item["url"] = clean_text(item.get("url", ""))
        item["impact"] = "需结合原文、官方数据及实时行情进一步判断影响。"
        item["asset"] = default_asset
        item["_score"] = importance_score(item)
        deduped.append(item)

    min_date = datetime.datetime.min.replace(tzinfo=BEIJING_TZ)
    deduped.sort(key=lambda x: (x["_score"], x.get("published") is not None, x.get("published") or min_date), reverse=True)
    selected = deduped[:limit]
    total = len(selected)
    output = []
    for idx, item in enumerate(selected):
        item.pop("_score", None)
        item["id"] = f"NO.{total - idx:02d}"
        output.append(item)
    return output


def js_safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).replace("</", "<\\/").replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def main():
    current = now_beijing()
    datetime_str = current.strftime("%Y-%m-%d %H:%M:%S")

    raw_global = collect_rss(RSS_FEEDS["global"])
    raw_asia = filter_asia_news(collect_rss(RSS_FEEDS["asia"]))
    raw_china = fetch_china_news()

    final_global = rank_news(raw_global, GLOBAL_LIMIT, "全球宏观", "全球股指 / 美元 / 美债 / 大宗商品")
    final_asia = rank_news(raw_asia, ASIA_LIMIT, "亚太市场", "亚洲股指 / 港股 / 日股 / 汇率")
    final_china = rank_news(raw_china, CHINA_LIMIT, "国内核心", "A股 / 人民币 / 国债 / 相关行业")

    final_global = translate_international_channels(final_global, "国际财经与政经")
    final_asia = translate_international_channels(final_asia, "亚太财经与政经")

    if not HTML_FILE.exists():
        raise FileNotFoundError(f"找不到 {HTML_FILE}")

    content = HTML_FILE.read_text(encoding="utf-8")
    news_data = {"global": final_global, "asia": final_asia, "china": final_china}

    pattern = r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->"
    replacement = f"<!-- NEWS_DATA_START -->\n<script>\nconst newsData = {js_safe_json(news_data)};\n</script>\n<!-- NEWS_DATA_END -->"

    updated, count = re.subn(pattern, lambda _m: replacement, content, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError("没有找到 NEWS_DATA_START / NEWS_DATA_END 标记")

    time_pattern = r'<div id="updateTime"[^>]*>.*?</div>'
    time_replacement = f'<div id="updateTime" class="text-slate-300 font-medium text-sm">更新时间: {datetime_str} (北京时间)</div>'
    updated, _ = re.subn(time_pattern, lambda _m: time_replacement, updated, count=1, flags=re.DOTALL)

    HTML_FILE.write_text(updated, encoding="utf-8")
    print(f"✅ 看板更新成功：全球宏观 {len(final_global)} 条 | 亚太市场 {len(final_asia)} 条 | 国内核心 {len(final_china)} 条")


if __name__ == "__main__":
    main()
